# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pure decision layer: fold the Task stream, then pick one command (spec section 8.3)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from sagewai.work.tasks.budget import budget_breach
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import BudgetUsed, Task, TaskKind, TaskRecord, TaskStatus
from sagewai.work.tasks.plan import AcceptedPlan, PlanStep, plan_from_events
from sagewai.work.tasks.schedule import next_fire

_WAITING = frozenset(
    {
        TaskStatus.CLARIFYING,
        TaskStatus.PLAN_PROPOSED,
        TaskStatus.BLOCKED,
        TaskStatus.BUDGET_EXHAUSTED,
        TaskStatus.CONTROL_DEGRADED,
        TaskStatus.PAUSED,
        TaskStatus.COMPLETE,
        TaskStatus.CANCELLED,
    }
)
# The only statuses the transition table lets reach BUDGET_EXHAUSTED (transitions.py:18-32).
_BUDGETED = frozenset({TaskStatus.PLANNING, TaskStatus.EXECUTING, TaskStatus.ASSESSING})


class StepWorkState(BaseModel):
    """The step Work facts decide() needs; gathered with I/O by the coordinator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str
    work_id: str
    status: str
    attention_kind: str | None = None
    attention_id: str | None = None
    attention_summary: str = ""
    gate_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    base_moved_phase: str | None = None
    merged_sha: str | None = None


class _Command(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    def receipt_id(self, revision: int) -> str:
        """Section 8.1: one command per decision, its id derived from the Task revision."""
        return f"{self.kind}:{revision}"


class StartCycle(_Command):
    kind: Literal["start_cycle"] = "start_cycle"
    cycle: int
    scheduled_for: str | None = None


class RunPlanning(_Command):
    kind: Literal["run_planning"] = "run_planning"
    plan_version: int


class ExhaustBudget(_Command):
    kind: Literal["exhaust_budget"] = "exhaust_budget"
    reason: str


class RecordStepOutcome(_Command):
    kind: Literal["record_step_outcome"] = "record_step_outcome"
    step_id: str
    work_id: str
    outcome: str
    merged_sha: str | None = None


class MirrorAttention(_Command):
    kind: Literal["mirror_attention"] = "mirror_attention"
    step_id: str
    work_id: str
    attention_kind: str
    attention_id: str
    summary: str
    gate_id: str | None = None
    evidence_refs: tuple[str, ...] = ()


class SupersedeStep(_Command):
    kind: Literal["supersede_step"] = "supersede_step"
    step_id: str
    work_id: str
    phase: str


class StartStep(_Command):
    kind: Literal["start_step"] = "start_step"
    step_id: str


class ResumeStep(_Command):
    kind: Literal["resume_step"] = "resume_step"
    step_id: str
    work_id: str


class AssessCycle(_Command):
    kind: Literal["assess_cycle"] = "assess_cycle"
    cycle: int


class Replan(_Command):
    kind: Literal["replan"] = "replan"
    plan_version: int
    reason: str


class BlockCycle(_Command):
    kind: Literal["block_cycle"] = "block_cycle"
    reason: str


class CompleteCycle(_Command):
    kind: Literal["complete_cycle"] = "complete_cycle"
    cycle: int
    outcome: str
    next_run_at: str | None = None


Command = (
    StartCycle
    | RunPlanning
    | ExhaustBudget
    | RecordStepOutcome
    | MirrorAttention
    | SupersedeStep
    | StartStep
    | ResumeStep
    | AssessCycle
    | Replan
    | BlockCycle
    | CompleteCycle
)


class CycleState(BaseModel):
    """Pure projection of the Task stream for the current cycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cycle: int = 0
    started_at: datetime | None = None
    plan: AcceptedPlan | None = None
    step_works: dict[str, str] = {}
    issue_urls: dict[str, str] = {}
    step_outcomes: dict[str, str] = {}
    superseded_works: frozenset[str] = frozenset()
    mirrored: frozenset[str] = frozenset()
    assessment: str | None = None


def fold_cycle(events: Sequence[TaskEvent], *, plan_version: int) -> CycleState:
    """Fold the accepted plan and the current cycle's step state out of the stream."""
    cycle = 0
    started_at: datetime | None = None
    step_works: dict[str, str] = {}
    issue_urls: dict[str, str] = {}
    step_outcomes: dict[str, str] = {}
    superseded: set[str] = set()
    mirrored: set[str] = set()
    assessment: str | None = None
    for event in sorted(events, key=lambda item: item.sequence):
        payload = event.payload_json
        if event.event_type is TaskEventType.CYCLE_STARTED:
            cycle = int(payload["cycle"])
            started_at = event.created_at
            step_works, issue_urls, step_outcomes, assessment = {}, {}, {}, None
            superseded = set()
            mirrored = set()
        elif event.event_type is TaskEventType.PLAN_ACCEPTED:
            assessment = None
        elif event.event_type is TaskEventType.STEP_WORK_STARTED:
            step_works[str(payload["step_id"])] = str(payload["work_id"])
            issue_urls[str(payload["step_id"])] = str(payload["issue_url"])
        elif event.event_type is TaskEventType.STEP_WORK_SUPERSEDED:
            superseded.add(str(payload["work_id"]))
        elif event.event_type is TaskEventType.STEP_WORK_OUTCOME:
            step_outcomes[str(payload["step_id"])] = str(payload["outcome"])
        elif event.event_type is TaskEventType.ASSESSMENT_RECORDED:
            assessment = str(payload["verdict"])
        elif event.event_type in {TaskEventType.GATE_REQUESTED, TaskEventType.TASK_MESSAGE}:
            attention_id = payload.get("attention_id")
            if attention_id is not None:
                mirrored.add(str(attention_id))
    return CycleState(
        cycle=cycle,
        started_at=started_at,
        plan=plan_from_events(events, version=plan_version),
        step_works=step_works,
        issue_urls=issue_urls,
        step_outcomes=step_outcomes,
        superseded_works=frozenset(superseded),
        mirrored=frozenset(mirrored),
        assessment=assessment,
    )


def _active(state: CycleState, works: Mapping[str, StepWorkState]) -> StepWorkState | None:
    for step in state.plan.steps:
        if step.id in state.step_outcomes:
            continue
        work = works.get(step.id)
        if work is not None:
            return work
    return None


def _next_ready(state: CycleState) -> PlanStep | None:
    for step in state.plan.steps:
        if step.id in state.step_outcomes or step.id in state.step_works:
            continue
        if all(state.step_outcomes.get(dependency) == "accepted" for dependency in step.depends_on):
            return step
    return None


def _next_run_at(task: Task, now: datetime) -> str | None:
    if task.kind is not TaskKind.SCHEDULED or task.schedule is None or not task.schedule.active:
        return None
    return next_fire(task.schedule.cron, after=now, timezone_name=task.schedule.timezone).isoformat()


def _planning_version(record: TaskRecord, events: Sequence[TaskEvent]) -> int:
    version = record.plan_version + 1
    for event in sorted(events, key=lambda item: item.sequence):
        if event.event_type is TaskEventType.REPLAN_PROPOSED:
            proposed = int(event.payload_json["version"])
            if proposed > record.plan_version:
                version = proposed
    return version


def decide(
    task: Task,
    record: TaskRecord,
    events: Sequence[TaskEvent],
    works: Mapping[str, StepWorkState],
    *,
    budget_used: BudgetUsed,
    now: datetime,
) -> Command | None:
    """The first applicable command, or None while the Task waits on a human or a runtime."""
    if record.status in _WAITING or record.pending_gate is not None:
        return None
    if record.current_cycle >= 1 and record.status in _BUDGETED:
        breach = budget_breach(budget_used, task.budget)
        if breach is not None:
            return ExhaustBudget(reason=breach)
    if record.status is TaskStatus.SCHEDULED:
        if record.next_run_at is None or record.next_run_at > now:
            return None
        return StartCycle(
            cycle=record.current_cycle + 1, scheduled_for=record.next_run_at.isoformat()
        )
    if record.status is TaskStatus.PLANNING:
        return RunPlanning(plan_version=_planning_version(record, events))
    state = fold_cycle(events, plan_version=record.plan_version)
    if state.plan is None:
        return None
    if record.current_cycle == 0:
        return StartCycle(cycle=record.current_cycle + 1)
    active = _active(state, works)
    if active is not None:
        if active.status == "COMPLETE":
            return RecordStepOutcome(
                step_id=active.step_id,
                work_id=active.work_id,
                outcome="accepted",
                merged_sha=active.merged_sha,
            )
        if active.attention_kind is not None:
            if active.attention_id in state.mirrored:
                return None
            return MirrorAttention(
                step_id=active.step_id,
                work_id=active.work_id,
                attention_kind=active.attention_kind,
                attention_id=active.attention_id,
                summary=active.attention_summary,
                gate_id=active.gate_id,
                evidence_refs=active.evidence_refs,
            )
        if active.base_moved_phase is not None:
            return SupersedeStep(
                step_id=active.step_id, work_id=active.work_id, phase=active.base_moved_phase
            )
        return ResumeStep(step_id=active.step_id, work_id=active.work_id)
    ready = _next_ready(state)
    if ready is not None:
        return StartStep(step_id=ready.id)
    if state.assessment is None:
        return AssessCycle(cycle=state.cycle)
    if state.assessment == "accept":
        return CompleteCycle(
            cycle=state.cycle, outcome="succeeded", next_run_at=_next_run_at(task, now)
        )
    if state.assessment == "replan" and budget_used.replans < task.budget.max_replans:
        return Replan(plan_version=record.plan_version + 1, reason="assessment requested a re-plan")
    if state.assessment == "replan":
        return BlockCycle(reason="assessment verdict replan with the re-plan budget spent")
    return BlockCycle(reason=f"assessment verdict {state.assessment}")


__all__ = [
    "AssessCycle",
    "BlockCycle",
    "Command",
    "CompleteCycle",
    "CycleState",
    "ExhaustBudget",
    "MirrorAttention",
    "RecordStepOutcome",
    "Replan",
    "ResumeStep",
    "RunPlanning",
    "StartCycle",
    "StartStep",
    "StepWorkState",
    "SupersedeStep",
    "decide",
    "fold_cycle",
]
