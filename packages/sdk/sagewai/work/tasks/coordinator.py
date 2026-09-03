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
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.work.activity import WorkActivityStore
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import SUPERSEDED, GateDecision, PendingAttention, WorkRecord
from sagewai.work.store import WorkStore
from sagewai.work.supersede import supersede_work
from sagewai.work.tasks.assessment import assess_cycle
from sagewai.work.tasks.budget import BudgetLedger, budget_used_from
from sagewai.work.tasks.decide import (
    AssessCycle,
    BlockCycle,
    Command,
    CompleteCycle,
    CycleState,
    ExhaustBudget,
    MirrorAttention,
    RecordStepOutcome,
    Replan,
    ResumeStep,
    RunPlanning,
    StartCycle,
    StartStep,
    StepWorkState,
    SupersedeStep,
    decide,
    fold_cycle,
)
from sagewai.work.tasks.decisions import (
    ConsoleDecisionChannel,
    DecisionChannel,
    DecisionRequest,
    coordinator_action,
    resolve_gate,
)
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import BudgetUsed, Task, TaskKind, TaskRecord, TaskStatus
from sagewai.work.tasks.plan import PlanRejectedError, PlanStep, TaskPlanResult, accept_plan
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


class TaskCoordinator:
    """One command per decision, each behind a receipt and the Task's lease epoch."""

    _MAX_COMMANDS = 20

    def __init__(
        self,
        *,
        task_store: TaskStore,
        work_store: WorkStore,
        profile_runner: ProfileRunner,
        artifact_store: LocalArtifactStore | None = None,
        activity_store: WorkActivityStore | None = None,
        decision_channels: Sequence[DecisionChannel] = (),
        actor_ref: str = "coordinator",
    ) -> None:
        self._task_store = task_store
        self._work_store = work_store
        self._profile = profile_runner
        self._artifacts = artifact_store or LocalArtifactStore()
        self._activity_store = activity_store
        self._channels = tuple(decision_channels) or (ConsoleDecisionChannel(),)
        self._writer = TaskWriter(task_store, actor_ref=actor_ref)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

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
        self._profile.use_ledger(ledger)
        return ledger

    async def drive(self, record: TaskRecord, *, lease_epoch: int) -> TaskRecord:
        """Run commands until the Task waits, a command makes no progress, or the cap is hit."""
        task, record = await self._load(record.task_id, record.project_id)
        if record.lease_epoch != lease_epoch:
            raise StaleTaskError("lease epoch changed; another coordinator owns this task")
        for _ in range(self._MAX_COMMANDS):
            events = await self._task_store.read_events(task.id, project_id=task.project_id)
            state = fold_cycle(events, plan_version=record.plan_version)
            works = await self._work_states(task, state)
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

    async def _work_states(self, task: Task, state: CycleState) -> dict[str, StepWorkState]:
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
                if phase == "merge" and await self._profile.is_merged(task, work_id=work_id):
                    phase = None
            github = work.profile_context.get("github") or {}
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
            )
        return states

    @staticmethod
    def _base_moved_phase(events: Sequence[WorkEvent]) -> str:
        latest = next(
            (event for event in reversed(events) if event.event_type is WorkEventType.BASE_MOVED),
            None,
        )
        return "publish" if latest is None else str(latest.payload_json["phase"])

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
            entries: list[Entry] = [
                (
                    TaskEventType.CYCLE_STARTED,
                    {"cycle": command.cycle, "scheduled_for": command.scheduled_for},
                )
            ]
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
        if isinstance(command, StartStep):
            return await self._start_step(task, record, command, state, lease_epoch, replay=replay)
        if isinstance(command, ResumeStep):
            before = await self._work_store.load_work(command.work_id, project_id=task.project_id)
            ledger = self._meter(task, record)
            resumed = await self._profile.resume(
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
        return await self._append(record, entries, lease_epoch, command=command)

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

    async def _run_planning(
        self, task: Task, record: TaskRecord, command: RunPlanning, lease_epoch: int
    ) -> TaskRecord:
        base_sha = await self._profile.base_sha(task)
        brief = self._artifacts.read(
            task.brief_ref.storage_ref, project_id=task.project_id
        ).decode("utf-8")
        events = await self._task_store.read_events(task.id, project_id=task.project_id)
        amendments = tuple(
            f"{event.payload_json['question_id']}: {event.payload_json['answer']}"
            for event in events
            if event.event_type is TaskEventType.CLARIFICATION_ANSWERED
        )
        ledger = self._meter(task, record)
        try:
            result = await self._profile.plan(
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
            entries.append(status_entry(record, TaskStatus.PLAN_PROPOSED))
        else:
            entries.append((TaskEventType.PLAN_ACCEPTED, {"version": plan.version}))
            entries.append(status_entry(record, TaskStatus.EXECUTING))
        return await self._append(record, entries, lease_epoch, command=command)

    async def _block_text(self, task: Task, text: str) -> str:
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
            return text
        gaps = "; ".join(
            f"{gap['statement']} (suggested step: {gap['suggested_step']})"
            for gap in latest.payload_json["gaps"]
        )
        return f"{text}: {gaps}"

    async def _block(
        self,
        task: Task,
        record: TaskRecord,
        text: str,
        lease_epoch: int,
        command: Command,
        prefix: Sequence[Entry] = (),
    ) -> TaskRecord:
        message = await self._block_text(task, text)
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
                event
                for event in reversed(work_events)
                if event.event_type is WorkEventType.GATE_REQUESTED
                and event.payload_json["gate_id"] == command.gate_id
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
        decision = DecisionRequest(
            project_id=task.project_id,
            task_id=task.id,
            attention_id=attention_id,
            summary=summary,
            urgency=urgency,
            evidence_refs=tuple(evidence_refs),
        )
        entries: list[Entry] = []
        for channel in self._channels:
            recorded = await self._task_store.record_command(
                task_id=task.id,
                project_id=task.project_id,
                command_id=f"notify:{channel.name}:{attention_id}",
                payload={"decision": decision.model_dump(mode="json")},
            )
            if not recorded:
                continue
            reference = await channel.notify(decision)
            if reference is None:
                continue
            entries.append(
                (
                    TaskEventType.NOTIFICATION_PRESENTED,
                    {
                        "channel": channel.name,
                        "ref": reference,
                        "attention_id": attention_id,
                        "urgency": urgency,
                        "due_at": None,
                    },
                )
            )
        return entries

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
        if issue_url is None and replay:
            issue_url = await self._profile.find_issue(task, cycle=record.current_cycle, step=step)
        if issue_url is None:
            issue_url = await self._profile.create_issue(task, cycle=record.current_cycle, step=step)
            await self._task_store.record_command(
                task_id=task.id,
                project_id=task.project_id,
                command_id=f"issue:{record.current_cycle}:{step.id}",
                payload={"issue_url": issue_url},
            )
        base_sha = await self._profile.base_sha(task)
        work = await self._profile.find_work(task, issue_url=issue_url) if replay else None
        if work is None:
            ledger = self._meter(task, record)
            work = await self._profile.start(
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
                {"step_id": command.step_id, "work_id": command.work_id, "outcome": command.outcome},
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
        base_sha = await self._profile.base_sha(task)
        replacement = await self._profile.find_work(
            task, issue_url=issue_url, exclude=command.work_id
        )
        if replacement is None:
            replacement = await self._profile.start(
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
        evidence = tuple(
            f"git://{event.payload_json['merged_sha']}"
            for event in await self._task_store.read_events(task.id, project_id=task.project_id)
            if event.event_type is TaskEventType.BASE_ADVANCED
        )
        result = assess_cycle(
            state.plan,
            attempt_id=f"{task.id}:assess:{command.cycle}",
            outcomes=state.step_outcomes,
            evidence=evidence,
        )
        entries: list[Entry] = [status_entry(record, TaskStatus.ASSESSING)]
        entries.append(
            (
                TaskEventType.ASSESSMENT_RECORDED,
                {"cycle": command.cycle, **result.model_dump(mode="json")},
            )
        )
        return await self._append(record, entries, lease_epoch, command=command)


__all__ = ["ProfileRunner", "TaskCoordinator"]
