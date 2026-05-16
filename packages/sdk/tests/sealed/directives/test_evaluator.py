# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""DirectiveEvaluator — match signals against policies, produce decisions."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sealed.directives.evaluator import DirectiveEvaluator
from sagewai.sealed.directives.models import (
    DirectivePolicy,
    PolicyAction,
    PolicyCondition,
    SignalEvent,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _signal(
    *,
    kind: str = "cost_overrun",
    severity: str = "warning",
    evidence: dict | None = None,
    run_id: str = "r-1",
) -> SignalEvent:
    return SignalEvent(
        kind=kind, run_id=run_id, project_id=None, workflow_name="wf",
        step_index=0, severity=severity, detail="", evidence=evidence or {},
        emitted_at=_now(),
    )


def _alert_policy(id: str = "alert", *, condition: PolicyCondition) -> DirectivePolicy:
    return DirectivePolicy(
        id=id, name=id,
        condition=condition,
        action=PolicyAction(kind="alert_operator", severity="warning"),
    )


def test_evaluator_matches_kind_and_returns_decision():
    pol = _alert_policy(condition=PolicyCondition(signal_kind="cost_overrun"))
    ev = DirectiveEvaluator()
    decisions = ev.evaluate(signals=[_signal()], policies=[pol])
    assert len(decisions) == 1
    assert decisions[0].directive_policy_id == "alert"
    assert decisions[0].action.kind == "alert_operator"


def test_evaluator_skips_signals_below_severity_floor():
    pol = _alert_policy(
        condition=PolicyCondition(
            signal_kind="cost_overrun", severity_at_least="critical",
        )
    )
    ev = DirectiveEvaluator()
    decisions = ev.evaluate(signals=[_signal(severity="warning")], policies=[pol])
    assert decisions == []


def test_evaluator_evidence_match_gt_operator():
    pol = _alert_policy(
        condition=PolicyCondition(
            signal_kind="cost_overrun",
            evidence_match={"actual_cost_usd": {"$gt": 100.0}},
        )
    )
    ev = DirectiveEvaluator()
    low = _signal(evidence={"actual_cost_usd": 50.0})
    high = _signal(evidence={"actual_cost_usd": 150.0})
    assert ev.evaluate(signals=[low], policies=[pol]) == []
    out = ev.evaluate(signals=[high], policies=[pol])
    assert len(out) == 1


def test_evaluator_rate_limits_per_run_per_policy():
    pol = _alert_policy(condition=PolicyCondition(signal_kind="cost_overrun"))
    ev = DirectiveEvaluator()
    sigs = [_signal(), _signal()]
    decisions = ev.evaluate(signals=sigs, policies=[pol])
    assert len(decisions) == 1  # second signal dropped by rate_limit_per_run=1


def test_evaluator_promote_action_carries_target_mode_and_profile():
    pol = DirectivePolicy(
        id="promote",
        name="promote",
        condition=PolicyCondition(signal_kind="capability_gap"),
        action=PolicyAction(
            kind="promote_run_mode",
            target_mode=ExecutionMode.IDENTITY,
            suggested_profile_field="suggested_profile",
        ),
    )
    sig = _signal(
        kind="capability_gap",
        evidence={"suggested_profile": "customer-db", "missing_key": "X"},
    )
    decisions = DirectiveEvaluator().evaluate(signals=[sig], policies=[pol])
    assert len(decisions) == 1
    action = decisions[0].action
    assert action.kind == "promote_run_mode"
    assert action.target_mode is ExecutionMode.IDENTITY
    assert action.security_profile_ref == "customer-db"


def test_evaluator_requires_approval_passthrough():
    pol = DirectivePolicy(
        id="abort_high",
        name="abort_high",
        condition=PolicyCondition(signal_kind="cost_overrun"),
        action=PolicyAction(kind="abort_run"),
        requires_approval=True,
    )
    decisions = DirectiveEvaluator().evaluate(signals=[_signal()], policies=[pol])
    assert decisions[0].requires_approval is True
