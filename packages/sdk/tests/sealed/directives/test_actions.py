# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Action runtime — actions.dispatch routes by kind."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.core.state import ExecutionMode, WorkflowRun
from sagewai.sealed.directives.actions import (
    InvalidPromotionError,
    dispatch,
)
from sagewai.sealed.directives.models import (
    AbortRun,
    AlertOperator,
    DirectiveDecision,
    PromoteRunMode,
    RestartWithFreshIdentity,
    SignalEvent,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _sig() -> SignalEvent:
    return SignalEvent(
        kind="cost_overrun", run_id="r-1", project_id=None, workflow_name="wf",
        step_index=0, severity="warning", detail="", evidence={}, emitted_at=_now(),
    )


def _decision(action) -> DirectiveDecision:
    return DirectiveDecision(
        decision_id="dec-1",
        directive_policy_id="pol",
        triggering_signal=_sig(),
        action=action,
        requires_approval=False,
        decided_at=_now(),
    )


class _FakeStore:
    def __init__(self) -> None:
        self.run = WorkflowRun(
            workflow_name="wf",
            run_id="r-1",
            execution_mode=ExecutionMode.SANDBOXED,
        )
        self.update_calls: list[dict] = []
        self.replays: list[dict] = []

    async def load_run(self, run_id):
        # Return either the original run (r-1) or a freshly minted replay run.
        if run_id == "r-replay":
            return WorkflowRun(workflow_name="wf", run_id="r-replay")
        return self.run

    async def save_run(self, run):
        if run.run_id == "r-1":
            self.run = run

    async def mark_revoked(self, *, run_id, reason):
        self.update_calls.append({"run_id": run_id, "reason": reason})

    async def enqueue_replay(self, **kwargs):
        self.replays.append(kwargs)
        return WorkflowRun(workflow_name="wf", run_id="r-replay")


class _FakeNotifications:
    def __init__(self) -> None:
        self.alerts: list[dict] = []

    async def insert(self, **kwargs) -> None:
        self.alerts.append(kwargs)


class _FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, **kwargs) -> None:
        self.events.append(kwargs)


@pytest.mark.asyncio
async def test_abort_run_marks_revoked_and_appends_chain():
    store, audit, notif = _FakeStore(), _FakeAudit(), _FakeNotifications()
    decision = _decision(AbortRun(run_id="r-1", reason="cost"))
    await dispatch(decision=decision, store=store, audit=audit, notifications=notif)
    assert store.update_calls == [{"run_id": "r-1", "reason": "cost"}]
    assert any(e["event_type"] == "directive.fired" for e in audit.events)
    assert len(store.run.directive_chain) == 1
    assert store.run.directive_chain[0].direction == "caused_abort"


@pytest.mark.asyncio
async def test_promote_run_mode_with_lower_target_raises():
    store, audit, notif = _FakeStore(), _FakeAudit(), _FakeNotifications()
    store.run = WorkflowRun(workflow_name="wf", run_id="r-1",
                             execution_mode=ExecutionMode.FULL)
    decision = _decision(PromoteRunMode(
        run_id="r-1", target_mode=ExecutionMode.IDENTITY,
        resume_from_step_index=0, security_profile_ref=None, reason="x",
    ))
    with pytest.raises(InvalidPromotionError):
        await dispatch(decision=decision, store=store, audit=audit, notifications=notif)


@pytest.mark.asyncio
async def test_promote_run_mode_enqueues_replay_and_links_chain():
    store, audit, notif = _FakeStore(), _FakeAudit(), _FakeNotifications()
    decision = _decision(PromoteRunMode(
        run_id="r-1", target_mode=ExecutionMode.IDENTITY,
        resume_from_step_index=2, security_profile_ref="customer-db",
        reason="capability gap",
    ))
    await dispatch(decision=decision, store=store, audit=audit, notifications=notif)
    assert len(store.replays) == 1
    assert store.replays[0]["execution_mode_override"] is ExecutionMode.IDENTITY
    assert store.replays[0]["identity_from"] == "current_cascade"
    assert store.replays[0]["security_profile_ref"] == "customer-db"
    assert any(e["event_type"] == "directive.fired" for e in audit.events)


@pytest.mark.asyncio
async def test_restart_with_fresh_identity_uses_same_mode_and_fresh_cascade():
    store, audit, notif = _FakeStore(), _FakeAudit(), _FakeNotifications()
    decision = _decision(RestartWithFreshIdentity(
        run_id="r-1", resume_from_step_index=0, reason="rotation",
    ))
    await dispatch(decision=decision, store=store, audit=audit, notifications=notif)
    assert store.replays[0]["identity_from"] == "current_cascade"
    assert store.replays[0]["execution_mode_override"] is None  # same mode


@pytest.mark.asyncio
async def test_alert_operator_inserts_notification_no_run_change():
    store, audit, notif = _FakeStore(), _FakeAudit(), _FakeNotifications()
    decision = _decision(AlertOperator(
        run_id="r-1", severity="critical", message="cost spiked",
    ))
    await dispatch(decision=decision, store=store, audit=audit, notifications=notif)
    assert store.update_calls == []
    assert len(notif.alerts) == 1
    assert any(e["event_type"] == "directive.alert_emitted" for e in audit.events)
