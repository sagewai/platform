# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Public API for the generic Work domain."""

from sagewai.work.capsule import TaskCapsuleCompiler
from sagewai.work.completion import (
    evaluate_completion,
    fold_verification_results,
    validate_criterion_subset,
    validate_verification_result,
)
from sagewai.work.contract import AcceptanceCriterion, WorkContract
from sagewai.work.control import (
    ControlCheck,
    ControlCheckContext,
    ControlCheckResult,
    ControlDegradedError,
    OperatorController,
)
from sagewai.work.events import (
    WorkEvent,
    WorkEventType,
    active_control_precondition_ids,
    execution_attempt_from_events,
)
from sagewai.work.fleet import FleetOperatorRuntime
from sagewai.work.metrics import WorkMetrics
from sagewai.work.models import (
    Action,
    ActionIntent,
    ActionPlan,
    ActionRequest,
    ActionResult,
    ActionScope,
    Assumption,
    ClaimClassification,
    ClassifiedClaim,
    CompletionEvaluation,
    ControlPrecondition,
    ControlPreconditionKind,
    CriterionVerification,
    ExecutionAttempt,
    GateDecision,
    OperatorDisciplineReport,
    PendingAttention,
    PendingAttentionKind,
    ProposedAcceptanceCriterion,
    Reversibility,
    ReviewFinding,
    ReviewResult,
    TaskCapsule,
    VerificationResult,
    WorkAnalysisResult,
    WorkContractProposal,
    WorkDesignResult,
    WorkItem,
    WorkRecord,
)
from sagewai.work.profile import WorkProfile
from sagewai.work.runtime import (
    CapabilityGrant,
    CapabilitySet,
    ClaudeRuntime,
    CodexRuntime,
    OperatorResult,
    OperatorRuntime,
    WorkRequest,
    Workspace,
)
from sagewai.work.store import WorkStore

__all__ = [
    "Action",
    "AcceptanceCriterion",
    "ActionPlan",
    "ActionRequest",
    "ActionIntent",
    "ActionResult",
    "ActionScope",
    "CapabilityGrant",
    "Assumption",
    "CapabilitySet",
    "ClaimClassification",
    "ClassifiedClaim",
    "ClaudeRuntime",
    "CodexRuntime",
    "ControlPrecondition",
    "ControlPreconditionKind",
    "ControlCheck",
    "ControlCheckContext",
    "ControlCheckResult",
    "ControlDegradedError",
    "CompletionEvaluation",
    "CriterionVerification",
    "FleetOperatorRuntime",
    "ExecutionAttempt",
    "GateDecision",
    "PendingAttention",
    "PendingAttentionKind",
    "ProposedAcceptanceCriterion",
    "OperatorDisciplineReport",
    "OperatorResult",
    "OperatorRuntime",
    "OperatorController",
    "Reversibility",
    "TaskCapsule",
    "ReviewFinding",
    "ReviewResult",
    "TaskCapsuleCompiler",
    "WorkContract",
    "WorkEvent",
    "WorkMetrics",
    "VerificationResult",
    "WorkAnalysisResult",
    "WorkContractProposal",
    "WorkDesignResult",
    "WorkEventType",
    "WorkItem",
    "WorkRecord",
    "WorkRequest",
    "WorkProfile",
    "WorkStore",
    "Workspace",
    "active_control_precondition_ids",
    "execution_attempt_from_events",
    "evaluate_completion",
    "fold_verification_results",
    "validate_criterion_subset",
    "validate_verification_result",
]
