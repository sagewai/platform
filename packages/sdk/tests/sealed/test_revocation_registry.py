# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for RevocationRegistry CRUD + lookup paths (Postgres-gated)."""
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.sealed.audit import AuditWriter
from sagewai.sealed.revocation import (
    Revocation,
    RevocationRegistry,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAGEWAI_DATABASE_URL"),
    reason="SAGEWAI_DATABASE_URL not set",
)


@pytest.fixture
async def pg_store():
    from sagewai.core.stores.postgres import PostgresStore
    store = PostgresStore(database_url=os.environ["SAGEWAI_DATABASE_URL"])
    await store.initialize()
    # Clean revocations table before each test
    await store._pool.execute("DELETE FROM sealed_revocations")
    yield store
    await store._pool.execute("DELETE FROM sealed_revocations")
    await store.close()


@pytest.fixture
def fake_audit():
    fake_store = MagicMock()
    fake_store._pool = MagicMock()
    fake_store._pool.execute = AsyncMock()
    return AuditWriter(fake_store)


@pytest.mark.asyncio
async def test_revoke_per_key_inserts_row(pg_store, fake_audit):
    reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
    rows = await reg.revoke(
        profile_id="acme",
        secret_key="K1",
        reason="leaked",
        actor_id="op1",
    )
    assert len(rows) == 1
    assert isinstance(rows[0], Revocation)
    assert rows[0].profile_id == "acme"
    assert rows[0].secret_key == "K1"
    assert rows[0].hard is False


@pytest.mark.asyncio
async def test_double_revoke_same_key_raises_conflict(pg_store, fake_audit):
    reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
    await reg.revoke(profile_id="acme", secret_key="K1", reason="r1")
    from sagewai.sealed.revocation import RevocationConflictError
    with pytest.raises(RevocationConflictError):
        await reg.revoke(profile_id="acme", secret_key="K1", reason="r2")


@pytest.mark.asyncio
async def test_revoke_after_lift_succeeds(pg_store, fake_audit):
    reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
    [first] = await reg.revoke(profile_id="acme", secret_key="K1", reason="r1")
    await reg.lift(first.id)
    [second] = await reg.revoke(profile_id="acme", secret_key="K1", reason="r2")
    assert second.id != first.id


@pytest.mark.asyncio
async def test_lift_sets_lifted_at(pg_store, fake_audit):
    reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
    [r] = await reg.revoke(profile_id="acme", secret_key="K1", reason="r")
    lifted = await reg.lift(r.id, actor_id="op2")
    assert lifted.lifted_at is not None
    assert lifted.lifted_by == "op2"


@pytest.mark.asyncio
async def test_lift_unknown_id_raises(pg_store, fake_audit):
    reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
    with pytest.raises(LookupError):
        await reg.lift(999_999)


@pytest.mark.asyncio
async def test_lift_already_lifted_raises_conflict(pg_store, fake_audit):
    reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
    [r] = await reg.revoke(profile_id="acme", secret_key="K1", reason="r")
    await reg.lift(r.id)
    from sagewai.sealed.revocation import RevocationConflictError
    with pytest.raises(RevocationConflictError):
        await reg.lift(r.id)


@pytest.mark.asyncio
async def test_is_revoked_returns_active_or_none(pg_store, fake_audit):
    reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
    assert await reg.is_revoked(profile_id="acme", secret_key="K1") is None
    [r] = await reg.revoke(profile_id="acme", secret_key="K1", reason="r")
    found = await reg.is_revoked(profile_id="acme", secret_key="K1")
    assert found is not None
    assert found.id == r.id
    await reg.lift(r.id)
    assert await reg.is_revoked(profile_id="acme", secret_key="K1") is None


@pytest.mark.asyncio
async def test_find_active_for_keys_batch(pg_store, fake_audit):
    reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
    await reg.revoke(profile_id="acme", secret_key="A", reason="r")
    await reg.revoke(profile_id="acme", secret_key="B", reason="r")
    found = await reg.find_active_for_keys(
        profile_id="acme", secret_keys=["A", "B", "C"]
    )
    assert set(found.keys()) == {"A", "B"}


@pytest.mark.asyncio
async def test_bulk_profile_revoke_all_or_nothing(pg_store, fake_audit):
    """When secret_key=None and bulk fails partway, full rollback."""
    reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
    # Pre-revoke K2 so the bulk attempt for [K1, K2, K3] hits a conflict on K2
    await reg.revoke(profile_id="acme", secret_key="K2", reason="pre-existing")

    from sagewai.sealed.revocation import RevocationConflictError
    with pytest.raises(RevocationConflictError):
        await reg.revoke(
            profile_id="acme",
            secret_key=None,
            reason="bulk attempt",
            current_keys=["K1", "K2", "K3"],
        )

    # K1 and K3 must NOT have been left committed
    assert await reg.is_revoked(profile_id="acme", secret_key="K1") is None
    assert await reg.is_revoked(profile_id="acme", secret_key="K3") is None


@pytest.mark.asyncio
async def test_list_active_filters_lifted(pg_store, fake_audit):
    reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
    [r1] = await reg.revoke(profile_id="acme", secret_key="K1", reason="r")
    [r2] = await reg.revoke(profile_id="other", secret_key="K2", reason="r")
    await reg.lift(r1.id)
    actives = await reg.list_active()
    ids = {r.id for r in actives}
    assert r2.id in ids
    assert r1.id not in ids


@pytest.mark.asyncio
async def test_list_all_includes_lifted(pg_store, fake_audit):
    reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
    [r] = await reg.revoke(profile_id="acme", secret_key="K1", reason="r")
    await reg.lift(r.id)
    all_rows = await reg.list_all(include_lifted=True)
    assert any(row.id == r.id for row in all_rows)


@pytest.mark.asyncio
async def test_revoke_emits_audit(pg_store):
    fake_store = MagicMock()
    fake_store._pool = MagicMock()
    fake_store._pool.execute = AsyncMock()
    audit = AuditWriter(fake_store)
    reg = RevocationRegistry(pg_store, audit_writer=audit)
    await reg.revoke(profile_id="acme", secret_key="K1", reason="leaked")
    # The audit writer was invoked at least once with secret.revoked
    calls = [c for c in fake_store._pool.execute.await_args_list
             if c.args and len(c.args) > 1 and c.args[1] == "secret.revoked"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_runs_using_revocation_returns_affected_runs(pg_store, fake_audit):
    """runs_using_revocation finds in-flight runs that injected the key."""
    # Insert two running runs that injected ("acme", "K1")
    await pg_store._pool.execute(
        """
        INSERT INTO workflow_runs
          (id, workflow_name, run_id, status, security_profile_ref,
           effective_env_keys, effective_secret_keys)
        VALUES
          ('id-test-1', 'wf1', 'r-test-1', 'running', 'acme',
           ARRAY['K1','K2'], ARRAY['K1']),
          ('id-test-2', 'wf1', 'r-test-2', 'running', 'acme',
           ARRAY['K1'], ARRAY['K1']),
          ('id-test-done', 'wf1', 'r-test-done', 'completed', 'acme',
           ARRAY['K1'], ARRAY['K1'])
        """
    )
    try:
        reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
        [r] = await reg.revoke(profile_id="acme", secret_key="K1", reason="r")
        affected = await reg.runs_using_revocation(r.id)
        assert set(affected) == {"r-test-1", "r-test-2"}  # completed run excluded
    finally:
        await pg_store._pool.execute(
            "DELETE FROM workflow_runs WHERE run_id IN ('r-test-1', 'r-test-2', 'r-test-done')"
        )


@pytest.mark.asyncio
async def test_hard_revoke_marks_in_flight_runs(pg_store, fake_audit):
    """Hard revoke updates workflow_runs.revoked_at on affected runs."""
    await pg_store._pool.execute(
        """
        INSERT INTO workflow_runs
          (id, workflow_name, run_id, status, security_profile_ref,
           effective_env_keys, effective_secret_keys)
        VALUES ('id-hard-1', 'wf1', 'r-hard-1', 'running', 'acme',
                ARRAY['K1'], ARRAY['K1'])
        """
    )
    try:
        reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
        [r] = await reg.revoke(
            profile_id="acme", secret_key="K1",
            reason="active breach", hard=True, actor_id="op",
        )
        assert r.hard is True
        row = await pg_store._pool.fetchrow(
            "SELECT revoked_at, revoke_reason FROM workflow_runs WHERE run_id = 'r-hard-1'"
        )
        assert row["revoked_at"] is not None
        assert row["revoke_reason"] == "active breach"
    finally:
        await pg_store._pool.execute(
            "DELETE FROM workflow_runs WHERE run_id = 'r-hard-1'"
        )


@pytest.mark.asyncio
async def test_hard_revoke_emits_hard_revoked_audit(pg_store):
    fake_store = MagicMock()
    fake_store._pool = MagicMock()
    fake_store._pool.execute = AsyncMock()
    audit = AuditWriter(fake_store)
    reg = RevocationRegistry(pg_store, audit_writer=audit)
    try:
        await reg.revoke(profile_id="acme", secret_key="K1", reason="r", hard=True)
        types = [c.args[1] for c in fake_store._pool.execute.await_args_list
                 if c.args and len(c.args) > 1]
        assert "secret.hard_revoked" in types
    finally:
        await pg_store._pool.execute(
            "DELETE FROM workflow_runs WHERE run_id LIKE 'r-%'"
        )


@pytest.mark.asyncio
async def test_soft_revoke_does_not_update_workflow_runs(pg_store, fake_audit):
    """Soft revoke does NOT touch workflow_runs.revoked_at."""
    await pg_store._pool.execute(
        """
        INSERT INTO workflow_runs
          (id, workflow_name, run_id, status, security_profile_ref,
           effective_env_keys, effective_secret_keys)
        VALUES ('id-soft', 'wf1', 'r-soft', 'running', 'acme',
                ARRAY['K1'], ARRAY['K1'])
        """
    )
    try:
        reg = RevocationRegistry(pg_store, audit_writer=fake_audit)
        await reg.revoke(profile_id="acme", secret_key="K1", reason="r", hard=False)
        row = await pg_store._pool.fetchrow(
            "SELECT revoked_at FROM workflow_runs WHERE run_id = 'r-soft'"
        )
        assert row["revoked_at"] is None
    finally:
        await pg_store._pool.execute(
            "DELETE FROM workflow_runs WHERE run_id = 'r-soft'"
        )
