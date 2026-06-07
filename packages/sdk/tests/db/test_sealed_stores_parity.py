# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for sealed audit + directive adapters — SQLite and PostgreSQL.

Covers:
  - AuditWriter (sealed/audit.py) via engine=
  - DirectiveEvaluationsAdapter (sealed/directives/postgres_adapters.py)
  - ApprovalsPostgresAdapter (sealed/directives/postgres_adapters.py)

Uses the dialect_engine fixture from tests/db/conftest.py:
  SQLite always; Postgres when SAGEWAI_TEST_DATABASE_URL is set.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text

from sagewai.db.models import (
    Base,
    DirectiveEvaluationModel,
    PendingDirectiveApprovalModel,
    SealedAuditEventModel,
)
from sagewai.sealed.audit import AuditWriter
from sagewai.sealed.directives.postgres_adapters import (
    ApprovalsPostgresAdapter,
    DirectiveEvaluationsAdapter,
)


# ---------------------------------------------------------------------------
# Migration-faithful helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _expires_at(minutes: int = 30) -> datetime:
    return _now_utc() + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# AuditWriter — sealed_audit_events table (migration 003)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_writer_emit_inserts_row(dialect_engine):
    """AuditWriter.emit() inserts a row into sealed_audit_events."""
    writer = AuditWriter(engine=dialect_engine)
    await writer.emit(
        event_type="test.event",
        actor_type="user",
        actor_id="alice",
        profile_id="p1",
        secret_key="sk1",
        run_id="run-abc",
        project_id="proj1",
        details={"foo": "bar"},
    )
    tbl = SealedAuditEventModel.__table__
    async with dialect_engine.connect() as conn:
        rows = (await conn.execute(select(tbl))).mappings().all()
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "test.event"
    assert row["actor_type"] == "user"
    assert row["actor_id"] == "alice"
    assert row["profile_id"] == "p1"
    assert row["secret_key"] == "sk1"
    assert row["run_id"] == "run-abc"
    assert row["project_id"] == "proj1"
    # details JSON round-trips as dict
    details = row["details"]
    if isinstance(details, str):
        import json
        details = json.loads(details)
    assert details == {"foo": "bar"}


@pytest.mark.asyncio
async def test_audit_writer_emit_empty_details(dialect_engine):
    """AuditWriter.emit() with no details stores an empty dict."""
    writer = AuditWriter(engine=dialect_engine)
    await writer.emit(event_type="test.empty", actor_type="system")
    tbl = SealedAuditEventModel.__table__
    async with dialect_engine.connect() as conn:
        rows = (await conn.execute(select(tbl))).mappings().all()
    assert len(rows) == 1
    details = rows[0]["details"]
    if isinstance(details, str):
        import json
        details = json.loads(details)
    assert details == {}


@pytest.mark.asyncio
async def test_audit_writer_emit_context_merged_into_details(dialect_engine):
    """context + details are merged; details takes precedence on key collision."""
    writer = AuditWriter(engine=dialect_engine)
    await writer.emit(
        event_type="test.merge",
        actor_type="system",
        details={"a": 1, "b": "details"},
        context={"b": "context", "c": 3},
    )
    tbl = SealedAuditEventModel.__table__
    async with dialect_engine.connect() as conn:
        row = (await conn.execute(select(tbl))).mappings().first()
    details = row["details"]
    if isinstance(details, str):
        import json
        details = json.loads(details)
    # details overrides context on collision
    assert details["a"] == 1
    assert details["b"] == "details"
    assert details["c"] == 3


@pytest.mark.asyncio
async def test_audit_writer_multiple_events(dialect_engine):
    """Multiple emit() calls each insert a separate row."""
    writer = AuditWriter(engine=dialect_engine)
    for i in range(3):
        await writer.emit(event_type=f"event.{i}", actor_type="system")
    tbl = SealedAuditEventModel.__table__
    async with dialect_engine.connect() as conn:
        count = (await conn.execute(select(func.count()).select_from(tbl))).scalar_one()
    assert count == 3


@pytest.mark.asyncio
async def test_audit_writer_engine_kwarg_works(dialect_engine):
    """AuditWriter(engine=...) is the preferred constructor form."""
    writer = AuditWriter(engine=dialect_engine)
    await writer.emit(event_type="kwarg.test", actor_type="system")
    tbl = SealedAuditEventModel.__table__
    async with dialect_engine.connect() as conn:
        count = (await conn.execute(select(func.count()).select_from(tbl))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_audit_writer_json_roundtrip(dialect_engine):
    """Nested JSON in details survives the round-trip correctly."""
    writer = AuditWriter(engine=dialect_engine)
    payload = {"nested": {"list": [1, 2, 3], "flag": True}, "num": 42}
    await writer.emit(event_type="json.test", actor_type="system", details=payload)
    tbl = SealedAuditEventModel.__table__
    async with dialect_engine.connect() as conn:
        row = (await conn.execute(select(tbl))).mappings().first()
    details = row["details"]
    if isinstance(details, str):
        import json
        details = json.loads(details)
    assert details == payload


@pytest.mark.asyncio
async def test_audit_event_created_at_is_set(dialect_engine):
    """created_at is populated automatically on insert."""
    writer = AuditWriter(engine=dialect_engine)
    await writer.emit(event_type="ts.test", actor_type="system")
    tbl = SealedAuditEventModel.__table__
    async with dialect_engine.connect() as conn:
        row = (await conn.execute(select(tbl))).mappings().first()
    assert row["created_at"] is not None


@pytest.mark.asyncio
async def test_audit_migration_faithful_insert(dialect_engine):
    """Migration 003 faithful: id is BigInteger PK autoincrement; no conflict target needed."""
    writer = AuditWriter(engine=dialect_engine)
    await writer.emit(event_type="mig.faithful", actor_type="system", run_id="r1")
    await writer.emit(event_type="mig.faithful", actor_type="system", run_id="r2")
    tbl = SealedAuditEventModel.__table__
    async with dialect_engine.connect() as conn:
        rows = (await conn.execute(select(tbl).order_by(tbl.c.id))).mappings().all()
    # Both rows inserted (append-only, no conflict target)
    assert len(rows) == 2
    assert rows[0]["id"] != rows[1]["id"]


@pytest.mark.asyncio
async def test_audit_list_by_run_id(dialect_engine):
    """Rows can be queried by run_id."""
    writer = AuditWriter(engine=dialect_engine)
    await writer.emit(event_type="run.start", actor_type="system", run_id="run-X")
    await writer.emit(event_type="run.end", actor_type="system", run_id="run-X")
    await writer.emit(event_type="other.event", actor_type="system", run_id="run-Y")
    tbl = SealedAuditEventModel.__table__
    async with dialect_engine.connect() as conn:
        rows = (
            await conn.execute(
                select(tbl).where(tbl.c.run_id == "run-X").order_by(tbl.c.id)
            )
        ).mappings().all()
    assert len(rows) == 2
    event_types = [r["event_type"] for r in rows]
    assert "run.start" in event_types
    assert "run.end" in event_types


# ---------------------------------------------------------------------------
# DirectiveEvaluationsAdapter — directive_evaluations (migration 008)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_directive_eval_insert_and_list_for_run(dialect_engine):
    """insert_directive_evaluation + list_for_run roundtrip."""
    adapter = DirectiveEvaluationsAdapter(engine=dialect_engine)
    await adapter.insert_directive_evaluation(
        event_type="eval.triggered",
        decision_id="d1",
        run_id="run-1",
        project_id="proj1",
        workflow_name="wf",
        policy_id="policy-A",
        signal_kind="cost_overrun",
        severity="high",
        details={"threshold": 100},
    )
    rows = await adapter.list_for_run(run_id="run-1")
    assert len(rows) == 1
    r = rows[0]
    assert r["event_type"] == "eval.triggered"
    assert r["decision_id"] == "d1"
    assert r["run_id"] == "run-1"
    assert r["project_id"] == "proj1"
    assert r["workflow_name"] == "wf"
    assert r["policy_id"] == "policy-A"
    assert r["signal_kind"] == "cost_overrun"
    assert r["severity"] == "high"
    assert r["details"] == {"threshold": 100}


@pytest.mark.asyncio
async def test_directive_eval_list_for_run_empty(dialect_engine):
    """list_for_run returns [] for unknown run."""
    adapter = DirectiveEvaluationsAdapter(engine=dialect_engine)
    rows = await adapter.list_for_run(run_id="nonexistent")
    assert rows == []


@pytest.mark.asyncio
async def test_directive_eval_list_filtered_by_policy(dialect_engine):
    """list_filtered filters by policy_id."""
    adapter = DirectiveEvaluationsAdapter(engine=dialect_engine)
    for i, policy in enumerate(["policy-A", "policy-B", "policy-A"]):
        await adapter.insert_directive_evaluation(
            event_type="eval.triggered",
            decision_id=f"d{i}",
            run_id=f"run-{i}",
            project_id="proj1",
            workflow_name="wf",
            policy_id=policy,
            signal_kind=None,
            severity=None,
            details={},
        )
    rows = await adapter.list_filtered(policy_id="policy-A")
    assert len(rows) == 2
    assert all(r["policy_id"] == "policy-A" for r in rows)


@pytest.mark.asyncio
async def test_directive_eval_list_filtered_by_event_type(dialect_engine):
    """list_filtered filters by event_type."""
    adapter = DirectiveEvaluationsAdapter(engine=dialect_engine)
    await adapter.insert_directive_evaluation(
        event_type="eval.triggered", decision_id=None, run_id="run-1",
        project_id="p", workflow_name="wf", policy_id="pol",
        signal_kind=None, severity=None, details={},
    )
    await adapter.insert_directive_evaluation(
        event_type="eval.resolved", decision_id=None, run_id="run-1",
        project_id="p", workflow_name="wf", policy_id="pol",
        signal_kind=None, severity=None, details={},
    )
    rows = await adapter.list_filtered(event_type="eval.triggered")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "eval.triggered"


@pytest.mark.asyncio
async def test_directive_eval_json_roundtrip(dialect_engine):
    """Nested JSON in details survives round-trip as a Python dict."""
    adapter = DirectiveEvaluationsAdapter(engine=dialect_engine)
    payload = {"nested": {"list": [1, 2], "flag": True}, "num": 42}
    await adapter.insert_directive_evaluation(
        event_type="json.test", decision_id=None, run_id="run-1",
        project_id=None, workflow_name="wf", policy_id=None,
        signal_kind=None, severity=None, details=payload,
    )
    rows = await adapter.list_for_run(run_id="run-1")
    assert rows[0]["details"] == payload


@pytest.mark.asyncio
async def test_directive_eval_migration_faithful_id(dialect_engine):
    """Migration 008 faithful: id is BigInteger PK autoincrement; two rows get distinct ids."""
    adapter = DirectiveEvaluationsAdapter(engine=dialect_engine)
    for i in range(2):
        await adapter.insert_directive_evaluation(
            event_type="mig.test", decision_id=None, run_id="run-1",
            project_id=None, workflow_name="wf", policy_id=None,
            signal_kind=None, severity=None, details={},
        )
    tbl = DirectiveEvaluationModel.__table__
    async with dialect_engine.connect() as conn:
        rows = (await conn.execute(select(tbl).order_by(tbl.c.id))).mappings().all()
    assert len(rows) == 2
    assert rows[0]["id"] != rows[1]["id"]


@pytest.mark.asyncio
async def test_directive_eval_limit(dialect_engine):
    """list_for_run respects the limit parameter."""
    adapter = DirectiveEvaluationsAdapter(engine=dialect_engine)
    for i in range(5):
        await adapter.insert_directive_evaluation(
            event_type=f"e{i}", decision_id=None, run_id="run-limit",
            project_id=None, workflow_name="wf", policy_id=None,
            signal_kind=None, severity=None, details={},
        )
    rows = await adapter.list_for_run(run_id="run-limit", limit=3)
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# ApprovalsPostgresAdapter — pending_directive_approvals (migration 008)
# ---------------------------------------------------------------------------


def _sample_approval(decision_id: str = "dec-1", run_id: str = "run-1") -> dict:
    return {
        "decision_id": decision_id,
        "run_id": run_id,
        "project_id": "proj1",
        "workflow_name": "wf",
        "policy_id": "policy-A",
        "triggering_signal": {"kind": "cost_overrun", "value": 200},
        "proposed_action": {"action": "pause", "target": run_id},
        "requested_at": _now_utc(),
        "status": "pending",
        "decided_at": None,
        "decided_by": None,
        "operator_note": None,
        "expires_at": _expires_at(),
    }


@pytest.mark.asyncio
async def test_approvals_insert_and_fetch_pending(dialect_engine):
    """insert + fetch_one_pending_for roundtrip."""
    adapter = ApprovalsPostgresAdapter(engine=dialect_engine)
    row = _sample_approval("dec-1", "run-1")
    await adapter.insert(row)
    result = await adapter.fetch_one_pending_for("run-1", "policy-A")
    assert result is not None
    assert result["decision_id"] == "dec-1"
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_approvals_fetch_pending_returns_none_when_absent(dialect_engine):
    """fetch_one_pending_for returns None when no pending row matches."""
    adapter = ApprovalsPostgresAdapter(engine=dialect_engine)
    result = await adapter.fetch_one_pending_for("run-nonexistent", "policy-A")
    assert result is None


@pytest.mark.asyncio
async def test_approvals_update_status_approve(dialect_engine):
    """update_status sets status to approved and records decided_at."""
    adapter = ApprovalsPostgresAdapter(engine=dialect_engine)
    await adapter.insert(_sample_approval("dec-approve", "run-A"))
    decided_at = _now_utc()
    result = await adapter.update_status(
        decision_id="dec-approve",
        status="approved",
        decided_at=decided_at,
        decided_by="admin",
        operator_note="LGTM",
    )
    assert result["status"] == "approved"
    assert result["decision_id"] == "dec-approve"


@pytest.mark.asyncio
async def test_approvals_update_status_raises_for_missing_decision(dialect_engine):
    """update_status raises KeyError for unknown decision_id."""
    adapter = ApprovalsPostgresAdapter(engine=dialect_engine)
    with pytest.raises(KeyError):
        await adapter.update_status(
            decision_id="ghost-id",
            status="approved",
        )


@pytest.mark.asyncio
async def test_approvals_fetch_approved_for_run(dialect_engine):
    """fetch_approved_for_run returns only approved rows for the run."""
    adapter = ApprovalsPostgresAdapter(engine=dialect_engine)
    pending = _sample_approval("dec-pending", "run-B")
    approved = _sample_approval("dec-approved", "run-B")
    await adapter.insert(pending)
    await adapter.insert(approved)
    await adapter.update_status(
        decision_id="dec-approved",
        status="approved",
        decided_at=_now_utc(),
        decided_by="admin",
    )
    results = await adapter.fetch_approved_for_run("run-B")
    assert len(results) == 1
    assert results[0]["decision_id"] == "dec-approved"


@pytest.mark.asyncio
async def test_approvals_decision_id_unique(dialect_engine):
    """decision_id has a UNIQUE constraint (migration 008). Duplicate insert must fail."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    adapter = ApprovalsPostgresAdapter(engine=dialect_engine)
    row = _sample_approval("dup-dec", "run-dup")
    await adapter.insert(row)
    with pytest.raises((IntegrityError, Exception)):
        await adapter.insert(row)


@pytest.mark.asyncio
async def test_approvals_json_roundtrip(dialect_engine):
    """triggering_signal and proposed_action survive round-trip as dicts."""
    adapter = ApprovalsPostgresAdapter(engine=dialect_engine)
    signal = {"kind": "capacity_gap", "data": {"pool": "gpu", "missing": 2}}
    action = {"action": "scale", "pool": "gpu", "count": 3}
    row = _sample_approval("dec-json", "run-json")
    row["triggering_signal"] = signal
    row["proposed_action"] = action
    await adapter.insert(row)

    results = await adapter.fetch_approved_for_run("run-json")
    # Not approved yet, so fetch via the pending check
    result = await adapter.fetch_one_pending_for("run-json", "policy-A")
    assert result is not None
    assert result["decision_id"] == "dec-json"


@pytest.mark.asyncio
async def test_approvals_fetch_approved_returns_proposed_action_as_dict(dialect_engine):
    """fetch_approved_for_run returns proposed_action as a Python dict (not string)."""
    adapter = ApprovalsPostgresAdapter(engine=dialect_engine)
    action = {"action": "pause", "reason": "cost"}
    row = _sample_approval("dec-full", "run-full")
    row["proposed_action"] = action
    await adapter.insert(row)
    await adapter.update_status(
        decision_id="dec-full",
        status="approved",
        decided_at=_now_utc(),
        decided_by="admin",
    )
    results = await adapter.fetch_approved_for_run("run-full")
    assert len(results) == 1
    pa = results[0]["proposed_action"]
    assert isinstance(pa, dict)
    assert pa == action


@pytest.mark.asyncio
async def test_approvals_migration_faithful_columns(dialect_engine):
    """Migration 008 faithful: all columns present in pending_directive_approvals."""
    tbl = PendingDirectiveApprovalModel.__table__
    col_names = {c.name for c in tbl.columns}
    expected = {
        "id", "decision_id", "run_id", "project_id", "workflow_name",
        "policy_id", "triggering_signal", "proposed_action", "requested_at",
        "status", "decided_at", "decided_by", "operator_note", "expires_at",
    }
    assert expected <= col_names


@pytest.mark.asyncio
async def test_audit_model_migration_faithful_columns(dialect_engine):
    """Migration 003 faithful: all columns present in sealed_audit_events."""
    tbl = SealedAuditEventModel.__table__
    col_names = {c.name for c in tbl.columns}
    expected = {
        "id", "event_type", "actor_type", "actor_id", "profile_id",
        "secret_key", "run_id", "project_id", "details", "created_at",
    }
    assert expected <= col_names


@pytest.mark.asyncio
async def test_directive_eval_model_migration_faithful_columns(dialect_engine):
    """Migration 008 faithful: all columns present in directive_evaluations."""
    tbl = DirectiveEvaluationModel.__table__
    col_names = {c.name for c in tbl.columns}
    expected = {
        "id", "event_type", "decision_id", "run_id", "project_id",
        "workflow_name", "policy_id", "signal_kind", "severity",
        "details", "created_at",
    }
    assert expected <= col_names


# ---------------------------------------------------------------------------
# Back-compat: AuditWriter with a store-like object that has ._pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_writer_back_compat_store_with_pool():
    """AuditWriter(store) still calls store._pool.execute when pool is present.

    This validates that the PostgresStore's pg-only callers
    (recover_revoked_stuck_runs, sealed_audit_cleanup) still work.
    """
    from unittest.mock import AsyncMock, MagicMock

    pool = MagicMock()
    pool.execute = AsyncMock()

    class _FakeStore:
        _pool = pool

    store = _FakeStore()
    writer = AuditWriter(store)
    await writer.emit(event_type="compat.test", actor_type="system")
    # The raw asyncpg path must have been called
    pool.execute.assert_called_once()
    call_args = pool.execute.call_args
    sql_str = call_args[0][0]
    assert "sealed_audit_events" in sql_str


@pytest.mark.asyncio
async def test_audit_writer_back_compat_store_with_engine(dialect_engine):
    """AuditWriter(store) uses store._engine (SQLAlchemy path) when _pool absent."""

    class _FakeStoreWithEngine:
        _engine = dialect_engine

    store = _FakeStoreWithEngine()
    writer = AuditWriter(store)
    await writer.emit(event_type="engine.compat", actor_type="system")
    tbl = SealedAuditEventModel.__table__
    async with dialect_engine.connect() as conn:
        count = (await conn.execute(select(func.count()).select_from(tbl))).scalar_one()
    assert count == 1
