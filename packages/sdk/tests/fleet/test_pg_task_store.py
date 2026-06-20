# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PostgresTaskStore parity tests — SQLite always, real Postgres when configured.

ALL imports live at the top (Tasks 5/6 append tests that reuse them — never add
mid-file imports, which trip Ruff I001).
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text, update

from sagewai.db.engine import create_engine
from sagewai.fleet.dispatcher import NotTaskOwnerError
from sagewai.fleet.task_store import PostgresTaskStore

# Run every test on SQLite always, and on real Postgres when SAGEWAI_TEST_DATABASE_URL
# is set — so the PG-only claim branch (FOR UPDATE SKIP LOCKED + JSONB `<@` containment)
# is genuinely exercised, not just the SQLite CAS scan. Mirrors tests/db/conftest.py.
_PG_URL = os.environ.get("SAGEWAI_TEST_DATABASE_URL")
_DIALECTS = ["sqlite"] + (["postgres"] if _PG_URL else [])


@pytest_asyncio.fixture(params=_DIALECTS)
async def store(request, tmp_path):
    from sagewai.db.models import Base

    if request.param == "sqlite":
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path/'tasks.db'}")
    else:
        engine = create_engine(_PG_URL)
        async with engine.begin() as conn:  # disposable test DB (cf. tests/db/conftest.py)
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    s = PostgresTaskStore(engine=engine)
    await s.init()  # sqlite: create_all; postgres: fail-closed probe (table now exists)
    assert s._engine.dialect.name == ("postgresql" if request.param == "postgres" else "sqlite")
    yield s, engine
    if request.param == "postgres":
        # Drop on teardown so this shared Postgres DB is left clean — otherwise the
        # leftover create_all tables (no alembic_version) collide with the alembic
        # migration round-trip test if it runs later in the same session.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def _task(run_id="r1", org="o", project=None, pool="default", model=None, labels=None, payload=None):
    return {"run_id": run_id, "org_id": org, "project_id": project, "pool": pool,
            "model": model, "labels": labels or {}, "payload": payload or {"k": "v"}}


@pytest.mark.asyncio
async def test_enqueue_persists_across_instances(store):
    s, engine = store
    await s.enqueue(_task(payload={"hello": 1}))
    s2 = PostgresTaskStore(engine=engine)  # fresh instance = "restart"
    got = await s2.get_task("r1", org_id="o", project_id=None)
    assert got is not None and got["status"] == "pending"


@pytest.mark.asyncio
async def test_claim_filters_and_returns_payload(store):
    s, _ = store
    await s.enqueue(_task(run_id="r1", model="gpt-4o", payload={"hello": 1}))
    # wrong project / wrong pool / wrong model don't match
    assert await s.claim_task("w", "o", ["gpt-4o"], "default", {}, project_id="pa") is None
    assert await s.claim_task("w", "o", ["gpt-4o"], "gpu", {}, project_id=None) is None
    assert await s.claim_task("w", "o", ["other"], "default", {}, project_id=None) is None
    # wrong org doesn't match (exact org)
    assert await s.claim_task("w", "other", ["gpt-4o"], "default", {}, project_id=None) is None
    # right capabilities -> claimed, payload returned
    t = await s.claim_task("w", "o", ["gpt-4o"], "default", {}, project_id=None)
    assert t and t["run_id"] == "r1" and t["payload"] == {"hello": 1} and t["worker_id"] == "w"


@pytest.mark.asyncio
async def test_claim_no_label_starvation(store):
    s, _ = store
    # labeled task is OLDER (enqueued first); unlabeled task is newer.
    await s.enqueue(_task(run_id="labeled", labels={"gpu": "true"}))
    await s.enqueue(_task(run_id="plain", labels={}))
    # a worker with NO labels must still reach the plain task past the labeled prefix
    t = await s.claim_task("w", "o", [], "default", {}, project_id=None)
    assert t and t["run_id"] == "plain"


@pytest.mark.asyncio
async def test_concurrent_claimers_single_winner(store):
    s, engine = store
    await s.enqueue(_task(run_id="r1"))
    s2 = PostgresTaskStore(engine=engine)
    # TRULY concurrent (gather) so two transactions race: on Postgres FOR UPDATE
    # SKIP LOCKED keeps them off the same row; on SQLite writes serialize. The
    # status='pending' CAS is the backstop. Exactly one must win.
    a, b = await asyncio.gather(
        s.claim_task("wa", "o", [], "default", {}, project_id=None),
        s2.claim_task("wb", "o", [], "default", {}, project_id=None),
    )
    assert {bool(a), bool(b)} == {True, False}


@pytest.mark.asyncio
async def test_report_validates_status(store):
    s, _ = store
    await s.enqueue(_task(run_id="r1"))
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    with pytest.raises(ValueError):
        await s.report_task("r1", "pending", None, None, worker_id="w")  # non-terminal rejected
    # the row is untouched (still claimed)
    assert (await s.get_task("r1", org_id="o", project_id=None))["status"] == "claimed"


@pytest.mark.asyncio
async def test_report_ownership_and_idempotency(store):
    s, _ = store
    await s.enqueue(_task(run_id="r1"))
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    # foreign worker -> NotTaskOwnerError
    with pytest.raises(NotTaskOwnerError):
        await s.report_task("r1", "completed", "x", None, worker_id="other")
    await s.report_task("r1", "completed", "out", None, worker_id="w")
    assert (await s.get_task("r1", org_id="o", project_id=None))["status"] == "completed"
    # idempotent lost-ack: same worker + same status -> no error
    await s.report_task("r1", "completed", "out", None, worker_id="w")
    # different terminal status on a terminal row -> NotTaskOwnerError
    with pytest.raises(NotTaskOwnerError):
        await s.report_task("r1", "failed", None, "boom", worker_id="w")
    # unknown run -> NotTaskOwnerError
    with pytest.raises(NotTaskOwnerError):
        await s.report_task("nope", "completed", None, None, worker_id="w")


@pytest.mark.asyncio
async def test_list_tasks_scope_and_status_filter(store):
    s, _ = store
    await s.enqueue(_task(run_id="r1"))
    await s.enqueue(_task(run_id="r2"))
    await s.claim_task("w", "o", [], "default", {}, project_id=None)  # claims r1 (FIFO)
    pending = await s.list_tasks(org_id="o", project_id=None, status="pending")
    assert [t["run_id"] for t in pending] == ["r2"]
    assert await s.list_tasks(org_id="other", project_id=None) == []  # scope isolation


@pytest.mark.asyncio
async def test_claim_sets_lease_and_attempts(store):
    s, engine = store
    await s.enqueue(_task(run_id="r1"))
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT attempts, lease_expires_at FROM fleet_tasks WHERE run_id='r1'"
        ))).first()
    assert row[0] == 1 and row[1] is not None  # attempts incremented, lease set


@pytest.mark.asyncio
async def test_report_clears_lease(store):
    s, engine = store
    await s.enqueue(_task(run_id="r1"))
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    await s.report_task("r1", "completed", "out", None, worker_id="w")
    async with engine.connect() as conn:
        lease = (await conn.execute(text(
            "SELECT lease_expires_at FROM fleet_tasks WHERE run_id='r1'"
        ))).scalar_one()
    assert lease is None  # IS NOT NULL <=> claimed


@pytest.mark.asyncio
async def test_init_upgrades_019_shaped_sqlite(tmp_path):
    """init() upgrades an older local SQLite fleet_tasks in place (adds the lease
    columns) instead of fail-closing, so a pre-020 home boots transparently. create_all
    won't ALTER an existing table, so the upgrade pass does it. (Postgres still
    fail-closes — Alembic owns its upgrades; see the integration test below.)"""
    eng = create_engine(f"sqlite+aiosqlite:///{tmp_path/'old.db'}")
    async with eng.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE fleet_tasks (run_id TEXT PRIMARY KEY, org_id TEXT NOT NULL, "
            "project_id TEXT, pool TEXT NOT NULL DEFAULT 'default', model TEXT, "
            "labels TEXT NOT NULL DEFAULT '{}', payload TEXT NOT NULL DEFAULT '{}', "
            "status TEXT NOT NULL DEFAULT 'pending', worker_id TEXT, claimed_at TIMESTAMP, "
            "output TEXT, error TEXT, reported_at TIMESTAMP, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
    await PostgresTaskStore(engine=eng).init()  # must NOT raise — upgrades in place
    async with eng.connect() as conn:
        res = await conn.exec_driver_sql("PRAGMA table_info(fleet_tasks)")
        cols = {r[1] for r in res}
    assert "lease_expires_at" in cols and "attempts" in cols
    await eng.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_init_fail_closed_on_unmigrated_pg():
    """Real fail-closed: against a Postgres fleet_tasks missing the B2 columns,
    init() raises (skipped without Postgres)."""
    import os

    url = os.environ.get("SAGEWAI_TEST_DATABASE_URL")
    if not url:
        pytest.skip("no Postgres")
    from sagewai.db.models import Base

    eng = create_engine(url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text(
            "CREATE TABLE fleet_tasks (run_id TEXT PRIMARY KEY, org_id TEXT NOT NULL, status TEXT)"
        ))
    try:
        with pytest.raises(Exception):
            await PostgresTaskStore(engine=eng).init()
    finally:
        async with eng.begin() as conn:
            await conn.execute(text("DROP TABLE fleet_tasks"))
        await eng.dispose()


async def _force_lease_past(engine, run_id):
    """Write a past lease_expires_at directly — deterministic, no clock injection."""
    from sagewai.db.models import FleetTaskModel
    t = FleetTaskModel.__table__
    async with engine.begin() as conn:
        await conn.execute(update(t).where(t.c.run_id == run_id).values(
            lease_expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc)
        ))


@pytest.mark.asyncio
async def test_renew_extends_only_that_worker(store):
    s, engine = store
    await s.enqueue(_task(run_id="r1"))
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    await _force_lease_past(engine, "r1")
    assert await s.renew_worker_leases("w") == 1
    assert await s.renew_worker_leases("other") == 0
    # renewed back into the future -> reap finds nothing
    assert await s.reap_expired_leases() == {"failed": 0, "requeued": 0}


@pytest.mark.asyncio
async def test_reap_requeues_then_fails_at_cap(store):
    s, engine = store
    s._max_attempts = 2
    await s.enqueue(_task(run_id="r1"))
    await s.claim_task("w", "o", [], "default", {}, project_id=None)   # attempts=1
    # not expired yet -> untouched
    assert await s.reap_expired_leases() == {"failed": 0, "requeued": 0}
    await _force_lease_past(engine, "r1")
    assert await s.reap_expired_leases() == {"failed": 0, "requeued": 1}
    got = await s.get_task("r1", org_id="o", project_id=None)
    assert got["status"] == "pending" and got["worker_id"] is None
    # re-claim -> attempts=2 (== cap); expire -> failed, lease cleared
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    await _force_lease_past(engine, "r1")
    assert await s.reap_expired_leases() == {"failed": 1, "requeued": 0}
    done = await s.get_task("r1", org_id="o", project_id=None)
    assert done["status"] == "failed" and done["error"]


@pytest.mark.asyncio
async def test_reap_vs_late_report_race(store):
    s, engine = store
    await s.enqueue(_task(run_id="r1"))
    await s.claim_task("w", "o", [], "default", {}, project_id=None)
    await _force_lease_past(engine, "r1")
    await s.reap_expired_leases()  # requeued to pending, worker cleared
    with pytest.raises(NotTaskOwnerError):
        await s.report_task("r1", "completed", "x", None, worker_id="w")
