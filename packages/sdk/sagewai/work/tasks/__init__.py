# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task aggregate: durable coordination above the Work kernel."""

from sagewai.work.tasks.events import (
    TaskEvent,
    TaskEventType,
    board_column,
    derive_attention,
    fold_record,
)
from sagewai.work.tasks.feed import FeedBus, FeedEntry
from sagewai.work.tasks.intake import ClarificationQuestion, IntakeResult, route
from sagewai.work.tasks.models import (
    Authority,
    Budget,
    BudgetUsed,
    ExecutionRoute,
    GateMode,
    HarnessTier,
    ReportTarget,
    RoleAlias,
    RoutingPolicy,
    RuntimeRef,
    Schedule,
    Sensitivity,
    Sink,
    SoftwareTarget,
    Task,
    TaskDefaults,
    TaskKind,
    TaskOrigin,
    TaskRecord,
    TaskStatus,
)
from sagewai.work.tasks.plan import (
    AcceptedPlan,
    MatrixItem,
    PlanRejectedError,
    PlanStep,
    TaskPlanResult,
    accept_plan,
)
from sagewai.work.tasks.planner import PlanningFailedError, TaskPlanner
from sagewai.work.tasks.schedule import next_fire, preset_to_cron, validate_cron, validate_timezone
from sagewai.work.tasks.scratch import (
    ScratchResultValidator,
    ScratchWorkspace,
    ScratchWorkspaceManager,
)
from sagewai.work.tasks.store import SpendReservation, SpendTotals, StaleTaskError, TaskStore
from sagewai.work.tasks.telemetry import (
    AttentionHistoryEntry,
    BurnSeriesPoint,
    CycleTelemetry,
    ProjectTelemetry,
    ScheduledCycleTelemetry,
    ScheduledTelemetry,
    StageAttemptTelemetry,
    StageTimelineEntry,
    TaskTelemetry,
    VerificationRunTelemetry,
    WorkTelemetry,
    derive_task_telemetry,
)
from sagewai.work.tasks.templates import (
    CATALOGUE,
    SlotSpec,
    SlotValidationError,
    TaskTemplate,
    get_template,
    validate_slots,
)
from sagewai.work.tasks.transitions import IllegalTransitionError, assert_transition

__all__ = [
    "AcceptedPlan", "AttentionHistoryEntry", "Authority", "Budget", "BudgetUsed",
    "BurnSeriesPoint", "CATALOGUE", "ClarificationQuestion", "CycleTelemetry", "ExecutionRoute",
    "FeedBus", "FeedEntry", "GateMode", "HarnessTier", "IllegalTransitionError", "IntakeResult",
    "MatrixItem", "PlanRejectedError", "PlanStep", "PlanningFailedError", "ProjectTelemetry",
    "ReportTarget", "RoleAlias", "RoutingPolicy", "RuntimeRef", "Schedule",
    "ScheduledCycleTelemetry", "ScheduledTelemetry", "ScratchResultValidator",
    "ScratchWorkspace", "ScratchWorkspaceManager", "Sensitivity", "Sink", "SlotSpec",
    "SlotValidationError", "SoftwareTarget", "SpendReservation", "SpendTotals",
    "StageAttemptTelemetry", "StageTimelineEntry", "StaleTaskError", "Task", "TaskDefaults",
    "TaskEvent", "TaskEventType", "TaskKind", "TaskOrigin", "TaskPlanResult", "TaskPlanner",
    "TaskRecord", "TaskStatus", "TaskStore", "TaskTelemetry", "TaskTemplate",
    "VerificationRunTelemetry", "WorkTelemetry", "accept_plan", "assert_transition",
    "board_column", "derive_attention", "derive_task_telemetry", "fold_record", "get_template",
    "next_fire", "preset_to_cron", "route", "validate_cron", "validate_slots",
    "validate_timezone",
]
