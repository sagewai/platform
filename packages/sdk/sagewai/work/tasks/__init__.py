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

from sagewai.work.tasks.assessment import TaskAssessmentResult, merge_assessment
from sagewai.work.tasks.assessor import TaskAssessor
from sagewai.work.tasks.budget import (
    BudgetLedger,
    MeteredOperatorController,
    budget_breach,
    budget_used_from,
)
from sagewai.work.tasks.coordinator import TaskCoordinator
from sagewai.work.tasks.decide import (
    AssessCycle,
    BlockCycle,
    CompleteCycle,
    ExhaustBudget,
    MirrorAttention,
    RecordStepOutcome,
    Replan,
    ResumeStep,
    RollbackWork,
    RunPlanning,
    StartCycle,
    StartStep,
    StepWorkState,
    SupersedeStep,
    decide,
    fold_cycle,
)
from sagewai.work.tasks.events import (
    TaskEvent,
    TaskEventType,
    board_column,
    derive_attention,
    fold_record,
)
from sagewai.work.tasks.feed import FeedBus, FeedEntry
from sagewai.work.tasks.health import evaluate_health
from sagewai.work.tasks.inbox import DecisionItem, decision_inbox
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
    SpendTotals,
    Task,
    TaskDefaults,
    TaskKind,
    TaskOrigin,
    TaskRecord,
    TaskStatus,
    TaskTriggerSpec,
)
from sagewai.work.tasks.plan import (
    AcceptedPlan,
    MatrixItem,
    PlanRejectedError,
    PlanStep,
    TaskPlanResult,
    accept_plan,
    plan_from_events,
)
from sagewai.work.tasks.planner import PlanningFailedError, TaskPlanner
from sagewai.work.tasks.runner import TaskCoordinatorRunner
from sagewai.work.tasks.schedule import next_fire, preset_to_cron, validate_cron, validate_timezone
from sagewai.work.tasks.scratch import (
    ScratchResultValidator,
    ScratchWorkspace,
    ScratchWorkspaceManager,
)
from sagewai.work.tasks.service import (
    ClarificationDeadlines,
    TaskCreationError,
    TaskDecisionError,
    TaskNotFoundError,
    TaskService,
)
from sagewai.work.tasks.store import SpendReservation, StaleTaskError, TaskStore
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
from sagewai.work.tasks.views import (
    ActionRecordView,
    ThreadEntry,
    ThreadView,
    actions_from_events,
    referenced_artifacts,
    task_work_ids,
    thread_from_events,
)
from sagewai.work.tasks.writer import TaskWriter, status_entry

__all__ = [
    "AcceptedPlan", "ActionRecordView", "AssessCycle", "AttentionHistoryEntry", "Authority",
    "BlockCycle", "Budget", "BudgetLedger", "BudgetUsed", "BurnSeriesPoint", "CATALOGUE",
    "ClarificationDeadlines", "ClarificationQuestion", "CompleteCycle", "CycleTelemetry",
    "DecisionItem", "ExecutionRoute", "ExhaustBudget", "FeedBus", "FeedEntry", "GateMode",
    "HarnessTier", "IllegalTransitionError", "IntakeResult", "MatrixItem",
    "MeteredOperatorController", "MirrorAttention", "PlanRejectedError", "PlanStep",
    "PlanningFailedError", "ProjectTelemetry",
    "RecordStepOutcome", "Replan", "ReportTarget", "ResumeStep", "RoleAlias", "RollbackWork",
    "RoutingPolicy", "RunPlanning", "RuntimeRef", "Schedule", "ScheduledCycleTelemetry",
    "ScheduledTelemetry", "ScratchResultValidator", "ScratchWorkspace", "ScratchWorkspaceManager",
    "Sensitivity", "Sink", "SlotSpec", "SlotValidationError", "SoftwareTarget", "SpendReservation",
    "SpendTotals", "StageAttemptTelemetry", "StageTimelineEntry", "StaleTaskError", "StartCycle",
    "StartStep", "StepWorkState", "SupersedeStep", "Task", "TaskAssessmentResult", "TaskAssessor",
    "TaskCoordinator", "TaskCoordinatorRunner", "TaskCreationError", "TaskDecisionError",
    "TaskDefaults", "TaskEvent", "TaskEventType", "TaskKind", "TaskNotFoundError", "TaskOrigin",
    "TaskPlanResult", "TaskPlanner", "TaskRecord", "TaskService", "TaskStatus", "TaskStore",
    "TaskTelemetry", "TaskTemplate", "TaskTriggerSpec", "TaskWriter", "ThreadEntry", "ThreadView",
    "VerificationRunTelemetry", "WorkTelemetry", "accept_plan", "actions_from_events",
    "assert_transition", "board_column", "budget_breach", "budget_used_from", "decide",
    "decision_inbox", "derive_attention", "derive_task_telemetry", "evaluate_health",
    "fold_cycle", "fold_record", "get_template", "merge_assessment", "next_fire",
    "plan_from_events", "preset_to_cron", "referenced_artifacts", "route", "status_entry",
    "task_work_ids", "thread_from_events", "validate_cron", "validate_slots", "validate_timezone",
]
