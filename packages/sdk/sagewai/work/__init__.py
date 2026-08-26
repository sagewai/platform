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
    OperatorController,
)
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import (
    Action,
    ActionIntent,
    ActionResult,
    ActionScope,
    ClaimClassification,
    ControlPrecondition,
    ControlPreconditionKind,
    OperatorDisciplineReport,
    Reversibility,
    TaskCapsule,
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
from sagewai.work.software import (
    SoftwareAttemptContext,
    SoftwareCapsuleContext,
    SoftwareContractContext,
    SoftwareResultValidator,
    SoftwareWorkspace,
    SoftwareWorktreeManager,
    WorkspaceStaleError,
)
from sagewai.work.store import WorkStore

__all__ = [
    "Action",
    "ActionIntent",
    "ActionResult",
    "ActionScope",
    "CapabilityGrant",
    "CapabilitySet",
    "ClaimClassification",
    "ClaudeRuntime",
    "CodexRuntime",
    "ControlPrecondition",
    "ControlPreconditionKind",
    "ControlCheck",
    "ControlCheckContext",
    "ControlCheckResult",
    "OperatorDisciplineReport",
    "OperatorResult",
    "OperatorRuntime",
    "OperatorController",
    "Reversibility",
    "SoftwareAttemptContext",
    "SoftwareCapsuleContext",
    "SoftwareContractContext",
    "SoftwareResultValidator",
    "SoftwareWorkspace",
    "SoftwareWorktreeManager",
    "TaskCapsule",
    "TaskCapsuleCompiler",
    "WorkContract",
    "WorkEvent",
    "WorkEventType",
    "WorkItem",
    "WorkRecord",
    "WorkRequest",
    "WorkStore",
    "Workspace",
    "WorkspaceStaleError",
]
