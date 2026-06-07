# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Durable, hash-chained, per-tenant audit (W8).

Adversarial audit tests (category #6 of the W0 cross-tenant charter):

- per-``(org, project)`` chains assign their own sequence, independently, even
  under concurrent appends (no lost events);
- ``verify_chain`` re-walks a chain and detects every tamper class — edit,
  reorder, insertion, and deletion (mid-chain *and* tail / full-chain) via the
  in-table hash chain plus the per-chain tip checkpoint;
- reads are gated by ``RequestContext``: a project admin reads only their own
  chain (cross-scope → 404, audit does NOT inherit the org/NULL chain), an
  under-privileged in-scope actor → 403, and an org admin may aggregate
  independent chains side-by-side.

Run on SQLite always; on Postgres when SAGEWAI_TEST_DATABASE_URL is set.
"""

import asyncio
import importlib

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text

from sagewai.admin.identity_store import IdentityStore, TenantAccessError
from sagewai.admin.tenancy import ALL_SCOPES, SCOPE_WRITE, RequestContext, UserRef
from sagewai.admin.tenant_audit import AuditPermissionError, TenantAuditStore


@pytest_asyncio.fixture
async def seeded(dialect_engine):
    """An org with two isolated projects + an initialised audit store."""
    ids = IdentityStore(engine=dialect_engine)
    await ids.init()
    oid = (await ids.bootstrap_org("Acme", "acme"))["id"]
    pa = (await ids.create_project(oid, "a", "A"))["id"]
    pb = (await ids.create_project(oid, "b", "B"))["id"]
    audit = TenantAuditStore(engine=dialect_engine)
    await audit.init()
    return audit, oid, pa, pb


async def _raw(engine, sql: str, **params) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)


def _ctx(org_id, roles, *, project_id=None, scopes=ALL_SCOPES):
    return RequestContext(
        actor=UserRef(id="actor", label="actor@acme.io"),
        org_id=org_id,
        project_id=project_id,
        roles=frozenset(roles),
        scopes=frozenset(scopes),
        request_id="req-test",
        tenancy_mode="multi",
    )


# --------------------------------------------------------------- append / seq


async def test_append_assigns_sequential_seq_per_chain(seeded):
    audit, oid, pa, pb = seeded
    await audit.append(oid, pa, "agent.create", actor_user_id="u1")
    await audit.append(oid, pa, "agent.update", actor_user_id="u1")
    await audit.append(oid, pa, "agent.delete", actor_user_id="u1")

    chain = await audit._read_chain(oid, pa)
    assert [r["seq"] for r in chain] == [1, 2, 3]
    assert chain[0]["prev_hash"] is None
    # each event chains onto the previous event's hash.
    assert chain[1]["prev_hash"] == chain[0]["hash"]
    assert chain[2]["prev_hash"] == chain[1]["hash"]
    assert [r["action"] for r in chain] == ["agent.create", "agent.update", "agent.delete"]


async def test_chains_are_independent_per_project(seeded):
    audit, oid, pa, pb = seeded
    await audit.append(oid, pa, "a.1")
    await audit.append(oid, pa, "a.2")
    await audit.append(oid, pb, "b.1")

    # B's chain starts its own sequence at 1 — not 3.
    chain_b = await audit._read_chain(oid, pb)
    assert [r["seq"] for r in chain_b] == [1]
    assert chain_b[0]["prev_hash"] is None


async def test_concurrent_appends_do_not_lose_events(seeded):
    """20 concurrent appends to one chain all persist with a contiguous seq.

    Without the head checkpoint + retry, racing writers read the same tip and
    collide on the unique seq index, losing all but a handful of events.
    """
    audit, oid, pa, pb = seeded
    n = 20
    await asyncio.gather(*(audit.append(oid, pa, f"e.{i}", actor_user_id=f"u{i}") for i in range(n)))

    chain = await audit._read_chain(oid, pa)
    assert len(chain) == n  # nothing lost
    assert [r["seq"] for r in chain] == list(range(1, n + 1))  # contiguous, no gaps
    assert (await audit._verify_chain(oid, pa)).ok is True


# ------------------------------------------------------------------- verify ok


async def test_verify_chain_valid(seeded):
    audit, oid, pa, pb = seeded
    for i in range(5):
        await audit.append(oid, pa, f"e.{i}", metadata={"i": i})
    result = await audit._verify_chain(oid, pa)
    assert result.ok is True
    assert result.length == 5
    assert result.broken_at is None


# ---------------------------------------------------------- verify: tampering


async def test_verify_detects_edit(seeded):
    audit, oid, pa, pb = seeded
    for i in range(3):
        await audit.append(oid, pa, f"e.{i}")
    # Mutate an existing event's content but leave its stored hash → mismatch.
    await _raw(
        audit.engine,
        "UPDATE audit_event SET action = 'TAMPERED' "
        "WHERE org_id = :o AND project_id = :p AND seq = 2",
        o=oid,
        p=pa,
    )
    result = await audit._verify_chain(oid, pa)
    assert result.ok is False
    assert result.broken_at == 2
    assert "hash" in (result.reason or "").lower()


async def test_verify_detects_delete(seeded):
    audit, oid, pa, pb = seeded
    for i in range(4):
        await audit.append(oid, pa, f"e.{i}")
    # Remove a middle event → a gap in the sequence.
    await _raw(
        audit.engine,
        "DELETE FROM audit_event WHERE org_id = :o AND project_id = :p AND seq = 2",
        o=oid,
        p=pa,
    )
    result = await audit._verify_chain(oid, pa)
    assert result.ok is False
    assert result.broken_at == 2


async def test_verify_detects_tail_deletion(seeded):
    """Deleting the LAST event leaves no gap — the checkpoint catches it."""
    audit, oid, pa, pb = seeded
    for i in range(3):
        await audit.append(oid, pa, f"e.{i}")
    await _raw(
        audit.engine,
        "DELETE FROM audit_event WHERE org_id = :o AND project_id = :p AND seq = 3",
        o=oid,
        p=pa,
    )
    result = await audit._verify_chain(oid, pa)
    assert result.ok is False
    assert result.broken_at == 3  # the checkpoint still expects seq 3
    assert "tip" in (result.reason or "").lower()


async def test_verify_detects_full_chain_deletion(seeded):
    """Deleting EVERY event still fails — the checkpoint proves events existed."""
    audit, oid, pa, pb = seeded
    for i in range(3):
        await audit.append(oid, pa, f"e.{i}")
    await _raw(
        audit.engine,
        "DELETE FROM audit_event WHERE org_id = :o AND project_id = :p",
        o=oid,
        p=pa,
    )
    result = await audit._verify_chain(oid, pa)
    assert result.ok is False
    assert result.length == 0


async def test_verify_detects_reorder(seeded):
    audit, oid, pa, pb = seeded
    for i in range(3):
        await audit.append(oid, pa, f"e.{i}")
    # Swap the seq of events 2 and 3 (via a temp slot to dodge the unique index).
    await _raw(
        audit.engine,
        "UPDATE audit_event SET seq = -1 WHERE org_id=:o AND project_id=:p AND seq=2",
        o=oid,
        p=pa,
    )
    await _raw(
        audit.engine,
        "UPDATE audit_event SET seq = 2 WHERE org_id=:o AND project_id=:p AND seq=3",
        o=oid,
        p=pa,
    )
    await _raw(
        audit.engine,
        "UPDATE audit_event SET seq = 3 WHERE org_id=:o AND project_id=:p AND seq=-1",
        o=oid,
        p=pa,
    )
    result = await audit._verify_chain(oid, pa)
    assert result.ok is False
    # Reorder breaks the prev_hash linkage at the first out-of-place event.
    assert result.broken_at == 2


async def test_verify_detects_insert(seeded):
    audit, oid, pa, pb = seeded
    for i in range(3):
        await audit.append(oid, pa, f"e.{i}")  # seqs 1, 2, 3
    # A genuinely-appended 4th event (real, DB-managed created_at + a hash
    # chained onto seq 3) is then relocated into the middle as the new seq 2 —
    # modelling an attacker who splices an event into the chain. Renumber via
    # negative temp slots to dodge the unique seq index.
    await audit.append(oid, pa, "spliced.event")  # seq 4, prev = hash(seq 3)
    for sql in (
        "UPDATE audit_event SET seq = -seq WHERE org_id=:o AND project_id=:p AND seq IN (2, 3)",
        "UPDATE audit_event SET seq = 2 WHERE org_id=:o AND project_id=:p AND seq = 4",
        "UPDATE audit_event SET seq = 3 WHERE org_id=:o AND project_id=:p AND seq = -2",
        "UPDATE audit_event SET seq = 4 WHERE org_id=:o AND project_id=:p AND seq = -3",
    ):
        await _raw(audit.engine, sql, o=oid, p=pa)
    # Order is now 1, (spliced), 2, 3 — the spliced event was chained onto the
    # old tip, so it does not chain onto seq 1.
    result = await audit._verify_chain(oid, pa)
    assert result.ok is False
    assert result.broken_at == 2


# ------------------------------------------------------- scope isolation (raw)


async def test_project_a_cannot_read_b_chain(seeded):
    audit, oid, pa, pb = seeded
    await audit.append(oid, pa, "a.secret")
    await audit.append(oid, pb, "b.secret")
    await audit.append(oid, pb, "b.secret2")

    chain_a = await audit._read_chain(oid, pa)
    assert {r["project_id"] for r in chain_a} == {pa}
    assert [r["action"] for r in chain_a] == ["a.secret"]

    chain_b = await audit._read_chain(oid, pb)
    assert {r["project_id"] for r in chain_b} == {pb}
    assert [r["action"] for r in chain_b] == ["b.secret", "b.secret2"]

    # Each chain verifies independently.
    assert (await audit._verify_chain(oid, pa)).ok is True
    assert (await audit._verify_chain(oid, pb)).ok is True


async def test_org_chain_is_separate_from_project_chains(seeded):
    audit, oid, pa, pb = seeded
    await audit.append(oid, None, "org.settings.update")  # org-level chain
    await audit.append(oid, pa, "agent.create")  # project chain

    org_chain = await audit._read_chain(oid, None)
    assert {r["project_id"] for r in org_chain} == {None}
    assert [r["action"] for r in org_chain] == ["org.settings.update"]
    assert org_chain[0]["seq"] == 1

    # Audit does NOT inherit: the project chain never sees the org-level event.
    proj_chain = await audit._read_chain(oid, pa)
    assert {r["project_id"] for r in proj_chain} == {pa}
    assert [r["action"] for r in proj_chain] == ["agent.create"]
    assert proj_chain[0]["seq"] == 1  # its own sequence, not continued from org

    assert (await audit._verify_chain(oid, None)).ok is True
    assert (await audit._verify_chain(oid, pa)).ok is True


# --------------------------------------------------- RequestContext enforcement


async def test_project_admin_reads_own_chain(seeded):
    audit, oid, pa, pb = seeded
    await audit.append(oid, pa, "a.event")
    ctx = _ctx(oid, {"project:admin"}, project_id=pa)
    chain = await audit.read_chain(ctx)
    assert [r["action"] for r in chain] == ["a.event"]
    assert (await audit.verify_chain(ctx)).ok is True


async def test_project_admin_cannot_read_other_project_404(seeded):
    audit, oid, pa, pb = seeded
    await audit.append(oid, pb, "b.event")
    ctx = _ctx(oid, {"project:admin"}, project_id=pa)
    # Cross-scope read hides existence: 404, not 403.
    with pytest.raises(TenantAccessError):
        await audit.read_chain(ctx, project_id=pb)
    with pytest.raises(TenantAccessError):
        await audit.verify_chain(ctx, project_id=pb)


async def test_project_admin_cannot_read_org_chain_404(seeded):
    audit, oid, pa, pb = seeded
    await audit.append(oid, None, "org.event")
    ctx = _ctx(oid, {"project:admin"}, project_id=pa)
    # Audit does not inherit: a project actor cannot reach the org-level chain.
    with pytest.raises(TenantAccessError):
        await audit.read_chain(ctx, project_id=None)


async def test_under_privileged_in_scope_actor_403(seeded):
    audit, oid, pa, pb = seeded
    await audit.append(oid, pa, "a.event")
    # In their own project, but project:member lacks audit:read -> 403.
    ctx = _ctx(oid, {"project:member"}, project_id=pa)
    with pytest.raises(AuditPermissionError):
        await audit.read_chain(ctx)


async def test_read_requires_read_scope_403(seeded):
    audit, oid, pa, pb = seeded
    await audit.append(oid, pa, "a.event")
    # Right role, but a write-only token scope cannot read audit.
    ctx = _ctx(oid, {"project:admin"}, project_id=pa, scopes={SCOPE_WRITE})
    with pytest.raises(AuditPermissionError):
        await audit.read_chain(ctx)


async def test_org_admin_reads_any_chain(seeded):
    audit, oid, pa, pb = seeded
    await audit.append(oid, pa, "a.event")
    await audit.append(oid, None, "org.event")
    ctx = _ctx(oid, {"org:admin"}, project_id=None)
    assert [r["action"] for r in await audit.read_chain(ctx, project_id=pa)] == ["a.event"]
    assert [r["action"] for r in await audit.read_chain(ctx, project_id=None)] == ["org.event"]


async def test_org_admin_aggregates_chains_side_by_side(seeded):
    audit, oid, pa, pb = seeded
    await audit.append(oid, pa, "a.event")
    await audit.append(oid, pb, "b.event")
    await audit.append(oid, None, "org.event")
    ctx = _ctx(oid, {"org:admin"}, project_id=None)

    chains = await audit.read_chains(ctx, [pa, pb, None])
    assert set(chains) == {pa, pb, None}
    assert [r["action"] for r in chains[pa]] == ["a.event"]
    assert [r["action"] for r in chains[pb]] == ["b.event"]
    assert [r["action"] for r in chains[None]] == ["org.event"]
    # Chains stay independent — each starts its own sequence at 1.
    assert all(c[0]["seq"] == 1 for c in chains.values())


async def test_aggregation_denied_for_project_admin(seeded):
    audit, oid, pa, pb = seeded
    ctx = _ctx(oid, {"project:admin"}, project_id=pa)
    with pytest.raises(AuditPermissionError):
        await audit.read_chains(ctx, [pa])


# -------------------------------------------------------------- migration 010


def test_migration_010_roundtrip_sqlite():
    """Alembic 010 creates/drops the audit tables on top of the 009 tenancy schema."""
    audit_mod = importlib.import_module("sagewai.db.migrations.versions.010_tenant_audit")
    assert audit_mod.revision == "010_tenant_audit"
    assert audit_mod.down_revision == "009_tenancy_identity"

    tenancy_mod = importlib.import_module("sagewai.db.migrations.versions.009_tenancy_identity")

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    audit_tables = {"audit_event", "audit_chain_head"}
    engine = sa.create_engine("sqlite://")  # sync, in-memory
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            tenancy_mod.upgrade()  # org/project/... — the audit FK targets
            audit_mod.upgrade()
            assert audit_tables <= set(sa.inspect(conn).get_table_names())
            audit_mod.downgrade()
            assert not (audit_tables & set(sa.inspect(conn).get_table_names()))
            # The tenancy tables it depends on are untouched by 010's downgrade.
            assert "project" in set(sa.inspect(conn).get_table_names())
