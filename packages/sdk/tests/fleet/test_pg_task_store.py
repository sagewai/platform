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

import pytest
import pytest_asyncio

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
