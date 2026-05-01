# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""PendingApprovalsRegistry — Postgres-backed HITL queue."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sagewai.sealed.directives.approvals import (
    AlreadyDecidedError,
    PendingApprovalsRegistry,
    SuppressedAlreadyPendingError,
)
from sagewai.sealed.directives.models import (
    AbortRun,
    DirectiveDecision,
    SignalEvent,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _decision(decision_id: str = "dec-1", policy_id: str = "pol-1") -> DirectiveDecision:
    return DirectiveDecision(
        decision_id=decision_id,
        directive_policy_id=policy_id,
        triggering_signal=SignalEvent(
            kind="cost_overrun", run_id="r-1", project_id=None, workflow_name="wf",
            step_index=0, severity="warning", detail="", evidence={},
            emitted_at=_now(),
        ),
        action=AbortRun(run_id="r-1", reason="cost"),
        requires_approval=True,
        decided_at=_now(),
    )


class _FakePool:
    """Minimal asyncpg-style pool for unit testing — table-as-list."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def fetch_one_pending_for(self, run_id, policy_id):
        for r in self.rows:
            if (
                r["run_id"] == run_id
                and r["policy_id"] == policy_id
                and r["status"] == "pending"
            ):
                return r
        return None

    async def insert(self, row: dict) -> None:
        for r in self.rows:
            if r["decision_id"] == row["decision_id"]:
                raise ValueError("UNIQUE violation: decision_id")
        self.rows.append(row)

    async def update_status(self, *, decision_id, **fields):
        for r in self.rows:
            if r["decision_id"] == decision_id:
                r.update(fields)
                return r
        raise KeyError(decision_id)

    async def fetch_approved_for_run(self, run_id):
        return [
            r for r in self.rows
            if r["run_id"] == run_id and r["status"] == "approved"
        ]


@pytest.mark.asyncio
async def test_request_inserts_pending_row():
    reg = PendingApprovalsRegistry(_FakePool())
    decision = _decision()
    await reg.request(decision=decision, ttl_seconds=3600)
    pending = await reg.list_pending()
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_request_rejects_duplicate_pending_for_same_run_policy():
    reg = PendingApprovalsRegistry(_FakePool())
    await reg.request(decision=_decision("dec-1"), ttl_seconds=3600)
    with pytest.raises(SuppressedAlreadyPendingError):
        await reg.request(decision=_decision("dec-2"), ttl_seconds=3600)


@pytest.mark.asyncio
async def test_approve_transitions_pending_to_approved():
    reg = PendingApprovalsRegistry(_FakePool())
    await reg.request(decision=_decision(), ttl_seconds=3600)
    await reg.approve(decision_id="dec-1", actor="ops@acme", note="ok")
    pending = await reg.list_pending()
    assert pending == []


@pytest.mark.asyncio
async def test_double_approve_raises():
    reg = PendingApprovalsRegistry(_FakePool())
    await reg.request(decision=_decision(), ttl_seconds=3600)
    await reg.approve(decision_id="dec-1", actor="ops")
    with pytest.raises(AlreadyDecidedError):
        await reg.approve(decision_id="dec-1", actor="ops")


@pytest.mark.asyncio
async def test_expire_marks_overdue_rows():
    pool = _FakePool()
    reg = PendingApprovalsRegistry(pool)
    await reg.request(decision=_decision(), ttl_seconds=1)
    # Simulate clock advance
    pool.rows[0]["expires_at"] = _now() - timedelta(seconds=10)
    expired = await reg.expire_overdue(now=_now())
    assert expired == 1
    assert pool.rows[0]["status"] == "expired"


@pytest.mark.asyncio
async def test_consume_approved_for_run():
    reg = PendingApprovalsRegistry(_FakePool())
    await reg.request(decision=_decision(), ttl_seconds=3600)
    await reg.approve(decision_id="dec-1", actor="ops")
    approved = await reg.list_approved_for_run("r-1")
    assert len(approved) == 1
    await reg.mark_consumed(decision_id="dec-1")
    approved = await reg.list_approved_for_run("r-1")
    assert approved == []
