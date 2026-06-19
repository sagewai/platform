# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PostgresFleetRegistry on sqlite+aiosqlite — persistence + secret + project + status."""
from __future__ import annotations

import hashlib

import pytest

from sagewai.db.engine import create_engine
from sagewai.fleet.models import WorkerApprovalStatus, WorkerCapabilities
from sagewai.fleet.registry import PostgresFleetRegistry, _hash_key


@pytest.fixture
async def reg(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path/'fleet.db'}")
    r = PostgresFleetRegistry(engine=engine)
    await r.init()
    return r, engine


@pytest.mark.asyncio
async def test_register_persists_across_instances(reg):
    r, engine = reg
    w = await r.register_worker(
        name="w1", org_id="o", project_id="pa",
        capabilities=WorkerCapabilities(models_supported=["gpt-4o"]),
        secret_hash="deadbeef",
    )
    # a NEW registry instance on the same engine reads it back (persistence)
    r2 = PostgresFleetRegistry(engine=engine)
    got = await r2.get_worker(w.id)
    assert got is not None
    assert got.secret_hash == "deadbeef"           # secret survives "restart"
    assert got.project_id == "pa"                   # first-class project
    assert "gpt-4o" in got.capabilities.models_canonical


@pytest.mark.asyncio
async def test_status_sentinel_isolates_from_core(reg):
    r, engine = reg
    w = await r.register_worker(
        name="w", org_id="o",
        capabilities=WorkerCapabilities(models_supported=["gpt-4o"]),
        secret_hash="x",
    )
    from sqlalchemy import text
    async with engine.connect() as conn:
        status = (await conn.execute(
            text("SELECT status FROM workers WHERE worker_id=:w"), {"w": w.id}
        )).scalar_one()
    assert status == "fleet"  # core load balancer (status='active') never selects this


@pytest.mark.asyncio
async def test_core_worker_invisible_and_immutable_to_fleet_registry(reg):
    """Bidirectional isolation: a core workflow worker (status='active') sharing the
    `workers` table is never read or mutated by the fleet registry. Regression for
    the §2b gap where get/list/approve/heartbeat keyed on worker_id/org_id only."""
    from sqlalchemy import text

    from sagewai.db.models import WorkerModel

    r, engine = reg
    # A core-style row (status='active'); Core insert fills JSON/array defaults.
    async with engine.begin() as conn:
        await conn.execute(WorkerModel.__table__.insert().values(
            worker_id="core-w", org_id="o", status="active", approval_status="approved",
        ))
    # Reads never surface it.
    assert await r.get_worker("core-w") is None
    assert all(w.id != "core-w" for w in await r.list_workers("o"))
    # Mutations refuse it (get_worker returns None -> "not found").
    with pytest.raises(ValueError):
        await r.approve_worker("core-w", "admin")
    with pytest.raises(ValueError):
        await r.revoke_worker("core-w")
    # heartbeat/pool-stats are no-ops on a non-fleet row.
    await r.heartbeat("core-w", pool_stats={"warm": 1})
    assert await r.get_pool_stats("core-w") is None
    # The core row is byte-for-byte untouched.
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT status, approval_status FROM workers WHERE worker_id='core-w'"
        ))).first()
    assert row[0] == "active" and row[1] == "approved"


@pytest.mark.asyncio
async def test_approve_and_list_and_enrollment(reg):
    r, _ = reg
    w = await r.register_worker(
        name="w", org_id="o",
        capabilities=WorkerCapabilities(models_supported=["gpt-4o"]), secret_hash="x",
    )
    assert w.approval_status == WorkerApprovalStatus.PENDING
    appr = await r.approve_worker(w.id, "admin")
    assert appr.approval_status == WorkerApprovalStatus.APPROVED
    assert [x.id for x in await r.list_workers("o")] == [w.id]
    rec, raw = await r.create_enrollment_key(org_id="o", name="k", created_by="admin")
    assert (await r.validate_enrollment_key("o", raw)).id == rec.id
    assert (await r.find_enrollment_key_by_hash(_hash_key(raw))).id == rec.id
    await r.heartbeat(w.id, pool_stats={"warm": 1})
    assert (await r.get_pool_stats(w.id)) == {"warm": 1}


@pytest.mark.asyncio
async def test_lazy_self_init_without_explicit_init(tmp_path):
    """A method call builds the SQLite schema even when init() is never called —
    the registry must not depend on the app lifespan having run (Starlette 1.2
    bare TestClient doesn't enter the lifespan)."""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path/'lazy.db'}")
    r = PostgresFleetRegistry(engine=engine)  # NOTE: no await r.init()
    w = await r.register_worker(
        name="w", org_id="o",
        capabilities=WorkerCapabilities(models_supported=["gpt-4o"]), secret_hash="x",
    )
    assert (await r.get_worker(w.id)) is not None


@pytest.mark.asyncio
async def test_init_fail_closed_on_unreachable_db():
    """Fail-closed: init() against an unreachable non-SQLite DB raises at startup
    rather than deferring the failure to the first route call. Port 1 is closed."""
    engine = create_engine("postgresql+asyncpg://x:x@127.0.0.1:1/none")
    r = PostgresFleetRegistry(engine=engine)
    with pytest.raises(Exception):
        await r.init()
    await engine.dispose()


@pytest.mark.asyncio
async def test_revoke_unknown_enrollment_key_raises(reg):
    """Parity with InMemoryFleetRegistry: revoking an absent key is a ValueError."""
    r, _ = reg
    with pytest.raises(ValueError):
        await r.revoke_enrollment_key("nonexistent-id")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_enrollment_keys_on_postgres():
    """Enrollment-key create/validate/bump/revoke against REAL Postgres — catches
    the UUID-vs-Text id binding the SQLite tests can't (enrollment_keys.id is a
    native UUID column on Postgres). Skipped when Postgres is offline."""
    import os

    url = os.environ.get("SAGEWAI_TEST_DATABASE_URL")
    if not url:
        pytest.skip("no Postgres (SAGEWAI_TEST_DATABASE_URL unset)")
    from sagewai.db.models import Base

    engine = create_engine(url)
    async with engine.begin() as conn:  # the test DB is disposable (cf. tests/db/conftest.py)
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        r = PostgresFleetRegistry(engine=engine)
        rec, raw = await r.create_enrollment_key(org_id="o", name="k", created_by="admin")
        assert (await r.validate_enrollment_key("o", raw)).id == rec.id     # WHERE id = <uuid>
        assert (await r.find_enrollment_key_by_hash(_hash_key(raw))).id == rec.id
        # bump: auto-approve register increments current_uses via _bump_key_use
        w = await r.register_worker(
            name="w", org_id="o",
            capabilities=WorkerCapabilities(models_supported=["gpt-4o"]),
            enrollment_key=raw, secret_hash="x",
        )
        assert w.approval_status == WorkerApprovalStatus.APPROVED
        await r.revoke_enrollment_key(rec.id)
        assert (await r.validate_enrollment_key("o", raw)) is None
        with pytest.raises(ValueError):
            await r.revoke_enrollment_key("00000000-0000-0000-0000-000000000000")
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_workers_filters_by_project(reg):
    """list_workers(project_id=...) matches the claim predicate: None = org-global,
    a slug = that project, omitted = all. Underpins the project-scoped enqueue gate."""
    r, _ = reg
    caps = WorkerCapabilities(models_supported=["gpt-4o"])
    glob = await r.register_worker(name="g", org_id="o", capabilities=caps, secret_hash="x")
    pa = await r.register_worker(
        name="a", org_id="o", project_id="pa", capabilities=caps, secret_hash="x"
    )
    assert {w.id for w in await r.list_workers("o")} == {glob.id, pa.id}  # no filter
    assert [w.id for w in await r.list_workers("o", project_id=None)] == [glob.id]
    assert [w.id for w in await r.list_workers("o", project_id="pa")] == [pa.id]
    assert await r.list_workers("o", project_id="pb") == []


@pytest.mark.asyncio
async def test_approval_transition_is_atomic_cas(reg):
    """The expected-state check is in the UPDATE predicate, so a transition out of
    a state that's no longer current matches 0 rows and raises (no read-then-write
    race that lets two callers both win)."""
    r, _ = reg
    w = await r.register_worker(
        name="w", org_id="o",
        capabilities=WorkerCapabilities(models_supported=["gpt-4o"]), secret_hash="x",
    )
    assert (await r.approve_worker(w.id, "admin")).approval_status == WorkerApprovalStatus.APPROVED
    # No longer PENDING -> the CAS UPDATE matches nothing -> ValueError.
    with pytest.raises(ValueError):
        await r.approve_worker(w.id, "admin")
    with pytest.raises(ValueError):
        await r.reject_worker(w.id)  # reject expects PENDING


@pytest.mark.asyncio
async def test_consume_enrollment_key_enforces_cap_atomically(reg):
    """_consume_enrollment_key enforces current_uses < max_uses in the UPDATE's
    WHERE, so a single-use key is consumed exactly once even though validate() and
    the consume are separate steps."""
    r, _ = reg
    single, _raw = await r.create_enrollment_key(
        org_id="o", name="k", created_by="admin", max_uses=1
    )
    assert await r._consume_enrollment_key(single.id) is True   # first use wins
    assert await r._consume_enrollment_key(single.id) is False  # cap enforced at write time
    unlimited, _raw2 = await r.create_enrollment_key(org_id="o", name="k2", created_by="admin")
    assert await r._consume_enrollment_key(unlimited.id) is True
    assert await r._consume_enrollment_key(unlimited.id) is True


@pytest.mark.asyncio
async def test_single_use_enrollment_key_auto_approves_one_worker(reg):
    """End-to-end: a max_uses=1 key auto-approves the first worker; the second
    registration with the same key enters PENDING (key exhausted)."""
    r, _ = reg
    _rec, raw = await r.create_enrollment_key(
        org_id="o", name="k", created_by="admin", max_uses=1
    )
    caps = WorkerCapabilities(models_supported=["gpt-4o"])
    w1 = await r.register_worker(
        name="w1", org_id="o", capabilities=caps, enrollment_key=raw, secret_hash="x"
    )
    assert w1.approval_status == WorkerApprovalStatus.APPROVED
    w2 = await r.register_worker(
        name="w2", org_id="o", capabilities=caps, enrollment_key=raw, secret_hash="x"
    )
    assert w2.approval_status == WorkerApprovalStatus.PENDING
