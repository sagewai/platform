"""Sealed-v core models — round-trip + discriminated-union behaviour."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from sagewai.core.state import ExecutionMode
from sagewai.sealed.directives.models import (
    AbortRun,
    AlertOperator,
    DirectiveAction,
    DirectiveChainEntry,
    DirectiveDecision,
    DirectivePolicy,
    PolicyAction,
    PolicyCondition,
    PromoteRunMode,
    RestartWithFreshIdentity,
    SignalEvent,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def test_signal_event_roundtrip():
    ev = SignalEvent(
        kind="cost_overrun",
        run_id="r-1",
        project_id="p-1",
        workflow_name="wf",
        step_index=2,
        severity="warning",
        detail="x",
        evidence={"actual_cost_usd": 12.4, "estimated_cost_usd": 1.0},
        emitted_at=_now(),
    )
    dumped = ev.model_dump(mode="json")
    revived = SignalEvent.model_validate(dumped)
    assert revived == ev


def test_signal_event_is_frozen():
    ev = SignalEvent(
        kind="cost_overrun",
        run_id="r-1",
        project_id=None,
        workflow_name="wf",
        step_index=0,
        severity="info",
        detail="",
        evidence={},
        emitted_at=_now(),
    )
    with pytest.raises(ValidationError):
        ev.kind = "other"  # type: ignore[misc]


def test_action_discriminator_dispatches_correctly():
    abort = AbortRun(run_id="r-1", reason="cost")
    promote = PromoteRunMode(
        run_id="r-1",
        target_mode=ExecutionMode.IDENTITY,
        resume_from_step_index=2,
        security_profile_ref="customer-db",
        reason="capability gap",
    )
    restart = RestartWithFreshIdentity(
        run_id="r-1", resume_from_step_index=0, reason="rotation"
    )
    alert = AlertOperator(run_id="r-1", message="hello")

    # Sanity: variant tuple matches.
    assert AbortRun in get_args(get_args(DirectiveAction)[0])

    for action in (abort, promote, restart, alert):
        as_json = action.model_dump(mode="json")

        adapter = TypeAdapter(DirectiveAction)
        revived = adapter.validate_python(as_json)
        assert revived == action


def test_policy_action_promote_requires_target_mode():
    """Constructor allows missing target_mode; runtime validates in actions.dispatch."""
    pa = PolicyAction(kind="promote_run_mode")
    assert pa.target_mode is None  # construction-time leniency by design


def test_directive_policy_round_trip():
    p = DirectivePolicy(
        id="cost-overrun-default",
        name="Alert on >5x cost",
        condition=PolicyCondition(signal_kind="cost_overrun"),
        action=PolicyAction(kind="alert_operator", severity="warning",
                            message_template="x"),
    )
    dumped = p.model_dump(mode="json")
    revived = DirectivePolicy.model_validate(dumped)
    assert revived == p


def test_directive_decision_envelope():
    sig = SignalEvent(
        kind="cost_overrun",
        run_id="r-1",
        project_id=None,
        workflow_name="wf",
        step_index=0,
        severity="warning",
        detail="",
        evidence={},
        emitted_at=_now(),
    )
    decision = DirectiveDecision(
        decision_id="dec-1",
        directive_policy_id="cost-overrun-default",
        triggering_signal=sig,
        action=AbortRun(run_id="r-1", reason="cost"),
        requires_approval=True,
        decided_at=_now(),
    )
    dumped = decision.model_dump(mode="json")
    revived = DirectiveDecision.model_validate(dumped)
    assert revived == decision
    assert revived.action.kind == "abort_run"


def test_directive_chain_entry_roundtrip():
    entry = DirectiveChainEntry(
        decision_id="dec-1",
        direction="caused_replay",
        counterpart_run_id="r-old",
        action_kind="promote_run_mode",
        decided_at=_now(),
        target_mode=ExecutionMode.IDENTITY,
        reason="capability gap",
    )
    dumped = entry.model_dump(mode="json")
    revived = DirectiveChainEntry.model_validate(dumped)
    assert revived == entry
