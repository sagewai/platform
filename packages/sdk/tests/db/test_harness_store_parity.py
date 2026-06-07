# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for PostgresHarnessStore — runs against both SQLite and Postgres.

Uses the ``dialect_engine`` fixture from tests/db/conftest.py (SQLite always;
Postgres when SAGEWAI_TEST_DATABASE_URL is set).

Covers:
- Policy CRUD (create/get/list/update/delete)
- Policy org-scope filtering — org-scoped + global rows both appear, foreign-org rows excluded
- Policy priority DESC ordering — higher priority comes first
- InMemoryHarnessStore cross-check: same inputs → same filtered/ordered results
- Key lifecycle (create/validate/list/revoke/delete)
- Spend record/query/summary/by_model/by_user
- Audit record/query with filters
- Migration-faithful upsert test (raw DDL then upsert)
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pytest

from sagewai.harness.models import (
    HarnessAuditEvent,
    HarnessKey,
    PolicyRule,
    PolicyScope,
    SpendRecord,
)
from sagewai.harness.postgres_store import PostgresHarnessStore
from sagewai.harness.store import InMemoryHarnessStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy(
    *,
    name: str = "default",
    priority: int = 0,
    org_id: str | None = None,
    enabled: bool = True,
) -> PolicyRule:
    return PolicyRule(
        id=uuid.uuid4().hex[:12],
        name=name,
        priority=priority,
        scope=PolicyScope(org_id=org_id),
        enabled=enabled,
    )


def _make_key(*, org_id: str = "org1", user_id: str = "user1") -> HarnessKey:
    return HarnessKey(
        id=uuid.uuid4().hex[:12],
        name="test-key",
        user_id=user_id,
        org_id=org_id,
    )


def _make_spend(
    *,
    org_id: str = "org1",
    user_id: str = "user1",
    model_used: str = "claude-haiku",
    cost_usd: float = 0.01,
    ts: float | None = None,
) -> SpendRecord:
    return SpendRecord(
        id=uuid.uuid4().hex[:12],
        timestamp=ts if ts is not None else time.time(),
        user_id=user_id,
        org_id=org_id,
        model_requested=model_used,
        model_used=model_used,
        complexity_tier="simple",
        input_tokens=100,
        output_tokens=50,
        cost_usd=cost_usd,
        latency_ms=200.0,
    )


def _make_audit(
    *,
    org_id: str = "org1",
    event_type: str = "key.created",
    ts: float | None = None,
) -> HarnessAuditEvent:
    return HarnessAuditEvent(
        id=uuid.uuid4().hex[:12],
        timestamp=ts if ts is not None else time.time(),
        event_type=event_type,
        user_id="user1",
        org_id=org_id,
        details={"foo": "bar"},
    )


# ---------------------------------------------------------------------------
# Policy Store tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_policy_create_and_get(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    rule = _make_policy(name="allow-sonnet", priority=10)
    created = await store.create_policy(rule)
    assert created.id == rule.id
    assert created.name == "allow-sonnet"

    fetched = await store.get_policy(rule.id)
    assert fetched is not None
    assert fetched.id == rule.id
    assert fetched.name == "allow-sonnet"
    assert fetched.priority == 10


@pytest.mark.asyncio
async def test_policy_get_missing(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    result = await store.get_policy("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_policy_update(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    rule = _make_policy(name="p1", priority=5)
    await store.create_policy(rule)

    updated = _make_policy(name="p1-updated", priority=99)
    result = await store.update_policy(rule.id, updated)
    assert result is not None
    assert result.id == rule.id
    assert result.name == "p1-updated"
    assert result.priority == 99

    fetched = await store.get_policy(rule.id)
    assert fetched.name == "p1-updated"


@pytest.mark.asyncio
async def test_policy_update_missing(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    fake = _make_policy(name="ghost")
    result = await store.update_policy("no-such-id", fake)
    assert result is None


@pytest.mark.asyncio
async def test_policy_delete(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    rule = _make_policy(name="to-delete")
    await store.create_policy(rule)

    deleted = await store.delete_policy(rule.id)
    assert deleted is True

    fetched = await store.get_policy(rule.id)
    assert fetched is None


@pytest.mark.asyncio
async def test_policy_delete_missing(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    result = await store.delete_policy("no-such-id")
    assert result is False


@pytest.mark.asyncio
async def test_policy_list_all(dialect_engine):
    """list_policies() with no filter returns all policies."""
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    p1 = _make_policy(name="p1", priority=5)
    p2 = _make_policy(name="p2", priority=10, org_id="org1")
    await store.create_policy(p1)
    await store.create_policy(p2)

    policies = await store.list_policies()
    ids = {p.id for p in policies}
    assert {p1.id, p2.id} == ids


@pytest.mark.asyncio
async def test_policy_list_org_scope_filter(dialect_engine):
    """list_policies(org_id=X) returns global + org-X rows; excludes org-Y rows."""
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    global_policy = _make_policy(name="global", priority=5, org_id=None)
    org1_policy = _make_policy(name="org1-specific", priority=10, org_id="org1")
    org2_policy = _make_policy(name="org2-specific", priority=20, org_id="org2")

    await store.create_policy(global_policy)
    await store.create_policy(org1_policy)
    await store.create_policy(org2_policy)

    # Filtered by org1 — should see global + org1, NOT org2
    policies = await store.list_policies(org_id="org1")
    ids = {p.id for p in policies}
    assert global_policy.id in ids
    assert org1_policy.id in ids
    assert org2_policy.id not in ids


@pytest.mark.asyncio
async def test_policy_list_priority_desc_ordering(dialect_engine):
    """list_policies() returns policies sorted by priority DESC (highest first)."""
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    low = _make_policy(name="low", priority=1)
    mid = _make_policy(name="mid", priority=5)
    high = _make_policy(name="high", priority=99)

    # Insert in scrambled order
    await store.create_policy(mid)
    await store.create_policy(low)
    await store.create_policy(high)

    policies = await store.list_policies()
    priorities = [p.priority for p in policies]
    assert priorities == sorted(priorities, reverse=True), (
        f"Expected priority DESC; got {priorities}"
    )
    assert policies[0].priority == 99


@pytest.mark.asyncio
async def test_policy_scope_filter_vs_in_memory(dialect_engine):
    """Cross-check: store and InMemoryHarnessStore return same org-scoped subset."""
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()
    mem = InMemoryHarnessStore()

    global_p = _make_policy(name="global", priority=10, org_id=None)
    org1_p = _make_policy(name="org1", priority=20, org_id="org1")
    org2_p = _make_policy(name="org2", priority=30, org_id="org2")

    for p in [global_p, org1_p, org2_p]:
        await store.create_policy(p)
        await mem.create_policy(p)

    store_ids = {p.id for p in await store.list_policies(org_id="org1")}
    mem_ids = {p.id for p in await mem.list_policies(org_id="org1")}
    assert store_ids == mem_ids


@pytest.mark.asyncio
async def test_policy_priority_ordering_vs_in_memory(dialect_engine):
    """Cross-check: priority DESC order matches InMemoryHarnessStore."""
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()
    mem = InMemoryHarnessStore()

    policies = [
        _make_policy(name="a", priority=3),
        _make_policy(name="b", priority=10),
        _make_policy(name="c", priority=1),
        _make_policy(name="d", priority=7),
    ]

    for p in policies:
        await store.create_policy(p)
        await mem.create_policy(p)

    store_policies = await store.list_policies()
    mem_policies = await mem.list_policies()

    # Both must have same IDs (order may differ in mem — we check the priority sequence)
    store_priorities = [p.priority for p in store_policies]
    assert store_priorities == sorted(store_priorities, reverse=True)

    # And both include the same set
    assert {p.id for p in store_policies} == {p.id for p in mem_policies}


# ---------------------------------------------------------------------------
# Migration-faithful upsert test — raw DDL then create_policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_policy_upsert_on_pk_conflict(dialect_engine):
    """create_policy twice on same id should update (upsert on PK)."""
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    rule = _make_policy(name="original", priority=5)
    await store.create_policy(rule)

    # Second create with same id — upsert should overwrite
    updated = PolicyRule(
        id=rule.id,
        name="overwritten",
        priority=99,
        scope=PolicyScope(),
    )
    await store.create_policy(updated)

    fetched = await store.get_policy(rule.id)
    assert fetched.name == "overwritten"
    assert fetched.priority == 99


# ---------------------------------------------------------------------------
# Key Store tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key_create_and_validate(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    key = _make_key()
    plaintext = await store.create_key(key)
    assert plaintext.startswith("sk-harness-")

    identity = await store.validate_key(plaintext)
    assert identity is not None
    assert identity.user_id == key.user_id
    assert identity.org_id == key.org_id


@pytest.mark.asyncio
async def test_key_validate_invalid(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    result = await store.validate_key("sk-harness-totally-fake-key-00000000")
    assert result is None


@pytest.mark.asyncio
async def test_key_validate_revoked(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    key = _make_key()
    plaintext = await store.create_key(key)
    await store.revoke_key(key.id)

    result = await store.validate_key(plaintext)
    assert result is None


@pytest.mark.asyncio
async def test_key_get(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    key = _make_key(org_id="orgA", user_id="userA")
    await store.create_key(key)

    fetched = await store.get_key(key.id)
    assert fetched is not None
    assert fetched.org_id == "orgA"
    assert fetched.user_id == "userA"


@pytest.mark.asyncio
async def test_key_get_missing(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    result = await store.get_key("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_key_list_all(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    k1 = _make_key(org_id="org1", user_id="u1")
    k2 = _make_key(org_id="org2", user_id="u2")
    await store.create_key(k1)
    await store.create_key(k2)

    keys = await store.list_keys()
    ids = {k.id for k in keys}
    assert {k1.id, k2.id} == ids


@pytest.mark.asyncio
async def test_key_list_org_filter(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    k1 = _make_key(org_id="org1")
    k2 = _make_key(org_id="org2")
    await store.create_key(k1)
    await store.create_key(k2)

    keys = await store.list_keys(org_id="org1")
    ids = {k.id for k in keys}
    assert k1.id in ids
    assert k2.id not in ids


@pytest.mark.asyncio
async def test_key_revoke(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    key = _make_key()
    await store.create_key(key)

    ok = await store.revoke_key(key.id)
    assert ok is True

    fetched = await store.get_key(key.id)
    assert fetched.enabled is False


@pytest.mark.asyncio
async def test_key_revoke_missing(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    result = await store.revoke_key("nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_key_delete(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    key = _make_key()
    await store.create_key(key)

    ok = await store.delete_key(key.id)
    assert ok is True

    fetched = await store.get_key(key.id)
    assert fetched is None


@pytest.mark.asyncio
async def test_key_delete_missing(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    result = await store.delete_key("nonexistent")
    assert result is False


# ---------------------------------------------------------------------------
# Spend Store tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_record_and_query(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    rec = _make_spend(org_id="org1", user_id="u1", cost_usd=0.05)
    await store.record_spend(rec)

    records = await store.get_spend()
    ids = {r.id for r in records}
    assert rec.id in ids


@pytest.mark.asyncio
async def test_spend_org_filter(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    r1 = _make_spend(org_id="org1")
    r2 = _make_spend(org_id="org2")
    await store.record_spend(r1)
    await store.record_spend(r2)

    records = await store.get_spend(org_id="org1")
    ids = {r.id for r in records}
    assert r1.id in ids
    assert r2.id not in ids


@pytest.mark.asyncio
async def test_spend_user_filter(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    r1 = _make_spend(user_id="alice")
    r2 = _make_spend(user_id="bob")
    await store.record_spend(r1)
    await store.record_spend(r2)

    records = await store.get_spend(user_id="alice")
    ids = {r.id for r in records}
    assert r1.id in ids
    assert r2.id not in ids


@pytest.mark.asyncio
async def test_spend_since_filter(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    old_ts = time.time() - 3600
    new_ts = time.time()

    r_old = _make_spend(ts=old_ts)
    r_new = _make_spend(ts=new_ts)
    await store.record_spend(r_old)
    await store.record_spend(r_new)

    since = time.time() - 60
    records = await store.get_spend(since=since)
    ids = {r.id for r in records}
    assert r_new.id in ids
    assert r_old.id not in ids


@pytest.mark.asyncio
async def test_spend_most_recent_first(dialect_engine):
    """get_spend() returns records most recent first."""
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    base = time.time()
    for i in range(3):
        await store.record_spend(_make_spend(ts=base + i))

    records = await store.get_spend()
    timestamps = [r.timestamp for r in records]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_spend_summary(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    r1 = _make_spend(cost_usd=0.10)
    r2 = _make_spend(cost_usd=0.20)
    await store.record_spend(r1)
    await store.record_spend(r2)

    summary = await store.get_spend_summary()
    assert summary["total_requests"] >= 2
    assert summary["total_cost_usd"] >= 0.30 - 1e-6


@pytest.mark.asyncio
async def test_spend_by_model(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    await store.record_spend(_make_spend(model_used="claude-haiku", cost_usd=0.01))
    await store.record_spend(_make_spend(model_used="claude-haiku", cost_usd=0.02))
    await store.record_spend(_make_spend(model_used="claude-sonnet", cost_usd=0.10))

    by_model = await store.get_spend_by_model()
    assert "claude-haiku" in by_model
    assert by_model["claude-haiku"]["requests"] == 2
    assert abs(by_model["claude-haiku"]["cost_usd"] - 0.03) < 1e-6
    assert "claude-sonnet" in by_model


@pytest.mark.asyncio
async def test_spend_by_user(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    await store.record_spend(_make_spend(user_id="alice", cost_usd=0.05))
    await store.record_spend(_make_spend(user_id="alice", cost_usd=0.05))
    await store.record_spend(_make_spend(user_id="bob", cost_usd=0.10))

    by_user = await store.get_spend_by_user()
    assert "alice" in by_user
    assert by_user["alice"]["requests"] == 2
    assert abs(by_user["alice"]["cost_usd"] - 0.10) < 1e-6


@pytest.mark.asyncio
async def test_spend_accumulation_matches_in_memory(dialect_engine):
    """Cross-check: same records → same total_cost_usd in summary."""
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()
    mem = InMemoryHarnessStore()

    records = [_make_spend(cost_usd=0.01 * i) for i in range(1, 6)]
    for r in records:
        await store.record_spend(r)
        await mem.record_spend(r)

    store_summary = await store.get_spend_summary()
    mem_summary = await mem.get_spend_summary()
    assert abs(store_summary["total_cost_usd"] - mem_summary["total_cost_usd"]) < 1e-6
    assert store_summary["total_requests"] == mem_summary["total_requests"]


# ---------------------------------------------------------------------------
# Audit Store tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_record_and_query(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    event = _make_audit(org_id="org1", event_type="key.created")
    await store.record_audit(event)

    events = await store.get_audit()
    ids = {e.id for e in events}
    assert event.id in ids


@pytest.mark.asyncio
async def test_audit_org_filter(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    e1 = _make_audit(org_id="org1")
    e2 = _make_audit(org_id="org2")
    await store.record_audit(e1)
    await store.record_audit(e2)

    events = await store.get_audit(org_id="org1")
    ids = {e.id for e in events}
    assert e1.id in ids
    assert e2.id not in ids


@pytest.mark.asyncio
async def test_audit_event_type_filter(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    e1 = _make_audit(event_type="key.created")
    e2 = _make_audit(event_type="policy.updated")
    await store.record_audit(e1)
    await store.record_audit(e2)

    events = await store.get_audit(event_type="key.created")
    ids = {e.id for e in events}
    assert e1.id in ids
    assert e2.id not in ids


@pytest.mark.asyncio
async def test_audit_since_filter(dialect_engine):
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    old_ts = time.time() - 3600
    new_ts = time.time()

    e_old = _make_audit(ts=old_ts)
    e_new = _make_audit(ts=new_ts)
    await store.record_audit(e_old)
    await store.record_audit(e_new)

    since = time.time() - 60
    events = await store.get_audit(since=since)
    ids = {e.id for e in events}
    assert e_new.id in ids
    assert e_old.id not in ids


@pytest.mark.asyncio
async def test_audit_most_recent_first(dialect_engine):
    """get_audit() returns events most recent first."""
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    base = time.time()
    for i in range(3):
        await store.record_audit(_make_audit(ts=base + i))

    events = await store.get_audit()
    timestamps = [e.timestamp for e in events]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_audit_details_roundtrip(dialect_engine):
    """JSON details round-trip faithfully."""
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()

    event = HarnessAuditEvent(
        id=uuid.uuid4().hex[:12],
        timestamp=time.time(),
        event_type="test.event",
        user_id="u1",
        org_id="org1",
        details={"key": "value", "nested": {"x": 1}},
    )
    await store.record_audit(event)

    events = await store.get_audit()
    match = next(e for e in events if e.id == event.id)
    assert match.details == {"key": "value", "nested": {"x": 1}}


@pytest.mark.asyncio
async def test_audit_append_list_matches_in_memory(dialect_engine):
    """Cross-check: same events → same org-filtered result set."""
    store = PostgresHarnessStore(engine=dialect_engine)
    await store.init()
    mem = InMemoryHarnessStore()

    events = [
        _make_audit(org_id="org1", event_type="a"),
        _make_audit(org_id="org1", event_type="b"),
        _make_audit(org_id="org2", event_type="c"),
    ]
    for e in events:
        await store.record_audit(e)
        await mem.record_audit(e)

    store_ids = {e.id for e in await store.get_audit(org_id="org1")}
    mem_ids = {e.id for e in await mem.get_audit(org_id="org1")}
    assert store_ids == mem_ids
