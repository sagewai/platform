# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""DirectiveEvaluator — match SignalEvents against DirectivePolicies.

See spec §6.1, §5.1.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sagewai.sealed.directives.models import (
    AbortRun,
    AlertOperator,
    DirectiveAction,
    DirectiveDecision,
    DirectivePolicy,
    PolicyAction,
    PromoteRunMode,
    RestartWithFreshIdentity,
    SignalEvent,
)

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def _severity_at_least(observed: str, floor: str | None) -> bool:
    if floor is None:
        return True
    return _SEVERITY_ORDER[observed] >= _SEVERITY_ORDER[floor]


def _evidence_match(observed: dict[str, Any], match: dict[str, Any]) -> bool:
    """Simple matcher: equality, or {"$gt"/"$gte"/"$lt"/"$lte": N} for numbers."""
    for key, expected in match.items():
        actual = observed.get(key)
        if isinstance(expected, dict):
            for op, threshold in expected.items():
                if actual is None:
                    return False
                if op == "$gt" and not (actual > threshold):
                    return False
                if op == "$gte" and not (actual >= threshold):
                    return False
                if op == "$lt" and not (actual < threshold):
                    return False
                if op == "$lte" and not (actual <= threshold):
                    return False
        else:
            if actual != expected:
                return False
    return True


def _build_action(
    *,
    pa: PolicyAction,
    sig: SignalEvent,
) -> DirectiveAction:
    if pa.kind == "abort_run":
        return AbortRun(run_id=sig.run_id, reason=sig.detail or sig.kind)
    if pa.kind == "alert_operator":
        msg = pa.message_template or f"{sig.kind} on run {sig.run_id}"
        # naive `{evidence.X}` interpolation
        for k, v in sig.evidence.items():
            msg = msg.replace("{evidence." + k + "}", str(v))
        msg = msg.replace("{run_id}", sig.run_id)
        return AlertOperator(run_id=sig.run_id, severity=pa.severity, message=msg)
    if pa.kind == "promote_run_mode":
        if pa.target_mode is None:
            raise ValueError("promote_run_mode requires target_mode in policy")
        profile_ref: str | None = None
        if pa.suggested_profile_field:
            profile_ref = sig.evidence.get(pa.suggested_profile_field)
        return PromoteRunMode(
            run_id=sig.run_id,
            target_mode=pa.target_mode,
            resume_from_step_index=sig.step_index,
            security_profile_ref=profile_ref,
            reason=sig.detail or sig.kind,
        )
    if pa.kind == "restart_with_fresh_identity":
        return RestartWithFreshIdentity(
            run_id=sig.run_id,
            resume_from_step_index=sig.step_index,
            reason=sig.detail or sig.kind,
        )
    raise ValueError(f"Unknown action kind: {pa.kind}")


class DirectiveEvaluator:
    """Stateless evaluator — call evaluate() per poll."""

    def evaluate(
        self,
        *,
        signals: list[SignalEvent],
        policies: list[DirectivePolicy],
    ) -> list[DirectiveDecision]:
        decisions: list[DirectiveDecision] = []
        # rate-limit tracking: (run_id, policy_id) → count
        fired: dict[tuple[str, str], int] = {}
        now = datetime.now(tz=timezone.utc)
        for sig in signals:
            for pol in policies:
                if pol.condition.signal_kind != sig.kind:
                    continue
                if not _severity_at_least(sig.severity, pol.condition.severity_at_least):
                    continue
                if not _evidence_match(sig.evidence, pol.condition.evidence_match):
                    continue
                rk = (sig.run_id, pol.id)
                if fired.get(rk, 0) >= pol.rate_limit_per_run:
                    continue
                action = _build_action(pa=pol.action, sig=sig)
                decisions.append(
                    DirectiveDecision(
                        decision_id=f"dec-{uuid.uuid4().hex[:12]}",
                        directive_policy_id=pol.id,
                        triggering_signal=sig,
                        action=action,
                        requires_approval=pol.requires_approval,
                        decided_at=now,
                    )
                )
                fired[rk] = fired.get(rk, 0) + 1
        return decisions
