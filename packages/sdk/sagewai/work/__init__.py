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
from sagewai.work.contract import WorkContract
from sagewai.work.control import (
    ControlCheck,
    ControlCheckContext,
    ControlCheckResult,
    ControlDegradedError,
    OperatorController,
    active_control_precondition_ids,
)
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import (
    Action,
    ActionIntent,
    ActionRequest,
    ActionResult,
    ActionScope,
    Assumption,
    ClaimClassification,
    ControlPrecondition,
    ControlPreconditionKind,
    GateDecision,
    OperatorDisciplineReport,
    PendingAttention,
    PendingAttentionKind,
    Reversibility,
    ReviewFinding,
    ReviewResult,
    TaskCapsule,
    VerificationResult,
    WorkItem,
    WorkRecord,
)
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
    "ActionRequest",
    "ActionIntent",
    "ActionResult",
    "ActionScope",
    "CapabilityGrant",
    "Assumption",
    "CapabilitySet",
    "ClaimClassification",
    "ClaudeRuntime",
    "CodexRuntime",
    "ControlPrecondition",
    "ControlPreconditionKind",
    "ControlCheck",
    "ControlCheckContext",
    "ControlCheckResult",
    "ControlDegradedError",
    "GateDecision",
    "PendingAttention",
    "PendingAttentionKind",
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
    "VerificationResult",
    "WorkEventType",
    "WorkItem",
    "WorkRecord",
    "WorkRequest",
    "WorkStore",
    "Workspace",
    "active_control_precondition_ids",
]
