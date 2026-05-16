# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Sealed-v core models — frozen Pydantic shapes for signals, actions,
policies, decisions, and audit chain entries.

See spec §3.1, §4.1, §4.5, §5.1.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sagewai.core.state import ExecutionMode

# ──────────────────────────────────────────────────────────────────────
# SignalEvent — emitted by SignalSources
# ──────────────────────────────────────────────────────────────────────


class SignalEvent(BaseModel):
    """A single piece of evidence emitted by a SignalSource."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    run_id: str
    project_id: str | None
    workflow_name: str
    step_index: int = Field(ge=0)
    severity: Literal["info", "warning", "critical"]
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    emitted_at: datetime


# ──────────────────────────────────────────────────────────────────────
# DirectiveAction — the four typed actions, discriminated on `kind`
# ──────────────────────────────────────────────────────────────────────


class AbortRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["abort_run"] = "abort_run"
    run_id: str
    reason: str


class PromoteRunMode(BaseModel):
    """Abort current run + enqueue replay at higher mode, resume from step_index.

    Note: target_mode > current_mode is enforced at action-dispatch time
    (see actions.dispatch) since current_mode is a property of the run.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["promote_run_mode"] = "promote_run_mode"
    run_id: str
    target_mode: ExecutionMode
    resume_from_step_index: int = Field(ge=0)
    security_profile_ref: str | None = None
    reason: str


class RestartWithFreshIdentity(BaseModel):
    """Abort + replay with current cascade resolution (not original injection)."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["restart_with_fresh_identity"] = "restart_with_fresh_identity"
    run_id: str
    resume_from_step_index: int = Field(ge=0)
    reason: str


class AlertOperator(BaseModel):
    """Notify, no run-state change."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["alert_operator"] = "alert_operator"
    run_id: str
    severity: Literal["info", "warning", "critical"] = "warning"
    message: str


DirectiveAction = Annotated[
    AbortRun | PromoteRunMode | RestartWithFreshIdentity | AlertOperator,
    Field(discriminator="kind"),
]


# ──────────────────────────────────────────────────────────────────────
# DirectivePolicy — operator-authored policy tree
# ──────────────────────────────────────────────────────────────────────


class PolicyCondition(BaseModel):
    """Condition that matches a SignalEvent."""

    model_config = ConfigDict(frozen=True)

    signal_kind: str
    severity_at_least: Literal["info", "warning", "critical"] | None = None
    evidence_match: dict[str, Any] = Field(default_factory=dict)


class PolicyAction(BaseModel):
    """Action specification — turned into a typed DirectiveAction at fire time."""

    model_config = ConfigDict(frozen=True)

    kind: Literal[
        "abort_run", "promote_run_mode", "restart_with_fresh_identity", "alert_operator"
    ]
    target_mode: ExecutionMode | None = None
    suggested_profile_field: str | None = None
    severity: Literal["info", "warning", "critical"] = "warning"
    message_template: str | None = None


class DirectivePolicy(BaseModel):
    """A single policy. Cascade: system → project → workflow."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str = ""
    enabled: bool = True
    condition: PolicyCondition
    action: PolicyAction
    requires_approval: bool = False
    rate_limit_per_run: int = Field(default=1, ge=1)


# ──────────────────────────────────────────────────────────────────────
# DirectiveDecision — the evaluator's output envelope
# ──────────────────────────────────────────────────────────────────────


class DirectiveDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str
    directive_policy_id: str
    triggering_signal: SignalEvent
    action: DirectiveAction
    requires_approval: bool
    decided_at: datetime


# ──────────────────────────────────────────────────────────────────────
# DirectiveChainEntry — persisted on workflow_runs.directive_chain
# ──────────────────────────────────────────────────────────────────────


class DirectiveChainEntry(BaseModel):
    """Persisted entry on workflow_runs.directive_chain — links a run
    to the directive decision that caused its abort or replay."""

    model_config = ConfigDict(frozen=True)

    decision_id: str
    direction: Literal["caused_abort", "caused_replay"]
    counterpart_run_id: str | None
    action_kind: str
    decided_at: datetime
    target_mode: ExecutionMode | None = None
    reason: str
