# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Drive one leased Task: decide one command, execute it, repeat (spec section 8)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.work.activity import WorkActivityStore
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import (
    SUPERSEDED,
    ActionRequest,
    ActionResult,
    GateDecision,
    PendingAttention,
    Reversibility,
    WorkRecord,
)
from sagewai.work.store import WorkStore
from sagewai.work.supersede import supersede_work
from sagewai.work.tasks.actions import (
    DeliveryReceipt,
    RollbackExecutor,
    RollbackRefusedError,
    deliver_action_id,
    rollback_action,
    rollback_action_id,
)
from sagewai.work.tasks.assessment import TaskAssessmentResult
from sagewai.work.tasks.assessor import AssessmentFailedError
from sagewai.work.tasks.budget import BudgetLedger, budget_used_from
from sagewai.work.tasks.channels import TrackingDecisionChannel
from sagewai.work.tasks.decide import (
    AssessCycle,
    BlockCycle,
    Command,
    CompleteCycle,
    CycleState,
    DeliverReport,
    ExhaustBudget,
    MirrorAttention,
    MirrorGateDecision,
    RecordStepOutcome,
    Replan,
    RequestDeliverGate,
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
from sagewai.work.tasks.decisions import (
    TASK_GATES,
    ConsoleDecisionChannel,
    DecisionChannel,
    DecisionRequest,
    channel_error_detail,
    coordinator_action,
    resolve_gate,
)
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.health import (
    AlertOperator,
    HealthPolicy,
    PauseSchedule,
    cycle_history,
    evaluate_health,
)
from sagewai.work.tasks.models import BudgetUsed, Task, TaskKind, TaskRecord, TaskStatus
from sagewai.work.tasks.plan import (
    AcceptedPlan,
    PlanRejectedError,
    PlanStep,
    TaskPlanResult,
    accept_plan,
)
from sagewai.work.tasks.planner import PlanningFailedError
from sagewai.work.tasks.store import StaleTaskError, TaskStore
from sagewai.work.tasks.writer import Entry, TaskWriter, status_entry

logger = logging.getLogger("sagewai.work.tasks")

_URGENCY = {
    "WORK_BLOCKED": "now",
    "CONTROL_DEGRADED": "now",
    "EXTERNAL_OUTCOME_INCIDENT": "now",
    "GATE_REQUESTED": "today",
}
_DUE_IN = {
    "now": timedelta(0),
    "today": timedelta(hours=24),
    "this_week": timedelta(days=7),
}


def _lost_rollback(project_id: str, action_id: str) -> tuple[ActionResult, dict[str, object]]:
    """A rollback whose receipt was consumed before its batch landed: ask, never repeat."""
    now = datetime.now(timezone.utc)
    return (
        ActionResult(
            project_id=project_id,
            action_id=action_id,
            status="blocked",
            external_ref=None,
            evidence_refs=(),
            started_at=now,
            completed_at=now,
        ),
        {
            "action_id": action_id,
            "check": "rollback_receipt",
            "passed": None,
            "detail": "the rollback may have run before a crash; confirm the outcome on GitHub",
            "evidence_refs": [],
        },
    )


def _lost_delivery(
    project_id: str, action_id: str, action: ActionRequest
) -> tuple[ActionResult, dict[str, object]]:
    """A delivery whose receipt was consumed before its batch landed: ask, never repeat."""
    now = datetime.now(timezone.utc)
    return (
        ActionResult(
            project_id=project_id,
            action_id=action_id,
            status="blocked",
            external_ref=None,
            evidence_refs=action.evidence_refs,
            started_at=now,
            completed_at=now,
        ),
        {
            "action_id": action_id,
            "check": "delivery_receipt",
            "passed": None,
            "detail": "the delivery may have run before a crash; confirm the sink",
            "evidence_refs": list(action.evidence_refs),
        },
    )


def _failed_rollback(
    project_id: str,
    action_id: str,
    detail: str,
    now: datetime,
) -> tuple[ActionResult, dict[str, object]]:
    return (
        ActionResult(
            project_id=project_id,
            action_id=action_id,
            status="failed",
            external_ref=None,
            evidence_refs=(),
            started_at=now,
            completed_at=now,
        ),
        {
            "action_id": action_id,
            "check": "rollback_refused",
            "passed": False,
            "detail": detail,
            "evidence_refs": [],
        },
    )


def _refused_rollback_action_id(work_id: str, action: ActionRequest) -> str:
    if action.rollback == "delete_comment":
        return f"delete_comment:{work_id}:refused"
    if action.rollback == "revert_pull_request":
        return f"revert:{work_id}:refused"
    return f"rollback:{work_id}:refused"


def _rollback_refusal_entries(
    work_id: str, result: ActionResult, observation: dict[str, object]
) -> list[Entry]:
    return [
        (
            TaskEventType.ACTION_RESULT_RECORDED,
            {"work_id": work_id, **result.model_dump(mode="json")},
        ),
        (
            TaskEventType.OBSERVATION_RECORDED,
            {"work_id": work_id, **observation},
        ),
    ]


def _accepted_plan_text(plan: AcceptedPlan) -> str:
    return "\n".join(
        [
            f"Accepted plan v{plan.version}",
            *(f"- {step.id}: {step.title}" for step in plan.steps),
        ]
    )


def _assessment_tracking_text(cycle: int, result: TaskAssessmentResult) -> str:
    lines = [f"Assessment cycle {cycle}: {result.verdict}"]
    lines.extend(f"- {gap.statement} (suggested step: {gap.suggested_step})" for gap in result.gaps)
    return "\n".join(lines)


class ProfileRunner(Protocol):
    """Profile-specific execution the coordinator drives; PR4b adds the report runner."""

    def use_ledger(self, ledger: BudgetLedger) -> None: ...

    async def base_sha(self, task: Task) -> str | None: ...

    async def plan(
        self,
        task: Task,
        *,
        cycle: int,
        plan_version: int,
        base_sha: str | None,
        brief_text: str,
        amendments: tuple[str, ...],
    ) -> TaskPlanResult: ...

    async def find_issue(self, task: Task, *, cycle: int, step: PlanStep) -> str | None: ...

    async def create_issue(self, task: Task, *, cycle: int, step: PlanStep) -> str: ...

    async def find_work(
        self, task: Task, *, issue_url: str, exclude: str | None = None
    ) -> WorkRecord | None: ...

    async def start(
        self,
        task: Task,
        *,
        cycle: int,
        step: PlanStep,
        issue_url: str,
        base_sha: str | None,
        evidence_refs: tuple[str, ...] = (),
    ) -> WorkRecord: ...

    async def resume(self, task: Task, *, cycle: int, work_id: str) -> WorkRecord: ...

    async def is_merged(self, task: Task, *, work_id: str) -> bool: ...

    async def assess(
        self,
        task: Task,
        *,
        cycle: int,
        plan_version: int,
        plan: AcceptedPlan,
        outcomes: Mapping[str, str],
        merged_sha: str | None,
        evidence: tuple[str, ...],
    ) -> TaskAssessmentResult: ...

    async def deliver(
        self, task: Task, *, work_id: str, sink_version: int
    ) -> tuple[WorkRecord, tuple[DeliveryReceipt, ...]]:
        """Deliver to sink_version; receipt results are recorded under the coordinator action id."""
        ...


class TaskCoordinator:
    """One command per decision, each behind a receipt and the Task's lease epoch."""

    _MAX_COMMANDS = 20

    def __init__(
        self,
        *,
        task_store: TaskStore,
        work_store: WorkStore,
        profile_runners: Callable[[Task], ProfileRunner],
        artifact_store: LocalArtifactStore | None = None,
        activity_store: WorkActivityStore | None = None,
        decision_channels: Sequence[DecisionChannel] = (),
        channel_factory: Callable[[str], Awaitable[Sequence[DecisionChannel]]] | None = None,
        rollbacks: RollbackExecutor | None = None,
        actor_ref: str = "coordinator",
    ) -> None:
        self._task_store = task_store
        self._work_store = work_store
        self._profile_runners = profile_runners
        self._artifacts = artifact_store or LocalArtifactStore()
        self._activity_store = activity_store
        self._static_channels = tuple(decision_channels) or (ConsoleDecisionChannel(),)
        self._channel_factory = channel_factory
        self._rollbacks = rollbacks
        self._writer = TaskWriter(task_store, actor_ref=actor_ref)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _profile_for(self, task: Task) -> ProfileRunner:
        return self._profile_runners(task)

    async def _channels(self, task: Task) -> Sequence[DecisionChannel]:
        """Per project, so a configuration change takes effect on the next tick."""
        if self._channel_factory is None:
            return self._static_channels
        return await self._channel_factory(task.project_id)

    @staticmethod
    def _cycle(record: TaskRecord) -> int:
        """The cycle an attempt bills to; planning for cycle 1 runs while current_cycle is 0."""
        return max(record.current_cycle, 1)

    async def _load(self, task_id: str, project_id: str) -> tuple[Task, TaskRecord]:
        loaded = await self._task_store.load(task_id, project_id=project_id)
        if loaded is None:
            raise KeyError(task_id)
        return loaded

    def _meter(self, task: Task, record: TaskRecord) -> BudgetLedger:
        """The ledger this command's attempts bill to; handed to the profile runner."""
        ledger = BudgetLedger(
            store=self._task_store,
            task_id=task.id,
            project_id=task.project_id,
            cycle=self._cycle(record),
            budget=task.budget,
        )
        self._profile_for(task).use_ledger(ledger)
        return ledger

    async def drive(self, record: TaskRecord, *, lease_epoch: int) -> TaskRecord:
        """Run commands until the Task waits, a command makes no progress, or the cap is hit."""
        task, record = await self._load(record.task_id, record.project_id)
        if record.lease_epoch != lease_epoch:
            raise StaleTaskError("lease epoch changed; another coordinator owns this task")
        for _ in range(self._MAX_COMMANDS):
            events = await self._task_store.read_events(task.id, project_id=task.project_id)
            state = fold_cycle(events, plan_version=record.plan_version)
            works = await self._work_states(task, state, record.pending_gate)
            used = await self._budget_used(task, record, events)
            command = decide(task, record, events, works, budget_used=used, now=self._now())
            if command is None:
                return record
            replay = not await self._task_store.record_command(
                task_id=task.id,
                project_id=task.project_id,
                command_id=command.receipt_id(record.revision),
                payload=command.model_dump(mode="json"),
            )
            revision = record.revision
            record = await self._execute(
                task, record, command, state, used, lease_epoch=lease_epoch, replay=replay
            )
            if record.revision == revision:
                return record
        return record

    async def _work_states(
        self, task: Task, state: CycleState, pending_gate: str | None
    ) -> dict[str, StepWorkState]:
        pending: dict[str, PendingAttention] = {}
        for item in await self._work_store.pending_attention(project_id=task.project_id):
            pending.setdefault(item.work_id, item)
        states: dict[str, StepWorkState] = {}
        for step_id, work_id in state.step_works.items():
            if step_id in state.step_outcomes or work_id in state.superseded_works:
                continue
            work = await self._work_store.load_work(work_id, project_id=task.project_id)
            if work is None:
                continue
            events: Sequence[WorkEvent] | None = None
            if work.status == SUPERSEDED:
                events = await self._work_store.read_events(work_id, project_id=task.project_id)
                states[step_id] = StepWorkState(
                    step_id=step_id,
                    work_id=work_id,
                    status=work.status,
                    base_moved_phase=self._base_moved_phase(events),
                )
                continue
            attention = pending.get(work_id)
            phase = None
            if work.status == "BASE_MOVED":
                # pending_attention projects a BASE_MOVED Work as WORK_BLOCKED
                # (work/store.py:346-365) and section 8.3 mirrors attention before it
                # supersedes, so the hold must not be offered as attention at all.
                attention = None
                events = await self._work_store.read_events(work_id, project_id=task.project_id)
                phase = self._base_moved_phase(events)
                if phase == "merge" and await self._profile_for(task).is_merged(
                    task, work_id=work_id
                ):
                    phase = None
            github = work.profile_context.get("github") or {}
            report = work.profile_context.get("report") or {}
            decided = None
            if (
                pending_gate is not None
                and not pending_gate.startswith(TASK_GATES)
                and work.pending_gate != pending_gate
            ):
                if events is None:
                    events = await self._work_store.read_events(work_id, project_id=task.project_id)
                decided = self._gate_decision(events, pending_gate)
            states[step_id] = StepWorkState(
                step_id=step_id,
                work_id=work_id,
                status=work.status,
                attention_kind=None if attention is None else attention.kind.value,
                attention_id=None if attention is None else attention.attention_id,
                attention_summary="" if attention is None else attention.summary,
                gate_id=work.pending_gate,
                evidence_refs=() if attention is None else attention.evidence_refs,
                base_moved_phase=phase,
                merged_sha=github.get("merged_sha"),
                deliver_sink_version=report.get("pending_sink_version"),
                decided_gate=decided,
            )
        return states

    @staticmethod
    def _gate_decision(events: Sequence[WorkEvent], gate_id: str) -> str | None:
        return next(
            (
                str(event.payload_json["decision"])
                for event in reversed(events)
                if event.event_type is WorkEventType.GATE_DECIDED
                and event.payload_json["gate_id"] == gate_id
            ),
            None,
        )

    @staticmethod
    def _base_moved_phase(events: Sequence[WorkEvent]) -> str:
        latest = next(
            (event for event in reversed(events) if event.event_type is WorkEventType.BASE_MOVED),
            None,
        )
        return "publish" if latest is None else str(latest.payload_json["phase"])

    @staticmethod
    def _gate_action(events: Sequence[WorkEvent], prefix: str) -> ActionRequest | None:
        """The action record a Work gate carries, from its request or its decision."""
        for event in reversed(events):
            if event.event_type not in {
                WorkEventType.GATE_REQUESTED,
                WorkEventType.GATE_DECIDED,
            }:
                continue
            if not str(event.payload_json["gate_id"]).startswith(prefix):
                continue
            action = event.payload_json.get("action")
            if action is not None:
                return ActionRequest.model_validate(action)
        return None

    @staticmethod
    def _blocked_merge_post_check(events: Sequence[WorkEvent]) -> dict[str, Any] | None:
        """The failed merge post-check's payload, but only when a merge is there to undo.

        A post-check that failed because GitHub never merged carries a null ``merged_sha``:
        there is nothing to revert, so no rollback is offered and the Task blocks with the
        plain message instead (decision 11).
        """
        blocked = next(
            (event for event in reversed(events) if event.event_type is WorkEventType.WORK_BLOCKED),
            None,
        )
        if blocked is None or blocked.payload_json.get("reason") != "merge_post_check_failed":
            return None
        merged_sha = blocked.payload_json.get("merged_sha")
        issue_url = blocked.payload_json.get("issue_url")
        if not merged_sha or not issue_url:
            return None
        return {
            "merged_sha": str(merged_sha),
            "issue_url": str(issue_url),
        }

    async def _budget_used(
        self, task: Task, record: TaskRecord, events: Sequence[TaskEvent]
    ) -> BudgetUsed:
        """Always read the ledger for the cycle attempts bill to, planning included."""
        cycle = self._cycle(record)
        totals = await self._task_store.spend_totals(
            task_id=task.id, project_id=task.project_id, cycle=cycle
        )
        return budget_used_from(totals, events=events, cycle=cycle, now=self._now())

    async def _execute(
        self,
        task: Task,
        record: TaskRecord,
        command: Command,
        state: CycleState,
        used: BudgetUsed,
        *,
        lease_epoch: int,
        replay: bool,
    ) -> TaskRecord:
        if isinstance(command, StartCycle):
            if command.scheduled_for is not None and not await self._task_store.record_command(
                task_id=task.id,
                project_id=task.project_id,
                command_id=f"cycle:{command.scheduled_for}",
                payload={"cycle": command.cycle},
            ):
                return record
            entries: list[Entry] = [
                (
                    TaskEventType.CYCLE_STARTED,
                    {"cycle": command.cycle, "scheduled_for": command.scheduled_for},
                )
            ]
            if state.plan is not None:
                entries.extend(
                    await self._track(
                        task,
                        record,
                        key=f"plan:{state.plan.version}",
                        text=_accepted_plan_text(state.plan),
                    )
                )
            if record.status is not TaskStatus.EXECUTING:
                entries.append(status_entry(record, TaskStatus.EXECUTING))
            return await self._append(record, entries, lease_epoch, command=command)
        if isinstance(command, RunPlanning):
            return await self._run_planning(task, record, command, lease_epoch)
        if isinstance(command, ExhaustBudget):
            entries = [
                (TaskEventType.BUDGET_RECORDED, {"budget_used": used.model_dump(mode="json")}),
                status_entry(record, TaskStatus.BUDGET_EXHAUSTED),
            ]
            entries.extend(
                await self._present(
                    task,
                    record,
                    attention_id=f"budget:{record.current_cycle}",
                    summary=f"Budget exhausted: {command.reason}",
                    urgency="now",
                )
            )
            return await self._append(record, entries, lease_epoch, command=command)
        if isinstance(command, MirrorAttention):
            return await self._mirror(task, record, command, lease_epoch)
        if isinstance(command, MirrorGateDecision):
            entries = [
                (
                    TaskEventType.GATE_DECIDED,
                    {"gate_id": command.gate_id, "decision": command.decision},
                ),
                (
                    TaskEventType.TASK_MESSAGE,
                    {
                        "author": "coordinator",
                        "text": (
                            f"gate {command.gate_id} was decided {command.decision} on the Work"
                        ),
                        "refs": [command.work_id],
                    },
                ),
            ]
            if command.decision != GateDecision.ALLOW.value:
                entries.append(status_entry(record, TaskStatus.BLOCKED))
            return await self._append(record, entries, lease_epoch, command=command)
        if isinstance(command, RequestDeliverGate):
            return await self._request_deliver(task, record, command, lease_epoch)
        if isinstance(command, DeliverReport):
            return await self._deliver(task, record, command, lease_epoch)
        if isinstance(command, StartStep):
            return await self._start_step(task, record, command, state, lease_epoch, replay=replay)
        if isinstance(command, ResumeStep):
            before = await self._work_store.load_work(command.work_id, project_id=task.project_id)
            ledger = self._meter(task, record)
            resumed = await self._profile_for(task).resume(
                task, cycle=record.current_cycle, work_id=command.work_id
            )
            if resumed.status == before.status:
                entries = ledger.drain()
                if entries:
                    spent = await self._budget_used(
                        task,
                        record,
                        await self._task_store.read_events(task.id, project_id=task.project_id),
                    )
                    entries.append(
                        (
                            TaskEventType.BUDGET_RECORDED,
                            {"budget_used": spent.model_dump(mode="json")},
                        )
                    )
                    return await self._append(record, entries, lease_epoch, command=command)
                return record
            spent = await self._budget_used(
                task,
                record,
                await self._task_store.read_events(task.id, project_id=task.project_id),
            )
            entries = ledger.drain()
            entries.append(
                (TaskEventType.BUDGET_RECORDED, {"budget_used": spent.model_dump(mode="json")})
            )
            return await self._append(
                record,
                entries,
                lease_epoch,
                command=command,
            )
        if isinstance(command, RecordStepOutcome):
            return await self._record_outcome(task, record, command, lease_epoch)
        if isinstance(command, SupersedeStep):
            return await self._supersede(task, record, command, state, lease_epoch, replay=replay)
        if isinstance(command, RollbackWork):
            return await self._rollback(task, record, command, lease_epoch)
        if isinstance(command, AssessCycle):
            return await self._assess(task, record, command, state, lease_epoch)
        if isinstance(command, Replan):
            action = coordinator_action(
                task.project_id, action="replan", work_id=task.id, scope=task.id
            )
            entries = [
                (
                    TaskEventType.REPLAN_PROPOSED,
                    {"version": command.plan_version, "reason": command.reason},
                )
            ]
            if resolve_gate(task.authority.replan, action) is GateDecision.REQUIRE_APPROVAL:
                entries.append(
                    (
                        TaskEventType.GATE_REQUESTED,
                        {
                            "gate_id": f"replan:{task.id}:{command.plan_version}",
                            "question": command.reason,
                            "action": action.model_dump(mode="json"),
                        },
                    )
                )
                entries.extend(
                    await self._present(
                        task,
                        record,
                        attention_id=f"replan:{task.id}:{command.plan_version}",
                        summary=command.reason,
                        urgency="today",
                    )
                )
            else:
                entries.append(status_entry(record, TaskStatus.PLANNING))
            return await self._append(record, entries, lease_epoch, command=command)
        if isinstance(command, BlockCycle):
            return await self._block(task, record, command.reason, lease_epoch, command)
        assert isinstance(command, CompleteCycle)
        final = TaskStatus.SCHEDULED if task.kind is TaskKind.SCHEDULED else TaskStatus.COMPLETE
        entries = [
            (TaskEventType.BUDGET_RECORDED, {"budget_used": used.model_dump(mode="json")}),
            (
                TaskEventType.CYCLE_COMPLETED,
                {
                    "cycle": command.cycle,
                    "outcome": command.outcome,
                    "next_run_at": command.next_run_at,
                },
            ),
            status_entry(record, final),
        ]
        record = await self._append(record, entries, lease_epoch, command=command)
        await self._prune_activity(task, state)
        if task.kind is not TaskKind.SCHEDULED:
            return record
        return await self._act_on_health(task, record, command, lease_epoch)

    async def _prune_activity(self, task: Task, state: CycleState) -> None:
        """Section 14.2 retention: drop this cycle's activity past the Task's window.

        A Task without ``retention_days`` keeps its activity.
        """
        if self._activity_store is None or task.retention_days is None or not state.step_works:
            return
        await self._activity_store.prune(
            project_id=task.project_id,
            completed_work_ids=(*state.step_works.values(), *state.superseded_works),
            older_than=self._now() - timedelta(days=task.retention_days),
        )

    @staticmethod
    def _last_health_cycle(events: Sequence[TaskEvent]) -> int | None:
        """The cycle of the most recent health action, for the policy's cooldown."""
        return next(
            (
                int(event.payload_json["cycle"])
                for event in sorted(events, key=lambda item: item.sequence, reverse=True)
                if event.event_type is TaskEventType.HEALTH_ACTION
                and event.payload_json["kind"] != "retry_cycle"
            ),
            None,
        )

    async def _act_on_health(self, task, record, command, lease_epoch):
        """Judge the completed cycle and take at most one action (section 8.6).

        Only scheduled Tasks reach here: PauseSchedule from COMPLETE has no transition edge.
        """
        events = await self._task_store.read_events(task.id, project_id=task.project_id)
        policy = HealthPolicy()
        first_cycle = max(1, command.cycle - policy.window + 1)
        spend = {
            cycle: await self._task_store.spend_totals(
                task_id=task.id, project_id=task.project_id, cycle=cycle
            )
            for cycle in range(first_cycle, command.cycle + 1)
        }
        signal, action = evaluate_health(
            cycle_history(events, spend=spend),
            policy=policy,
            last_action_cycle=self._last_health_cycle(events),
        )
        if signal is None:
            return record
        health: list[Entry] = [(TaskEventType.HEALTH_SIGNAL, signal.model_dump(mode="json"))]
        if action is not None:
            health.append(
                (
                    TaskEventType.HEALTH_ACTION,
                    {**action.model_dump(mode="json"), "cycle": signal.cycle},
                )
            )
            if isinstance(action, PauseSchedule):
                health.append(status_entry(record, TaskStatus.PAUSED))
            if isinstance(action, AlertOperator):
                health.append(
                    (
                        TaskEventType.ATTENTION_CHANGED,
                        {"owner": "user", "reason": f"health:{signal.kind}:{signal.cycle}"},
                    )
                )
                health.extend(
                    await self._present(
                        task,
                        record,
                        attention_id=f"health:{signal.kind}:{signal.cycle}",
                        summary=signal.detail,
                        urgency="today",
                    )
                )
        return await self._append(record, health, lease_epoch)

    async def _append(
        self,
        record: TaskRecord,
        entries: Sequence[Entry],
        lease_epoch: int,
        *,
        command: Command | None = None,
    ) -> TaskRecord:
        """Append one batch; a command's batch is headed by its COMMAND_RECEIPT event."""
        if command is not None:
            entries = [
                (
                    TaskEventType.COMMAND_RECEIPT,
                    {
                        "command_id": command.receipt_id(record.revision),
                        "kind": command.kind,
                        "payload": command.model_dump(mode="json"),
                    },
                ),
                *entries,
            ]
        return await self._writer.append(record, entries, lease_epoch=lease_epoch, now=self._now())

    async def _request_deliver(
        self, task: Task, record: TaskRecord, command: RequestDeliverGate, lease_epoch: int
    ) -> TaskRecord:
        """Section 8.8: a compensatable delivery runs; an irreversible one asks an admin."""
        work = await self._work_store.load_work(command.work_id, project_id=task.project_id)
        action = ActionRequest.model_validate(work.profile_context["report"]["deliver_action"])
        gate_id = deliver_action_id(command.work_id, sink_version=command.sink_version)
        decision = resolve_gate(task.authority.deliver, action)
        if decision is GateDecision.ALLOW:
            entries: list[Entry] = [
                (
                    TaskEventType.GATE_DECIDED,
                    {
                        "gate_id": gate_id,
                        "decision": GateDecision.ALLOW.value,
                        "action": action.model_dump(mode="json"),
                    },
                )
            ]
        else:
            entries = [
                (
                    TaskEventType.GATE_REQUESTED,
                    {
                        "gate_id": gate_id,
                        "question": f"Deliver the report to {action.scope}?",
                        "action": action.model_dump(mode="json"),
                        "work_id": command.work_id,
                    },
                )
            ]
            entries.extend(
                await self._present(
                    task,
                    record,
                    attention_id=gate_id,
                    summary=f"Approve delivery of the report to {action.scope}",
                    urgency="today",
                    evidence_refs=action.evidence_refs,
                )
            )
        return await self._append(record, entries, lease_epoch, command=command)

    async def _deliver(
        self, task: Task, record: TaskRecord, command: DeliverReport, lease_epoch: int
    ) -> TaskRecord:
        """Section 8.8 in one batch: the receipt precedes the side effect, the records follow."""
        action_id = deliver_action_id(command.work_id, sink_version=command.sink_version)
        events = await self._task_store.read_events(task.id, project_id=task.project_id)
        requested = next(
            event
            for event in reversed(events)
            if event.event_type in {TaskEventType.GATE_REQUESTED, TaskEventType.GATE_DECIDED}
            and event.payload_json["gate_id"] == action_id
            and "action" in event.payload_json
        )
        action = ActionRequest.model_validate(requested.payload_json["action"])
        entries: list[Entry] = [
            (
                TaskEventType.ACTION_INTENT_RECORDED,
                {
                    "action_id": action_id,
                    "work_id": command.work_id,
                    "gate_id": action_id,
                    "action": action.model_dump(mode="json"),
                },
            )
        ]
        first = await self._task_store.record_command(
            task_id=task.id,
            project_id=task.project_id,
            command_id=action_id,
            payload={"work_id": command.work_id, "sink_version": command.sink_version},
        )
        if not first:
            result, observation = _lost_delivery(task.project_id, action_id, action)
            entries.append(
                (
                    TaskEventType.ACTION_RESULT_RECORDED,
                    {"work_id": command.work_id, **result.model_dump(mode="json")},
                )
            )
            entries.append(
                (
                    TaskEventType.OBSERVATION_RECORDED,
                    {"work_id": command.work_id, **observation},
                )
            )
            entries.append(
                (
                    TaskEventType.TASK_MESSAGE,
                    {
                        "author": "coordinator",
                        "text": f"deliver on {action.scope}: blocked ({observation['detail']})",
                        "refs": [command.work_id],
                    },
                )
            )
            entries.append(status_entry(record, TaskStatus.BLOCKED))
            return await self._append(record, entries, lease_epoch, command=command)
        _work, receipts = await self._profile_for(task).deliver(
            task, work_id=command.work_id, sink_version=command.sink_version
        )
        failed: DeliveryReceipt | None = None
        for receipt in receipts:
            result = receipt.result.model_dump(mode="json")
            result["action_id"] = action_id
            entries.append(
                (
                    TaskEventType.ACTION_RESULT_RECORDED,
                    {"work_id": command.work_id, **result},
                )
            )
            entries.append(
                (
                    TaskEventType.OBSERVATION_RECORDED,
                    {"work_id": command.work_id, **receipt.observation, "action_id": action_id},
                )
            )
            if not receipt.observation["passed"]:
                failed = receipt
        if failed is not None and failed.action.rollback is not None:
            action = failed.action.model_copy(
                update={"scope": failed.result.external_ref or failed.action.scope}
            )
            entries.append(
                (
                    TaskEventType.GATE_REQUESTED,
                    {
                        "gate_id": f"rollback:{command.work_id}",
                        "question": (
                            f"The delivery post-check {failed.observation['check']} failed. "
                            f"Allow the recorded rollback ({action.rollback})?"
                        ),
                        "action": action.model_dump(mode="json"),
                        "work_id": command.work_id,
                    },
                )
            )
            entries.extend(
                await self._present(
                    task,
                    record,
                    attention_id=f"rollback:{command.work_id}",
                    summary=f"Delivery post-check failed on {action.scope}",
                    urgency="now",
                    evidence_refs=failed.result.evidence_refs,
                )
            )
        return await self._append(record, entries, lease_epoch, command=command)

    async def _run_planning(
        self, task: Task, record: TaskRecord, command: RunPlanning, lease_epoch: int
    ) -> TaskRecord:
        profile = self._profile_for(task)
        base_sha = await profile.base_sha(task)
        brief = self._artifacts.read(task.brief_ref.storage_ref, project_id=task.project_id).decode(
            "utf-8"
        )
        events = await self._task_store.read_events(task.id, project_id=task.project_id)
        amendments = tuple(
            f"{event.payload_json['question_id']}: {event.payload_json['answer']}"
            for event in events
            if event.event_type is TaskEventType.CLARIFICATION_ANSWERED
        )
        ledger = self._meter(task, record)
        try:
            result = await profile.plan(
                task,
                cycle=record.current_cycle,
                plan_version=command.plan_version,
                base_sha=base_sha,
                brief_text=brief,
                amendments=amendments,
            )
        except PlanningFailedError as exc:
            return await self._block(
                task,
                record,
                f"planning failed: {exc}",
                lease_epoch,
                command,
                prefix=ledger.drain(),
            )
        if result.asks_first:
            defaults = await self._task_store.get_defaults(project_id=task.project_id)
            deadline = self._now() + timedelta(seconds=defaults.clarification_deadline_seconds)
            entries = ledger.drain()
            entries.extend(
                [
                    (
                        TaskEventType.CLARIFICATION_REQUESTED,
                        {
                            "questions": [
                                question.model_dump(mode="json")
                                for question in result.clarifications
                            ],
                            "deadline_at": deadline.isoformat(),
                        },
                    ),
                    status_entry(record, TaskStatus.CLARIFYING),
                ]
            )
            return await self._append(record, entries, lease_epoch, command=command)
        try:
            plan = accept_plan(
                result, budget=task.budget, target=task.target, version=command.plan_version
            )
        except PlanRejectedError as exc:
            return await self._block(
                task,
                record,
                f"plan rejected: {exc}",
                lease_epoch,
                command,
                prefix=ledger.drain(),
            )
        entries = ledger.drain()
        entries.append(
            (
                TaskEventType.PLAN_PROPOSED,
                {
                    "version": plan.version,
                    "steps": [step.model_dump(mode="json") for step in plan.steps],
                    "acceptance_matrix": [
                        item.model_dump(mode="json") for item in plan.acceptance_matrix
                    ],
                },
            )
        )
        action = coordinator_action(task.project_id, action="plan", work_id=task.id, scope=task.id)
        if resolve_gate(task.authority.plan, action) is GateDecision.REQUIRE_APPROVAL:
            entries.append(
                (
                    TaskEventType.GATE_REQUESTED,
                    {
                        "gate_id": f"plan:{task.id}:{plan.version}",
                        "question": f"Approve the {len(plan.steps)}-step plan.",
                        "action": action.model_dump(mode="json"),
                    },
                )
            )
            entries.extend(
                await self._present(
                    task,
                    record,
                    attention_id=f"plan:{task.id}:{plan.version}",
                    summary=f"Approve the {len(plan.steps)}-step plan.",
                    urgency="today",
                )
            )
            entries.append(status_entry(record, TaskStatus.PLAN_PROPOSED))
        else:
            entries.append((TaskEventType.PLAN_ACCEPTED, {"version": plan.version}))
            entries.append(status_entry(record, TaskStatus.EXECUTING))
        return await self._append(record, entries, lease_epoch, command=command)

    async def _block_gaps(self, task: Task) -> tuple[str, ...]:
        events = await self._task_store.read_events(task.id, project_id=task.project_id)
        latest = next(
            (
                event
                for event in reversed(events)
                if event.event_type is not TaskEventType.COMMAND_RECEIPT
            ),
            None,
        )
        if (
            latest is None
            or latest.event_type is not TaskEventType.ASSESSMENT_RECORDED
            or not latest.payload_json.get("gaps")
        ):
            return ()
        return tuple(
            f"{gap['statement']} (suggested step: {gap['suggested_step']})"
            for gap in latest.payload_json["gaps"]
        )

    async def _block(
        self,
        task: Task,
        record: TaskRecord,
        text: str,
        lease_epoch: int,
        command: Command,
        prefix: Sequence[Entry] = (),
    ) -> TaskRecord:
        gaps = await self._block_gaps(task)
        message = text if not gaps else f"{text}: {'; '.join(gaps)}"
        entries: list[Entry] = list(prefix)
        entries.extend(
            [
                (
                    TaskEventType.TASK_MESSAGE,
                    {"author": "coordinator", "text": message, "refs": []},
                ),
                status_entry(record, TaskStatus.BLOCKED),
            ]
        )
        entries.extend(
            await self._present(
                task,
                record,
                attention_id=f"block:{record.current_cycle}:{record.revision}",
                summary=message,
                urgency="now",
                evidence_refs=gaps,
            )
        )
        return await self._append(
            record,
            entries,
            lease_epoch,
            command=command,
        )

    async def _mirror(
        self, task: Task, record: TaskRecord, command: MirrorAttention, lease_epoch: int
    ) -> TaskRecord:
        entries: list[Entry] = []
        if command.attention_kind == "GATE_REQUESTED" and command.gate_id is not None:
            work_events = await self._work_store.read_events(
                command.work_id, project_id=task.project_id
            )
            gate = next(
                (
                    event
                    for event in reversed(work_events)
                    if event.event_type is WorkEventType.GATE_REQUESTED
                    and event.payload_json["gate_id"] == command.gate_id
                ),
                None,
            )
            if gate is None:
                raise ValueError(
                    f"Work {command.work_id} has no GATE_REQUESTED for {command.gate_id}"
                )
            entries.append(
                (
                    TaskEventType.GATE_REQUESTED,
                    {
                        "gate_id": command.gate_id,
                        "question": command.summary,
                        "action": gate.payload_json["action"],
                        "work_id": command.work_id,
                        "attention_id": command.attention_id,
                    },
                )
            )
        else:
            work_events = await self._work_store.read_events(
                command.work_id, project_id=task.project_id
            )
            blocked = self._blocked_merge_post_check(work_events)
            action = self._gate_action(work_events, "merge:") if blocked is not None else None
            if action is not None:
                entries.append(
                    (
                        TaskEventType.GATE_REQUESTED,
                        {
                            "gate_id": f"rollback:{command.work_id}",
                            "question": (
                                f"The merge post-check failed on {action.scope}. "
                                "Allow the recorded rollback to revert and merge the revert?"
                            ),
                            "action": action.model_dump(mode="json"),
                            "work_id": command.work_id,
                            "attention_id": command.attention_id,
                            "merged_sha": blocked["merged_sha"],
                            "issue_url": blocked["issue_url"],
                        },
                    )
                )
            else:
                entries.append(
                    (
                        TaskEventType.TASK_MESSAGE,
                        {
                            "author": "coordinator",
                            "text": command.summary,
                            "refs": [command.work_id],
                            "attention_id": command.attention_id,
                        },
                    )
                )
                target = (
                    TaskStatus.CONTROL_DEGRADED
                    if command.attention_kind == "CONTROL_DEGRADED"
                    else TaskStatus.BLOCKED
                )
                entries.append(status_entry(record, target))
        entries.extend(
            await self._present(
                task,
                record,
                attention_id=command.attention_id,
                summary=command.summary,
                urgency=_URGENCY.get(command.attention_kind, "today"),
                evidence_refs=(*command.evidence_refs, command.work_id),
            )
        )
        return await self._append(record, entries, lease_epoch, command=command)

    async def _rollback(
        self, task: Task, record: TaskRecord, command: RollbackWork, lease_epoch: int
    ) -> TaskRecord:
        """Execute the recorded recipe once; a lost batch asks a human, never repeats."""
        events = await self._task_store.read_events(task.id, project_id=task.project_id)
        gate_id = f"rollback:{command.work_id}"
        requested = next(
            event
            for event in reversed(events)
            if event.event_type is TaskEventType.GATE_REQUESTED
            and event.payload_json["gate_id"] == gate_id
        )
        action = ActionRequest.model_validate(requested.payload_json["action"])
        try:
            intent = rollback_action(action)
            if intent.reversibility is Reversibility.IRREVERSIBLE:
                detail = f"the recorded rollback for {action.scope} is irreversible and was not run"
                result, observation = _failed_rollback(
                    task.project_id,
                    _refused_rollback_action_id(command.work_id, action),
                    detail,
                    self._now(),
                )
                return await self._block(
                    task,
                    record,
                    detail,
                    lease_epoch,
                    command,
                    prefix=_rollback_refusal_entries(command.work_id, result, observation),
                )
            action_id = rollback_action_id(action)
        except RollbackRefusedError as exc:
            result, observation = _failed_rollback(
                task.project_id,
                _refused_rollback_action_id(command.work_id, action),
                str(exc),
                self._now(),
            )
            return await self._block(
                task,
                record,
                f"the recorded rollback cannot run: {exc}",
                lease_epoch,
                command,
                prefix=_rollback_refusal_entries(command.work_id, result, observation),
            )
        entries: list[Entry] = [
            (
                TaskEventType.ACTION_INTENT_RECORDED,
                {
                    "action_id": action_id,
                    "work_id": command.work_id,
                    "gate_id": gate_id,
                    "action": intent.model_dump(mode="json"),
                },
            )
        ]
        first = await self._task_store.record_command(
            task_id=task.id,
            project_id=task.project_id,
            command_id=gate_id,
            payload={"action_id": action_id},
        )
        if first:
            try:
                result, observation = await self._rollbacks.run(
                    task,
                    action=action,
                    action_id=action_id,
                    merged_sha=requested.payload_json.get("merged_sha"),
                    issue_url=requested.payload_json.get("issue_url"),
                )
            except RollbackRefusedError as exc:
                result, observation = _failed_rollback(
                    task.project_id, action_id, str(exc), self._now()
                )
        else:
            result, observation = _lost_rollback(task.project_id, action_id)
        message = f"{intent.action} on {action.scope}: {result.status}"
        if result.status == "failed":
            message = f"{message} ({observation['detail']})"
        entries.append(
            (
                TaskEventType.ACTION_RESULT_RECORDED,
                {"work_id": command.work_id, **result.model_dump(mode="json")},
            )
        )
        entries.append(
            (
                TaskEventType.OBSERVATION_RECORDED,
                {"work_id": command.work_id, **observation},
            )
        )
        entries.append(
            (
                TaskEventType.TASK_MESSAGE,
                {
                    "author": "coordinator",
                    "text": message,
                    "refs": [command.work_id],
                },
            )
        )
        entries.append(status_entry(record, TaskStatus.BLOCKED))
        return await self._append(record, entries, lease_epoch, command=command)

    async def _due_at(self, task: Task, record: TaskRecord, urgency: str) -> datetime:
        derived = self._now() + _DUE_IN[urgency]
        if record.pending_questions == 0:
            return derived
        events = await self._task_store.read_events(task.id, project_id=task.project_id)
        deadline = next(
            (
                datetime.fromisoformat(event.payload_json["deadline_at"])
                for event in reversed(events)
                if event.event_type is TaskEventType.CLARIFICATION_REQUESTED
            ),
            None,
        )
        return derived if deadline is None else min(derived, deadline)

    async def _present(
        self,
        task: Task,
        record: TaskRecord,
        *,
        attention_id: str,
        summary: str,
        urgency: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> list[Entry]:
        """Present once per channel: ``now`` fans out to every channel, ``today`` and
        ``this_week`` present to the first only and ``DecisionEscalation`` walks the rest (§15);
        a ``TrackingDecisionChannel`` is always presented to; a channel that raises loses its
        receipt, so the item can be presented again."""
        due_at = await self._due_at(task, record, urgency)
        decision = DecisionRequest(
            project_id=task.project_id,
            task_id=task.id,
            attention_id=attention_id,
            summary=summary,
            urgency=urgency,
            due_at=due_at,
            evidence_refs=tuple(evidence_refs),
        )
        entries: list[Entry] = []
        all_channels = await self._channels(task)
        channels = (
            all_channels
            if urgency == "now"
            else tuple(
                channel
                for index, channel in enumerate(all_channels)
                if index == 0 or isinstance(channel, TrackingDecisionChannel)
            )
        )
        selected_ids = {id(channel) for channel in channels}

        async def notify_channel(channel: DecisionChannel) -> bool | None:
            command_id = f"notify:{channel.name}:{attention_id}"
            recorded = await self._task_store.record_command(
                task_id=task.id,
                project_id=task.project_id,
                command_id=command_id,
                payload={"decision": decision.model_dump(mode="json")},
            )
            if not recorded:
                return None
            try:
                reference = await channel.notify(decision)
            except Exception as exc:
                logger.warning(
                    "decision channel failed",
                    extra={
                        "event": "task.notify.failed",
                        "task": task.id,
                        "channel": channel.name,
                        "attention_id": attention_id,
                        "error": channel_error_detail(exc),
                    },
                )
                await self._task_store.delete_command(
                    task_id=task.id, project_id=task.project_id, command_id=command_id
                )
                return False
            if reference is None:
                await self._task_store.delete_command(
                    task_id=task.id, project_id=task.project_id, command_id=command_id
                )
                return False
            if isinstance(channel, TrackingDecisionChannel):
                established = channel.established(task.id)
                if established is not None:
                    entries.append((TaskEventType.TRACKING_ISSUE_RECORDED, {"url": established}))
            entries.append(
                (
                    TaskEventType.NOTIFICATION_PRESENTED,
                    {
                        "channel": channel.name,
                        "ref": reference,
                        "attention_id": attention_id,
                        "urgency": urgency,
                        "due_at": due_at.isoformat(),
                        "summary": summary,
                        "evidence_refs": list(evidence_refs),
                    },
                )
            )
            return True

        presented = False
        skipped = False
        for channel in channels:
            result = await notify_channel(channel)
            skipped = result is None or skipped
            presented = bool(result) or presented
        if presented or skipped:
            return entries
        for channel in all_channels:
            if id(channel) in selected_ids:
                continue
            if await notify_channel(channel):
                break
        return entries

    async def _track(self, task: Task, record: TaskRecord, *, key: str, text: str) -> list[Entry]:
        channel = next(
            (
                candidate
                for candidate in await self._channels(task)
                if isinstance(candidate, TrackingDecisionChannel)
            ),
            None,
        )
        if channel is None:
            return []
        command_id = f"track:{key}"
        recorded = await self._task_store.record_command(
            task_id=task.id,
            project_id=task.project_id,
            command_id=command_id,
            payload={"text": text},
        )
        if not recorded:
            return []
        try:
            reference = await channel.track(task, text)
        except Exception as exc:
            logger.warning(
                "tracking channel failed",
                extra={
                    "event": "task.track.failed",
                    "task": task.id,
                    "channel": channel.name,
                    "key": key,
                    "error": channel_error_detail(exc),
                },
            )
            await self._task_store.delete_command(
                task_id=task.id, project_id=task.project_id, command_id=command_id
            )
            return []
        if reference is None:
            await self._task_store.delete_command(
                task_id=task.id, project_id=task.project_id, command_id=command_id
            )
            return []
        established = channel.established(task.id)
        if established is not None:
            return [(TaskEventType.TRACKING_ISSUE_RECORDED, {"url": established})]
        return []

    async def _start_step(
        self,
        task: Task,
        record: TaskRecord,
        command: StartStep,
        state: CycleState,
        lease_epoch: int,
        *,
        replay: bool,
    ) -> TaskRecord:
        step = next(step for step in state.plan.steps if step.id == command.step_id)
        lease_key = task.repository_lease_key
        if lease_key is not None:
            acquired = await self._task_store.acquire_repository_lease(
                lease_key,
                project_id=task.project_id,
                task_id=task.id,
                work_id=None,
                ttl_seconds=8 * 3600,
            )
            if not acquired:
                logger.info(
                    "repository lease held by another task",
                    extra={"event": "task.lease.busy", "task": task.id, "lease_key": lease_key},
                )
                return record
        issue_url = state.issue_urls.get(step.id)
        profile = self._profile_for(task)
        if issue_url is None and replay:
            issue_url = await profile.find_issue(task, cycle=record.current_cycle, step=step)
        if issue_url is None:
            issue_url = await profile.create_issue(task, cycle=record.current_cycle, step=step)
            await self._task_store.record_command(
                task_id=task.id,
                project_id=task.project_id,
                command_id=f"issue:{record.current_cycle}:{step.id}",
                payload={"issue_url": issue_url},
            )
        base_sha = await profile.base_sha(task)
        work = await profile.find_work(task, issue_url=issue_url) if replay else None
        if work is None:
            ledger = self._meter(task, record)
            work = await profile.start(
                task,
                cycle=record.current_cycle,
                step=step,
                issue_url=issue_url,
                base_sha=base_sha,
            )
            entries = ledger.drain()
        else:
            # The Work already exists from a crashed attempt: record the base it actually
            # pinned (lifecycle.py:375 seeds it), never the head we just fetched.
            base_sha = work.profile_context.get("base_sha", base_sha)
            entries: list[Entry] = []
        if lease_key is not None:
            entries.append(
                (
                    TaskEventType.REPOSITORY_LEASE_ACQUIRED,
                    {"lease_key": lease_key, "work_id": work.work_id},
                )
            )
        entries.extend(
            await self._track(
                task,
                record,
                key=f"step:{step.id}:{work.work_id}",
                text=f"Step {step.id} started as {work.work_id}\nIssue: {issue_url}",
            )
        )
        entries.append(
            (
                TaskEventType.STEP_WORK_STARTED,
                {
                    "step_id": step.id,
                    "work_id": work.work_id,
                    "issue_url": issue_url,
                    "base_sha": base_sha,
                },
            )
        )
        return await self._append(record, entries, lease_epoch, command=command)

    async def _record_outcome(
        self, task: Task, record: TaskRecord, command: RecordStepOutcome, lease_epoch: int
    ) -> TaskRecord:
        entries: list[Entry] = [
            (
                TaskEventType.STEP_WORK_OUTCOME,
                {
                    "step_id": command.step_id,
                    "work_id": command.work_id,
                    "outcome": command.outcome,
                },
            )
        ]
        if command.merged_sha is not None:
            entries.append(
                (
                    TaskEventType.BASE_ADVANCED,
                    {
                        "step_id": command.step_id,
                        "work_id": command.work_id,
                        "merged_sha": command.merged_sha,
                    },
                )
            )
            work = await self._work_store.load_work(command.work_id, project_id=task.project_id)
            github = {} if work is None else (work.profile_context.get("github") or {})
            pull_request_url = github.get("pull_request_url") or f"work://{command.work_id}"
            entries.extend(
                await self._track(
                    task,
                    record,
                    key=f"merge:{command.work_id}",
                    text=(
                        f"Step {command.step_id} merged through {command.work_id}\n"
                        f"Pull request: {pull_request_url}"
                    ),
                )
            )
        lease_key = task.repository_lease_key
        if lease_key is not None:
            await self._task_store.release_repository_lease(
                lease_key, project_id=task.project_id, task_id=task.id
            )
            entries.append((TaskEventType.REPOSITORY_LEASE_RELEASED, {"lease_key": lease_key}))
        return await self._append(record, entries, lease_epoch, command=command)

    async def _supersede_evidence(self, task: Task, work_id: str) -> tuple[str, ...]:
        """Section 10: the superseded Work's reviewed diff is its pull request."""
        record = await self._work_store.load_work(work_id, project_id=task.project_id)
        github = {} if record is None else (record.profile_context.get("github") or {})
        url = github.get("pull_request_url")
        return (str(url),) if url else (f"work://{work_id}",)

    async def _supersede(
        self,
        task: Task,
        record: TaskRecord,
        command: SupersedeStep,
        state: CycleState,
        lease_epoch: int,
        *,
        replay: bool,
    ) -> TaskRecord:
        step = next(step for step in state.plan.steps if step.id == command.step_id)
        issue_url = state.issue_urls[step.id]
        evidence = await self._supersede_evidence(task, command.work_id)
        profile = self._profile_for(task)
        base_sha = await profile.base_sha(task)
        replacement = await profile.find_work(task, issue_url=issue_url, exclude=command.work_id)
        if replacement is None:
            replacement = await profile.start(
                task,
                cycle=record.current_cycle,
                step=step,
                issue_url=issue_url,
                base_sha=base_sha,
                evidence_refs=evidence,
            )
        else:
            base_sha = replacement.profile_context.get("base_sha", base_sha)
        await supersede_work(
            self._work_store,
            work_id=command.work_id,
            project_id=task.project_id,
            superseded_by=replacement.work_id,
            reason="base_moved",
            actor_ref="coordinator",
        )
        entries: list[Entry] = [
            (
                TaskEventType.STEP_WORK_SUPERSEDED,
                {
                    "step_id": step.id,
                    "work_id": command.work_id,
                    "superseded_by": replacement.work_id,
                    "reason": "base_moved",
                },
            ),
            (
                TaskEventType.STEP_WORK_STARTED,
                {
                    "step_id": step.id,
                    "work_id": replacement.work_id,
                    "issue_url": issue_url,
                    "base_sha": base_sha,
                },
            ),
        ]
        return await self._append(record, entries, lease_epoch, command=command)

    async def _assess(
        self,
        task: Task,
        record: TaskRecord,
        command: AssessCycle,
        state: CycleState,
        lease_epoch: int,
    ) -> TaskRecord:
        advanced = [
            str(event.payload_json["merged_sha"])
            for event in await self._task_store.read_events(task.id, project_id=task.project_id)
            if event.event_type is TaskEventType.BASE_ADVANCED
        ]
        ledger = self._meter(task, record)
        try:
            result = await self._profile_for(task).assess(
                task,
                cycle=command.cycle,
                plan_version=record.plan_version,
                plan=state.plan,
                outcomes=state.step_outcomes,
                merged_sha=advanced[-1] if advanced else None,
                evidence=tuple(f"git://{sha}" for sha in advanced),
            )
        except AssessmentFailedError as exc:
            return await self._block(
                task,
                record,
                f"assessment failed: {exc}",
                lease_epoch,
                command,
                prefix=ledger.drain(),
            )
        entries: list[Entry] = ledger.drain()
        entries.append(status_entry(record, TaskStatus.ASSESSING))
        entries.append(
            (
                TaskEventType.ASSESSMENT_RECORDED,
                {"cycle": command.cycle, **result.model_dump(mode="json")},
            )
        )
        entries.extend(
            await self._track(
                task,
                record,
                key=f"assess:{command.cycle}:{record.plan_version}",
                text=_assessment_tracking_text(command.cycle, result),
            )
        )
        return await self._append(record, entries, lease_epoch, command=command)


__all__ = ["ProfileRunner", "TaskCoordinator"]
