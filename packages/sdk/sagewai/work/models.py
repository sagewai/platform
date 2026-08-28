# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Generic Work-domain models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sagewai.work.contract import WorkContract
from sagewai.work.knowledge.models import KnowledgeItem


class ClaimClassification(str, Enum):
    """Grounding classification for material operator claims."""

    FACT = "FACT"
    REQUIREMENT = "REQUIREMENT"
    INFERENCE = "INFERENCE"
    DECISION = "DECISION"
    UNKNOWN = "UNKNOWN"


class Assumption(BaseModel):
    """An explicit unresolved condition carried by a WorkItem."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    statement: str
    kind: str
    evidence_refs: tuple[str, ...] = ()
    confidence: Literal["low", "medium", "high"]
    impact_if_wrong: Literal["low", "medium", "high"]
    status: Literal["open", "validated", "rejected", "accepted_risk"]


class ClassifiedClaim(BaseModel):
    """One material analysis claim with explicit grounding status."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    classification: ClaimClassification
    statement: str
    kind: str
    evidence_refs: tuple[str, ...] = ()
    confidence: Literal["low", "medium", "high"]
    impact_if_wrong: Literal["low", "medium", "high"]


class WorkContractProposal(BaseModel):
    """Operator-proposed contract fields awaiting deterministic acceptance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str
    allowed_scope: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    constraints: tuple[str, ...]
    non_goals: tuple[str, ...]
    risk: Literal["low", "medium", "high"]
    design_required: bool


class WorkAnalysisResult(BaseModel):
    """Structured output of the Work analysis stage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: str
    proposal: WorkContractProposal
    claims: tuple[ClassifiedClaim, ...]


class Reversibility(str, Enum):
    """How a material action can be undone."""

    PURE = "pure"
    SNAPSHOT_REVERSIBLE = "snapshot_reversible"
    COMPENSATABLE = "compensatable"
    IRREVERSIBLE = "irreversible"


class GateDecision(str, Enum):
    """Policy decision for a material gated action."""

    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class ActionRequest(BaseModel):
    """Policy input for one material external side effect."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    action: str
    work_id: str
    risk: str
    reversibility: Reversibility
    scope: str
    evidence_refs: tuple[str, ...]


class PendingAttentionKind(str, Enum):
    """Canonical operator-attention categories."""

    GATE_REQUESTED = "GATE_REQUESTED"
    WORK_BLOCKED = "WORK_BLOCKED"
    CONTROL_DEGRADED = "CONTROL_DEGRADED"
    PRODUCTION_INCIDENT = "PRODUCTION_INCIDENT"


class PendingAttention(BaseModel):
    """One unresolved item returned by the canonical attention query."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attention_id: str
    project_id: str | None
    work_id: str
    kind: PendingAttentionKind
    source_ref: str | None
    summary: str
    evidence_refs: tuple[str, ...] = ()
    created_at: datetime


class WorkItem(BaseModel):
    """Why a unit of work exists."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str | None
    profile: str
    source: str
    source_ref: str | None
    title: str
    description: str
    target_systems: tuple[str, ...] = ()
    created_at: datetime


class Action(BaseModel):
    """A profile-neutral material action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str | None
    work_id: str
    profile: str
    target_system: str
    capability: str
    scope: dict[str, Any]
    inputs: dict[str, Any]
    expected_effect: str
    reversibility: Reversibility
    preconditions: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()


class ActionResult(BaseModel):
    """Receipt produced by execution of an action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    action_id: str
    status: Literal["succeeded", "failed", "blocked"]
    external_ref: str | None
    evidence_refs: tuple[str, ...]
    started_at: datetime
    completed_at: datetime


class ActionScope(BaseModel):
    """Explicit boundary for a stage or action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str
    allowed_targets: tuple[str, ...]
    forbidden_targets: tuple[str, ...] = ()
    max_files_changed: int | None = None
    max_diff_lines: int | None = None
    allowed_capabilities: tuple[str, ...] = ()


class ActionIntent(BaseModel):
    """Risk and permission decision input for a state-changing action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    action_id: str
    capability: str
    target: str
    expected_effect: str
    scope: dict[str, Any]
    risk: Literal["low", "medium", "high", "critical"]
    reversibility: Reversibility
    required_permission: str
    evidence_refs: tuple[str, ...]


class OperatorDisciplineReport(BaseModel):
    """Deterministic and independent checks for an operator run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    work_id: str
    run_id: str
    unsupported_claims: tuple[str, ...]
    scope_violations: tuple[str, ...]
    permission_violations: tuple[str, ...]
    risk_mismatches: tuple[str, ...]
    unnecessary_changes: tuple[str, ...]
    output_tokens: int | None
    changed_files: int | None
    diff_lines: int | None
    verdict: Literal["pass", "repair", "blocked"]


class ControlPreconditionKind(str, Enum):
    """Condition required for Sagewai to remain in control."""

    AUTHORITY = "authority"
    OBSERVABILITY = "observability"
    WORKSPACE = "workspace"
    REVERSIBILITY = "reversibility"


class ControlPrecondition(BaseModel):
    """A deterministic control check required by stages or capabilities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str | None
    kind: ControlPreconditionKind
    description: str
    check_ref: str
    required_for: tuple[str, ...]


class VerificationResult(BaseModel):
    """Profile-neutral deterministic verification verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: str
    passed: bool
    evidence_refs: tuple[str, ...]
    profile_context: dict[str, Any] = Field(default_factory=dict)


class ReviewFinding(BaseModel):
    """One typed finding from an independent reviewer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: Literal["low", "medium", "high", "critical"]
    claim: str
    evidence_refs: tuple[str, ...]
    required_change: str | None
    profile_context: dict[str, Any] = Field(default_factory=dict)


class ReviewResult(BaseModel):
    """Profile-neutral independent review verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: str
    verdict: Literal["accept", "repair", "blocked"]
    findings: tuple[ReviewFinding, ...]
    evidence_refs: tuple[str, ...] = ()


class TaskCapsule(BaseModel):
    """Bounded, fresh execution context compiled from canonical state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    work_id: str
    stage: str
    work_item: WorkItem
    contract: WorkContract
    knowledge_refs: tuple[str, ...]
    knowledge_items: tuple[KnowledgeItem, ...]
    open_assumption_ids: tuple[str, ...]
    prior_result_refs: tuple[str, ...]
    profile_context: dict[str, Any] = Field(default_factory=dict)


class WorkRecord(BaseModel):
    """Mutable current-state projection derived from Work events."""

    model_config = ConfigDict(extra="forbid")

    work_id: str
    project_id: str | None
    source_ref: str | None
    profile: str
    status: str
    contract_version: int | None = Field(default=None, ge=1)
    active_run_id: str | None
    pending_gate: str | None
    profile_context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
