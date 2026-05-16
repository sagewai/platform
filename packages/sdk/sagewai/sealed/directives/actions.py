# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Action runtime — actions.dispatch routes typed DirectiveActions.

See spec §4.3.
"""
from __future__ import annotations

from typing import Any, Protocol

from sagewai.core.state import ExecutionMode
from sagewai.sealed.directives.models import (
    AbortRun,
    AlertOperator,
    DirectiveChainEntry,
    DirectiveDecision,
    PromoteRunMode,
    RestartWithFreshIdentity,
)


class InvalidPromotionError(ValueError):
    """Raised when PromoteRunMode.target_mode is not greater than current_mode."""


_MODE_ORDER = {
    ExecutionMode.BARE: 0,
    ExecutionMode.SANDBOXED: 1,
    ExecutionMode.IDENTITY: 2,
    ExecutionMode.FULL: 3,
    ExecutionMode.FULL_JIT: 4,
}


class _StoreLike(Protocol):
    async def load_run(self, run_id: str) -> Any: ...
    async def save_run(self, run: Any) -> None: ...
    async def mark_revoked(self, *, run_id: str, reason: str) -> None: ...
    async def enqueue_replay(self, **kwargs: Any) -> Any: ...


class _NotificationsLike(Protocol):
    async def insert(self, **kwargs: Any) -> None: ...


class _AuditLike(Protocol):
    async def emit(self, **kwargs: Any) -> None: ...


def _audit_base(decision: DirectiveDecision, event_type: str) -> dict[str, Any]:
    sig = decision.triggering_signal
    return {
        "event_type": event_type,
        "decision_id": decision.decision_id,
        "run_id": sig.run_id,
        "project_id": sig.project_id,
        "workflow_name": sig.workflow_name,
        "policy_id": decision.directive_policy_id,
        "signal_kind": sig.kind,
        "severity": sig.severity,
    }


async def dispatch(
    *,
    decision: DirectiveDecision,
    store: _StoreLike,
    audit: _AuditLike,
    notifications: _NotificationsLike,
) -> None:
    """Route a DirectiveDecision to its concrete effect.

    Dispatches to one of four handlers based on the action kind:
    - AbortRun → mark_revoked + chain entry
    - PromoteRunMode → validate mode order, mark_revoked, enqueue_replay, bidirectional chain
    - RestartWithFreshIdentity → mark_revoked, enqueue_replay (same mode), bidirectional chain
    - AlertOperator → insert notification, no run-state change
    """
    action = decision.action
    if isinstance(action, AbortRun):
        await _abort(decision, action, store, audit)
    elif isinstance(action, PromoteRunMode):
        await _promote(decision, action, store, audit)
    elif isinstance(action, RestartWithFreshIdentity):
        await _restart_fresh_identity(decision, action, store, audit)
    elif isinstance(action, AlertOperator):
        await _alert(decision, action, notifications, audit)
    else:
        raise TypeError(f"Unknown DirectiveAction: {action!r}")


async def _abort(decision: DirectiveDecision, action: AbortRun, store: _StoreLike, audit: _AuditLike) -> None:
    await store.mark_revoked(run_id=action.run_id, reason=action.reason)
    run = await store.load_run(action.run_id)
    entry = DirectiveChainEntry(
        decision_id=decision.decision_id,
        direction="caused_abort",
        counterpart_run_id=None,
        action_kind="abort_run",
        decided_at=decision.decided_at,
        target_mode=None,
        reason=action.reason,
    )
    run.directive_chain = list(run.directive_chain) + [entry]
    await store.save_run(run)
    await audit.emit(
        **_audit_base(decision, "directive.fired"),
        details={"action_kind": "abort_run", "reason": action.reason},
    )


async def _promote(decision: DirectiveDecision, action: PromoteRunMode, store: _StoreLike, audit: _AuditLike) -> None:
    original = await store.load_run(action.run_id)
    if _MODE_ORDER[action.target_mode] <= _MODE_ORDER[original.execution_mode]:
        raise InvalidPromotionError(
            f"target_mode {action.target_mode} not greater than "
            f"current {original.execution_mode}"
        )
    await store.mark_revoked(run_id=action.run_id, reason=action.reason)
    new_run = await store.enqueue_replay(
        original_run_id=action.run_id,
        from_step=action.resume_from_step_index,
        execution_mode_override=action.target_mode,
        identity_from="current_cascade",
        security_profile_ref=action.security_profile_ref,
    )
    # Re-load both ends after persistence so chain entries see latest state.
    original = await store.load_run(action.run_id)
    new_run = await store.load_run(new_run.run_id)
    decided_at = decision.decided_at
    original.directive_chain = list(original.directive_chain) + [
        DirectiveChainEntry(
            decision_id=decision.decision_id,
            direction="caused_abort",
            counterpart_run_id=new_run.run_id,
            action_kind="promote_run_mode",
            decided_at=decided_at,
            target_mode=action.target_mode,
            reason=action.reason,
        )
    ]
    new_run.directive_chain = list(new_run.directive_chain) + [
        DirectiveChainEntry(
            decision_id=decision.decision_id,
            direction="caused_replay",
            counterpart_run_id=action.run_id,
            action_kind="promote_run_mode",
            decided_at=decided_at,
            target_mode=action.target_mode,
            reason=action.reason,
        )
    ]
    await store.save_run(original)
    await store.save_run(new_run)
    await audit.emit(
        **_audit_base(decision, "directive.fired"),
        details={
            "action_kind": "promote_run_mode",
            "new_run_id": new_run.run_id,
            "target_mode": action.target_mode.value,
            "reason": action.reason,
        },
    )


async def _restart_fresh_identity(
    decision: DirectiveDecision,
    action: RestartWithFreshIdentity,
    store: _StoreLike,
    audit: _AuditLike,
) -> None:
    original = await store.load_run(action.run_id)
    await store.mark_revoked(run_id=action.run_id, reason=action.reason)
    new_run = await store.enqueue_replay(
        original_run_id=action.run_id,
        from_step=action.resume_from_step_index,
        execution_mode_override=None,  # same mode as original
        identity_from="current_cascade",
        security_profile_ref=original.security_profile_ref,
    )
    # Re-load both ends after persistence.
    original = await store.load_run(action.run_id)
    new_run = await store.load_run(new_run.run_id)
    decided_at = decision.decided_at
    original.directive_chain = list(original.directive_chain) + [
        DirectiveChainEntry(
            decision_id=decision.decision_id,
            direction="caused_abort",
            counterpart_run_id=new_run.run_id,
            action_kind="restart_with_fresh_identity",
            decided_at=decided_at,
            target_mode=None,
            reason=action.reason,
        )
    ]
    new_run.directive_chain = list(new_run.directive_chain) + [
        DirectiveChainEntry(
            decision_id=decision.decision_id,
            direction="caused_replay",
            counterpart_run_id=action.run_id,
            action_kind="restart_with_fresh_identity",
            decided_at=decided_at,
            target_mode=None,
            reason=action.reason,
        )
    ]
    await store.save_run(original)
    await store.save_run(new_run)
    await audit.emit(
        **_audit_base(decision, "directive.fired"),
        details={
            "action_kind": "restart_with_fresh_identity",
            "new_run_id": new_run.run_id,
            "reason": action.reason,
        },
    )


async def _alert(
    decision: DirectiveDecision,
    action: AlertOperator,
    notifications: _NotificationsLike,
    audit: _AuditLike,
) -> None:
    await notifications.insert(
        run_id=action.run_id,
        severity=action.severity,
        message=action.message,
        source="sealed.directives",
    )
    await audit.emit(
        **_audit_base(decision, "directive.alert_emitted"),
        details={
            "action_kind": "alert_operator",
            "severity": action.severity,
            "message": action.message,
        },
    )
