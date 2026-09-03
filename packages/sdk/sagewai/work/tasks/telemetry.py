# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Read-only Task telemetry projection."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import (
    Budget,
    BudgetUsed,
    SpendTotals,
    TaskKind,
    TaskRecord,
    TaskStatus,
)


class StageAttemptTelemetry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str
    runtime: str
    position: int
    selection_note: str | None
    started_at: datetime
    duration_seconds: float | None
    status: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    cost_known: bool
    changed_files: int | None
    diff_lines: int | None
    verification_checks: tuple[dict[str, Any], ...]
    review_verdict: str | None
    finding_counts: dict[str, int]
    escalation_reason: str | None


class VerificationRunTelemetry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: str
    at: datetime
    passed: bool
    checks: tuple[dict[str, Any], ...]


class StageTimelineEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: str
    status: str
    at: datetime


class AttentionHistoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    at: datetime
    resolved_at: datetime | None


class WorkTelemetry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    work_id: str
    stage_attempts: tuple[StageAttemptTelemetry, ...]
    verification_runs: tuple[VerificationRunTelemetry, ...]
    stage_timeline: tuple[StageTimelineEntry, ...]
    attention_history: tuple[AttentionHistoryEntry, ...]


class BurnSeriesPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    at: datetime
    usd_actual: Decimal


class CycleTelemetry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cycle: int
    outcome: str | None
    usd_actual: Decimal
    usd_reserved: Decimal
    usd_unknown: int
    limits: Budget
    worst_case_next_attempt: Decimal | None
    free_attempts: int
    paid_attempts: int
    by_device: dict[str, int]
    burn_series: tuple[BurnSeriesPoint, ...]


class ScheduledCycleTelemetry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cycle: int
    status: str
    completed_at: datetime | None
    duration_seconds: float | None
    usd_actual: Decimal


class ScheduledTelemetry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cycles: tuple[ScheduledCycleTelemetry, ...]
    success_rate: float | None
    consecutive_failures: int
    last_success_at: datetime | None
    overdue: bool


class ProjectTelemetry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    escalation_rate_per_role: dict[str, float]


class TaskTelemetry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    project_id: str
    works: tuple[WorkTelemetry, ...]
    cycles: tuple[CycleTelemetry, ...]
    scheduled: ScheduledTelemetry | None
    project: ProjectTelemetry


def derive_task_telemetry(
    *,
    record: TaskRecord,
    task_events: Sequence[TaskEvent],
    work_events: Mapping[str, Sequence[WorkEvent]],
    spend: Mapping[int, SpendTotals],
    budget: Budget,
    project_selections: Sequence[WorkEvent],
    now: datetime,
) -> TaskTelemetry:
    """STEP_WORK_STARTED and BUDGET_RECORDED follow their cycle's CYCLE_STARTED; violations raise."""
    ordered_task_events = sorted(task_events, key=lambda event: event.sequence)
    cycle_order, cycle_work_ids = _cycle_work_index(ordered_task_events)
    cycle_starts, cycle_outcomes, cycle_completed_at, burn_series = _cycle_events(
        ordered_task_events
    )
    work_telemetry = tuple(
        _work_telemetry(work_id, events)
        for work_id, events in work_events.items()
    )
    return TaskTelemetry(
        task_id=record.task_id,
        project_id=record.project_id,
        works=work_telemetry,
        cycles=tuple(
            _cycle_telemetry(
                cycle=cycle,
                outcome=cycle_outcomes.get(cycle),
                work_ids=cycle_work_ids[cycle],
                work_events=work_events,
                spend=spend[cycle],
                budget=budget,
                burn_series=burn_series[cycle],
            )
            for cycle in cycle_order
        ),
        scheduled=_scheduled_telemetry(
            record,
            cycle_order,
            cycle_starts,
            cycle_outcomes,
            cycle_completed_at,
            spend,
            now,
        ),
        project=_project_telemetry(project_selections),
    )


def _work_telemetry(work_id: str, events: Sequence[WorkEvent]) -> WorkTelemetry:
    ordered = sorted(events, key=lambda event: event.sequence)
    execution_by_run = _events_by_run(ordered, WorkEventType.EXECUTION_RECORDED)
    discipline_by_run = _events_by_run(ordered, WorkEventType.OPERATOR_DISCIPLINE_RECORDED)
    verification_by_attempt = _events_by_attempt(ordered, WorkEventType.VERIFICATION_RECORDED)
    review_by_attempt = _events_by_attempt(ordered, WorkEventType.REVIEW_RECORDED)
    selections = [event for event in ordered if event.event_type is WorkEventType.RUNTIME_SELECTED]
    attempts: list[StageAttemptTelemetry] = []
    for selection in selections:
        payload = selection.payload_json
        run_id = str(payload["run_id"])
        execution = execution_by_run.get(run_id)
        attempts.append(
            StageAttemptTelemetry(
                role=str(payload["role"]),
                runtime=str(payload["runtime"]),
                position=int(payload["position"]),
                selection_note=_selection_note(execution),
                started_at=selection.created_at,
                duration_seconds=_duration(selection.created_at, execution),
                status=None if execution is None else str(execution.payload_json["status"]),
                input_tokens=_optional_int(execution, "input_tokens"),
                output_tokens=_optional_int(execution, "output_tokens"),
                cost_usd=_optional_float(execution, "cost_usd"),
                cost_known=execution is not None
                and execution.payload_json["cost_usd"] is not None,
                changed_files=_optional_int(discipline_by_run.get(run_id), "changed_files"),
                diff_lines=_optional_int(discipline_by_run.get(run_id), "diff_lines"),
                verification_checks=_verification_checks(verification_by_attempt.get(run_id)),
                review_verdict=_review_verdict(review_by_attempt.get(run_id)),
                finding_counts=_finding_counts(review_by_attempt.get(run_id)),
                escalation_reason=_escalation_reason(selection, selections),
            )
        )
    verification_runs = tuple(
        VerificationRunTelemetry(
            attempt_id=str(event.payload_json["attempt_id"]),
            at=event.created_at,
            passed=bool(event.payload_json["passed"]),
            checks=_verification_checks(event),
        )
        for event in ordered
        if event.event_type is WorkEventType.VERIFICATION_RECORDED
    )
    return WorkTelemetry(
        work_id=work_id,
        stage_attempts=tuple(attempts),
        verification_runs=verification_runs,
        stage_timeline=_stage_timeline(ordered),
        attention_history=_attention_history(ordered),
    )


def _events_by_run(
    events: Sequence[WorkEvent],
    event_type: WorkEventType,
) -> dict[str, WorkEvent]:
    return {
        str(event.payload_json["run_id"]): event
        for event in events
        if event.event_type is event_type
    }


def _events_by_attempt(
    events: Sequence[WorkEvent],
    event_type: WorkEventType,
) -> dict[str, WorkEvent]:
    """Return events keyed by attempt id; the latest event wins for reused run ids."""
    return {
        str(event.payload_json["attempt_id"]): event
        for event in events
        if event.event_type is event_type
    }


def _selection_note(execution: WorkEvent | None) -> str | None:
    if execution is None:
        return None
    verification = execution.payload_json["verification"]
    return str(verification[0]) if verification else None


def _duration(started_at: datetime, execution: WorkEvent | None) -> float | None:
    if execution is None:
        return None
    return (execution.created_at - started_at).total_seconds()


def _optional_int(event: WorkEvent | None, field: str) -> int | None:
    value = None if event is None else event.payload_json[field]
    return None if value is None else int(value)


def _optional_float(event: WorkEvent | None, field: str) -> float | None:
    value = None if event is None else event.payload_json[field]
    return None if value is None else float(value)


def _verification_checks(event: WorkEvent | None) -> tuple[dict[str, Any], ...]:
    if event is None:
        return ()
    # checks is emitted by the software profile, not the generic verification model.
    checks = event.payload_json["profile_context"].get("checks", ())
    return tuple(dict(check) for check in checks)


def _review_verdict(event: WorkEvent | None) -> str | None:
    return None if event is None else str(event.payload_json["verdict"])


def _finding_counts(event: WorkEvent | None) -> dict[str, int]:
    if event is None:
        return {}
    return dict(
        Counter(str(finding["severity"]) for finding in event.payload_json["findings"])
    )


def _escalation_reason(
    selection: WorkEvent,
    selections: Sequence[WorkEvent],
) -> str | None:
    stage = selection.payload_json["stage"]
    for candidate in selections:
        if candidate.sequence <= selection.sequence:
            continue
        if candidate.payload_json["stage"] == stage:
            reason = str(candidate.payload_json["reason"])
            return reason if reason == "escalated" else None
    return None


def _stage_timeline(events: Sequence[WorkEvent]) -> tuple[StageTimelineEntry, ...]:
    entries: list[StageTimelineEntry] = []
    for event in events:
        if event.event_type is WorkEventType.STAGE_STARTED:
            entries.append(
                StageTimelineEntry(
                    stage=str(event.payload_json["stage"]),
                    status="started",
                    at=event.created_at,
                )
            )
        elif event.event_type is WorkEventType.STAGE_COMPLETED:
            entries.append(
                StageTimelineEntry(
                    stage=str(event.payload_json["stage"]),
                    status="completed",
                    at=event.created_at,
                )
            )
    return tuple(entries)


def _attention_history(events: Sequence[WorkEvent]) -> tuple[AttentionHistoryEntry, ...]:
    entries: list[AttentionHistoryEntry] = []
    for index, event in enumerate(events):
        if event.event_type not in {WorkEventType.WORK_BLOCKED, WorkEventType.CONTROL_DEGRADED}:
            continue
        resolved_at = next(
            (
                candidate.created_at
                for candidate in events[index + 1 :]
                if candidate.event_type
                in {WorkEventType.STAGE_STARTED, WorkEventType.WORK_SUPERSEDED}
            ),
            None,
        )
        entries.append(
            AttentionHistoryEntry(
                kind=(
                    "blocked"
                    if event.event_type is WorkEventType.WORK_BLOCKED
                    else "control_degraded"
                ),
                at=event.created_at,
                resolved_at=resolved_at,
            )
        )
    return tuple(entries)


def _cycle_work_index(
    events: Sequence[TaskEvent],
) -> tuple[list[int], dict[int, set[str]]]:
    cycle_order: list[int] = []
    cycle_work_ids: dict[int, set[str]] = defaultdict(set)
    current_cycle: int | None = None
    for event in events:
        if event.event_type is TaskEventType.CYCLE_STARTED:
            current_cycle = int(event.payload_json["cycle"])
            cycle_order.append(current_cycle)
        elif event.event_type is TaskEventType.STEP_WORK_STARTED:
            work_id = str(event.payload_json["work_id"])
            cycle_work_ids[int(current_cycle)].add(work_id)
    return cycle_order, cycle_work_ids


def _cycle_events(
    events: Sequence[TaskEvent],
) -> tuple[dict[int, datetime], dict[int, str], dict[int, datetime], dict[int, list[BurnSeriesPoint]]]:
    current_cycle: int | None = None
    starts: dict[int, datetime] = {}
    outcomes: dict[int, str] = {}
    completed_at: dict[int, datetime] = {}
    burn_series: dict[int, list[BurnSeriesPoint]] = defaultdict(list)
    for event in events:
        if event.event_type is TaskEventType.CYCLE_STARTED:
            current_cycle = int(event.payload_json["cycle"])
            starts[current_cycle] = event.created_at
        elif event.event_type is TaskEventType.BUDGET_RECORDED:
            budget_used = BudgetUsed.model_validate(event.payload_json["budget_used"])
            burn_series[int(current_cycle)].append(
                BurnSeriesPoint(at=event.created_at, usd_actual=budget_used.usd_actual)
            )
        elif event.event_type is TaskEventType.CYCLE_COMPLETED:
            cycle = int(current_cycle)
            outcome = str(event.payload_json["outcome"])
            outcomes[cycle] = outcome
            completed_at[cycle] = event.created_at
    return starts, outcomes, completed_at, burn_series


def _cycle_telemetry(
    *,
    cycle: int,
    outcome: str | None,
    work_ids: set[str],
    work_events: Mapping[str, Sequence[WorkEvent]],
    spend: SpendTotals,
    budget: Budget,
    burn_series: Sequence[BurnSeriesPoint],
) -> CycleTelemetry:
    selections = [
        event
        for work_id in work_ids
        for event in work_events[work_id]
        if event.event_type is WorkEventType.RUNTIME_SELECTED
    ]
    devices: Counter[str] = Counter(
        _device(str(event.payload_json["runtime"])) for event in selections
    )
    return CycleTelemetry(
        cycle=cycle,
        outcome=outcome,
        usd_actual=spend.usd_actual,
        usd_reserved=spend.usd_reserved,
        usd_unknown=spend.unknown_settlements,
        limits=budget,
        worst_case_next_attempt=_worst_case_next_attempt(selections, budget),
        free_attempts=sum(
            _runtime_kind(str(event.payload_json["runtime"])) == "harness"
            for event in selections
        ),
        paid_attempts=sum(
            _runtime_kind(str(event.payload_json["runtime"])) != "harness"
            for event in selections
        ),
        by_device=dict(devices),
        burn_series=tuple(sorted(burn_series, key=lambda point: point.at)),
    )


def _worst_case_next_attempt(
    selections: Sequence[WorkEvent],
    budget: Budget,
) -> Decimal | None:
    if not selections:
        return None
    latest = max(selections, key=lambda event: (event.created_at, event.sequence))
    kind = _runtime_kind(str(latest.payload_json["runtime"]))
    if kind == "harness":
        return Decimal("0")
    if kind == "claude":
        return budget.claude_max_budget_usd_per_attempt
    return None


def _runtime_kind(runtime: str) -> str:
    if runtime == "harness" or runtime.endswith(":runtime.harness"):
        return "harness"
    if runtime == "claude" or runtime.endswith(":runtime.claude"):
        return "claude"
    return "codex"


def _device(runtime: str) -> str:
    if runtime.startswith("fleet:"):
        return ":".join(runtime.split(":", 2)[:2])
    return "local"


def _scheduled_telemetry(
    record: TaskRecord,
    cycle_order: Sequence[int],
    cycle_starts: Mapping[int, datetime],
    cycle_outcomes: Mapping[int, str],
    cycle_completed_at: Mapping[int, datetime],
    spend: Mapping[int, SpendTotals],
    now: datetime,
) -> ScheduledTelemetry | None:
    if record.kind is not TaskKind.SCHEDULED:
        return None
    cycles = tuple(
        _scheduled_cycle_telemetry(
            cycle=cycle,
            started_at=cycle_starts[cycle],
            outcome=cycle_outcomes.get(cycle),
            completed_at=cycle_completed_at.get(cycle),
            usd_actual=spend[cycle].usd_actual,
        )
        for cycle in cycle_order
    )
    completed_cycles = tuple(cycle for cycle in cycles if cycle.completed_at is not None)
    succeeded = sum(cycle.status == "succeeded" for cycle in completed_cycles)
    last_success_at = next(
        (
            cycle.completed_at
            for cycle in reversed(completed_cycles)
            if cycle.status == "succeeded"
        ),
        None,
    )
    consecutive_failures = 0
    for cycle in reversed(completed_cycles):
        if cycle.status != "failed":
            break
        consecutive_failures += 1
    return ScheduledTelemetry(
        cycles=cycles,
        success_rate=succeeded / len(completed_cycles) if completed_cycles else None,
        consecutive_failures=consecutive_failures,
        last_success_at=last_success_at,
        overdue=(
            record.next_run_at is not None
            and record.next_run_at < now
            and record.status is TaskStatus.SCHEDULED
        ),
    )


def _scheduled_cycle_telemetry(
    *,
    cycle: int,
    started_at: datetime,
    outcome: str | None,
    completed_at: datetime | None,
    usd_actual: Decimal,
) -> ScheduledCycleTelemetry:
    return ScheduledCycleTelemetry(
        cycle=cycle,
        status=outcome if outcome is not None else "running",
        completed_at=completed_at,
        duration_seconds=(
            None if completed_at is None else (completed_at - started_at).total_seconds()
        ),
        usd_actual=usd_actual,
    )


def _project_telemetry(project_selections: Sequence[WorkEvent]) -> ProjectTelemetry:
    totals: Counter[str] = Counter()
    escalated: Counter[str] = Counter()
    for event in project_selections:
        role = str(event.payload_json["role"])
        totals[role] += 1
        if event.payload_json["reason"] == "escalated":
            escalated[role] += 1
    return ProjectTelemetry(
        escalation_rate_per_role={
            role: escalated[role] / total
            for role, total in totals.items()
        }
    )


__all__ = [
    "AttentionHistoryEntry",
    "BurnSeriesPoint",
    "CycleTelemetry",
    "ProjectTelemetry",
    "ScheduledCycleTelemetry",
    "ScheduledTelemetry",
    "StageAttemptTelemetry",
    "StageTimelineEntry",
    "TaskTelemetry",
    "VerificationRunTelemetry",
    "WorkTelemetry",
    "derive_task_telemetry",
]
