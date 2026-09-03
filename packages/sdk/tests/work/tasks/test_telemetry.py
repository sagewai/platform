# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pure Task telemetry projection tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import (
    CriterionVerification,
    OperatorDisciplineReport,
    ReviewFinding,
    ReviewResult,
    VerificationResult,
)
from sagewai.work.runtime import OperatorResult
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import (
    Budget,
    BudgetUsed,
    SpendTotals,
    TaskKind,
    TaskOrigin,
    TaskRecord,
    TaskStatus,
)
from sagewai.work.tasks.telemetry import derive_task_telemetry

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _at(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)


def _task_event(
    sequence: int,
    event_type: TaskEventType,
    payload: dict[str, Any],
    *,
    at: datetime | None = None,
    task_id: str = "task-1",
) -> TaskEvent:
    return TaskEvent(
        id=f"{task_id}:event:{sequence}",
        project_id="project-a",
        task_id=task_id,
        sequence=sequence,
        event_type=event_type,
        actor_type="system",
        actor_ref="test",
        payload_json=payload,
        created_at=at or _at(sequence),
    )


def _work_event(
    work_id: str,
    sequence: int,
    event_type: WorkEventType,
    payload: dict[str, Any],
    *,
    at: datetime | None = None,
) -> WorkEvent:
    return WorkEvent(
        id=f"{work_id}:event:{sequence}",
        project_id="project-a",
        work_id=work_id,
        sequence=sequence,
        event_type=event_type,
        actor_type="system",
        actor_ref="test",
        payload_json=payload,
        created_at=at or _at(sequence),
    )


def _record(
    *,
    kind: TaskKind = TaskKind.BATCH,
    status: TaskStatus = TaskStatus.EXECUTING,
    next_run_at: datetime | None = None,
) -> TaskRecord:
    return TaskRecord(
        task_id="task-1",
        project_id="project-a",
        kind=kind,
        origin=TaskOrigin.HUMAN,
        title="Build the thing",
        profile="software",
        status=status,
        last_event_sequence=1,
        next_run_at=next_run_at,
        created_at=NOW,
        updated_at=NOW,
    )


def _selection(
    work_id: str,
    sequence: int,
    *,
    run_id: str,
    stage: str = "implement",
    role: str = "implementer",
    position: int = 1,
    runtime: str = "harness",
    reason: str = "initial",
    at: datetime | None = None,
) -> WorkEvent:
    return _work_event(
        work_id,
        sequence,
        WorkEventType.RUNTIME_SELECTED,
        {
            "role": role,
            "stage": stage,
            "run_id": run_id,
            "attempt": position,
            "position": position,
            "runtime": runtime,
            "reason": reason,
        },
        at=at,
    )


def _execution(
    work_id: str,
    sequence: int,
    *,
    run_id: str,
    status: str,
    verification: tuple[str, ...],
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: float | None,
    at: datetime | None = None,
) -> WorkEvent:
    result = OperatorResult(
        project_id="project-a",
        work_id=work_id,
        run_id=run_id,
        status=status,
        summary=f"{run_id} {status}",
        evidence_refs=(),
        artifact_refs=(),
        changes=(),
        verification=verification,
        risks=(),
        action_results=(),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )
    return _work_event(
        work_id,
        sequence,
        WorkEventType.EXECUTION_RECORDED,
        result.model_dump(mode="json"),
        at=at,
    )


def _discipline(work_id: str, sequence: int, *, run_id: str) -> WorkEvent:
    report = OperatorDisciplineReport(
        project_id="project-a",
        work_id=work_id,
        run_id=run_id,
        unsupported_claims=(),
        scope_violations=(),
        permission_violations=(),
        risk_mismatches=(),
        unnecessary_changes=(),
        output_tokens=50,
        changed_files=2,
        diff_lines=14,
        verdict="repair",
    )
    return _work_event(
        work_id,
        sequence,
        WorkEventType.OPERATOR_DISCIPLINE_RECORDED,
        report.model_dump(mode="json"),
    )


def _verification(
    work_id: str,
    sequence: int,
    *,
    run_id: str,
    passed: bool,
    at: datetime | None = None,
) -> WorkEvent:
    result = VerificationResult(
        project_id="project-a",
        contract_id="contract-1",
        attempt_id=run_id,
        stage="implement",
        passed=passed,
        criterion_results=(
            CriterionVerification(
                project_id="project-a",
                contract_id="contract-1",
                criterion_id="unit",
                passed=passed,
                evidence_refs=("artifact://unit",) if passed else (),
            ),
        ),
        evidence_refs=("artifact://unit",) if passed else (),
        profile_context={
            "checks": [
                {
                    "name": "unit",
                    "command": "just smoke",
                    "exit_code": 0 if passed else 1,
                    "artifact_ref": "artifact://unit",
                }
            ]
        },
    )
    return _work_event(
        work_id,
        sequence,
        WorkEventType.VERIFICATION_RECORDED,
        result.model_dump(mode="json"),
        at=at,
    )


def _review(work_id: str, sequence: int, *, run_id: str) -> WorkEvent:
    result = ReviewResult(
        project_id="project-a",
        attempt_id=run_id,
        verdict="repair",
        findings=(
            ReviewFinding(
                project_id="project-a",
                severity="high",
                claim="broken",
                evidence_refs=("artifact://review",),
                required_change="fix it",
            ),
            ReviewFinding(
                project_id="project-a",
                severity="low",
                claim="style",
                evidence_refs=("artifact://review",),
                required_change=None,
            ),
        ),
        introduced_assumptions=(),
        unsupported_claims=(),
        scope_expansions=(),
        unsupported_implementation_choices=(),
    )
    return _work_event(
        work_id,
        sequence,
        WorkEventType.REVIEW_RECORDED,
        result.model_dump(mode="json"),
    )


def test_task_telemetry_projects_attempts_work_cycles_and_project_rates() -> None:
    task_events = (
        _task_event(1, TaskEventType.TASK_CREATED, {"title": "Build the thing"}, at=NOW),
        _task_event(2, TaskEventType.CYCLE_STARTED, {"cycle": 1}, at=_at(1)),
        _task_event(
            3,
            TaskEventType.STEP_WORK_STARTED,
            {"step_id": "step-1", "work_id": "work-1"},
            at=_at(1),
        ),
        _task_event(
            4,
            TaskEventType.STEP_WORK_STARTED,
            {"step_id": "step-2", "work_id": "work-2"},
            at=_at(1),
        ),
        _task_event(
            5,
            TaskEventType.BUDGET_RECORDED,
            {
                "budget_used": BudgetUsed(
                    works=1,
                    attempts=1,
                    seconds=30,
                    usd_actual=Decimal("0.10"),
                    usd_reserved=Decimal("0.40"),
                ).model_dump(mode="json")
            },
            at=_at(4),
        ),
        _task_event(
            6,
            TaskEventType.BUDGET_RECORDED,
            {
                "budget_used": BudgetUsed(
                    works=1,
                    attempts=2,
                    seconds=60,
                    usd_actual=Decimal("0.25"),
                    usd_reserved=Decimal("0.40"),
                ).model_dump(mode="json")
            },
            at=_at(5),
        ),
        _task_event(
            7,
            TaskEventType.ATTENTION_CHANGED,
            {"owner": "user", "reason": "gate:merge"},
            at=_at(6),
        ),
        _task_event(
            8,
            TaskEventType.CYCLE_COMPLETED,
            {"next_run_at": None, "outcome": "failed"},
            at=_at(7),
        ),
    )
    work_events = {
        "work-1": (
            _selection("work-1", 1, run_id="work-1:implement:1", at=_at(1)),
            _work_event(
                "work-1",
                2,
                WorkEventType.STAGE_STARTED,
                {"stage": "implement", "run_id": "work-1:implement:1", "runtime": "harness"},
                at=_at(1),
            ),
            _execution(
                "work-1",
                3,
                run_id="work-1:implement:1",
                status="failed",
                verification=("local tier selected",),
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.0,
                at=_at(2),
            ),
            _discipline("work-1", 4, run_id="work-1:implement:1"),
            _verification("work-1", 5, run_id="work-1:implement:1", passed=False),
            _review("work-1", 6, run_id="work-1:implement:1"),
            _work_event(
                "work-1",
                7,
                WorkEventType.STAGE_COMPLETED,
                {"stage": "implement", "run_id": "work-1:implement:1"},
                at=_at(3),
            ),
            _selection(
                "work-1",
                8,
                run_id="work-1:implement:2",
                position=2,
                runtime="codex",
                reason="escalated",
                at=_at(4),
            ),
            _work_event(
                "work-1",
                9,
                WorkEventType.STAGE_STARTED,
                {"stage": "implement", "run_id": "work-1:implement:2", "runtime": "codex"},
                at=_at(4),
            ),
            _execution(
                "work-1",
                10,
                run_id="work-1:implement:2",
                status="passed",
                verification=(),
                input_tokens=75,
                output_tokens=25,
                cost_usd=None,
                at=_at(5),
            ),
            _verification(
                "work-1",
                11,
                run_id="work-1:implement:2",
                passed=True,
                at=_at(5),
            ),
            _work_event(
                "work-1",
                12,
                WorkEventType.WORK_BLOCKED,
                {"reason": "needs input"},
                at=_at(6),
            ),
            _work_event(
                "work-1",
                13,
                WorkEventType.STAGE_STARTED,
                {"stage": "repair", "run_id": "work-1:repair:1", "runtime": "claude"},
                at=_at(8),
            ),
            _work_event(
                "work-1",
                14,
                WorkEventType.CONTROL_DEGRADED,
                {"failed_preconditions": ("workspace",)},
                at=_at(9),
            ),
            _work_event(
                "work-1",
                15,
                WorkEventType.WORK_SUPERSEDED,
                {"reason": "base_moved"},
                at=_at(10),
            ),
        ),
        "work-2": (
            _selection(
                "work-2",
                1,
                run_id="work-2:implement:1",
                runtime="fleet:org-1:runtime.claude",
                at=_at(1),
            ),
            _execution(
                "work-2",
                2,
                run_id="work-2:implement:1",
                status="passed",
                verification=("fleet tier selected",),
                input_tokens=120,
                output_tokens=60,
                cost_usd=1.25,
                at=_at(2),
            ),
        ),
    }
    project_selections = (
        work_events["work-1"][0],
        work_events["work-1"][7],
        work_events["work-2"][0],
    )

    telemetry = derive_task_telemetry(
        record=_record(),
        task_events=task_events,
        work_events=work_events,
        spend={
            1: SpendTotals(
                usd_reserved=Decimal("0.40"),
                usd_actual=Decimal("0.25"),
                unknown_settlements=1,
                reservations=2,
            )
        },
        budget=Budget(max_cycle_usd=Decimal("3.00")),
        project_selections=project_selections,
        now=_at(20),
    )

    work = telemetry.works[0]
    assert len(work.stage_attempts) == 2
    assert [attempt.runtime for attempt in work.stage_attempts] == ["harness", "codex"]
    first = work.stage_attempts[0]
    assert first.role == "implementer"
    assert first.position == 1
    assert first.selection_note == "local tier selected"
    assert first.started_at == _at(1)
    assert first.duration_seconds == 60.0
    assert first.status == "failed"
    assert first.input_tokens == 100
    assert first.output_tokens == 50
    assert first.cost_usd == 0.0
    assert first.cost_known is True
    assert first.changed_files == 2
    assert first.diff_lines == 14
    assert first.verification_checks == (
        {
            "name": "unit",
            "command": "just smoke",
            "exit_code": 1,
            "artifact_ref": "artifact://unit",
        },
    )
    assert first.review_verdict == "repair"
    assert first.finding_counts == {"high": 1, "low": 1}
    assert first.escalation_reason == "escalated"
    assert work.stage_attempts[1].cost_known is False
    assert work.stage_attempts[1].escalation_reason is None
    assert [(run.at, run.passed, run.checks) for run in work.verification_runs] == [
        (
            _at(5),
            False,
            (
                {
                    "name": "unit",
                    "command": "just smoke",
                    "exit_code": 1,
                    "artifact_ref": "artifact://unit",
                },
            ),
        ),
        (
            _at(5),
            True,
            (
                {
                    "name": "unit",
                    "command": "just smoke",
                    "exit_code": 0,
                    "artifact_ref": "artifact://unit",
                },
            ),
        ),
    ]

    assert [(entry.stage, entry.status, entry.at) for entry in work.stage_timeline] == [
        ("implement", "started", _at(1)),
        ("implement", "completed", _at(3)),
        ("implement", "started", _at(4)),
        ("repair", "started", _at(8)),
    ]
    assert [(entry.kind, entry.at, entry.resolved_at) for entry in work.attention_history] == [
        ("blocked", _at(6), _at(8)),
        ("control_degraded", _at(9), _at(10)),
    ]

    cycle = telemetry.cycles[0]
    assert cycle.cycle == 1
    assert cycle.outcome == "failed"
    assert cycle.usd_actual == Decimal("0.25")
    assert cycle.usd_reserved == Decimal("0.40")
    assert cycle.usd_unknown == 1
    assert cycle.limits == Budget(max_cycle_usd=Decimal("3.00"))
    assert cycle.worst_case_next_attempt is None
    assert cycle.free_attempts == 1
    assert cycle.paid_attempts == 2
    assert cycle.by_device == {"local": 2, "fleet:org-1": 1}
    assert [(point.at, point.usd_actual) for point in cycle.burn_series] == [
        (_at(4), Decimal("0.10")),
        (_at(5), Decimal("0.25")),
    ]
    assert telemetry.project.escalation_rate_per_role == {"implementer": 1 / 3}
    assert telemetry.scheduled is None


def test_scheduled_telemetry_projects_cycle_health_and_overdue_state() -> None:
    task_events = (
        _task_event(1, TaskEventType.CYCLE_STARTED, {"cycle": 1}, at=_at(1)),
        _task_event(
            2,
            TaskEventType.CYCLE_COMPLETED,
            {"next_run_at": _at(10).isoformat(), "outcome": "succeeded"},
            at=_at(2),
        ),
        _task_event(3, TaskEventType.CYCLE_STARTED, {"cycle": 2}, at=_at(11)),
        _task_event(
            4,
            TaskEventType.CYCLE_COMPLETED,
            {"next_run_at": _at(20).isoformat(), "outcome": "failed"},
            at=_at(12),
        ),
        _task_event(5, TaskEventType.CYCLE_STARTED, {"cycle": 3}, at=_at(21)),
        _task_event(
            6,
            TaskEventType.CYCLE_COMPLETED,
            {"next_run_at": _at(30).isoformat(), "outcome": "failed"},
            at=_at(22),
        ),
    )

    telemetry = derive_task_telemetry(
        record=_record(
            kind=TaskKind.SCHEDULED,
            status=TaskStatus.SCHEDULED,
            next_run_at=_at(30),
        ),
        task_events=task_events,
        work_events={},
        spend={
            1: SpendTotals(
                usd_reserved=Decimal("0"),
                usd_actual=Decimal("0.10"),
                unknown_settlements=0,
                reservations=1,
            ),
            2: SpendTotals(
                usd_reserved=Decimal("0"),
                usd_actual=Decimal("0.20"),
                unknown_settlements=0,
                reservations=1,
            ),
            3: SpendTotals(
                usd_reserved=Decimal("0"),
                usd_actual=Decimal("0.30"),
                unknown_settlements=0,
                reservations=1,
            ),
        },
        budget=Budget(),
        project_selections=(),
        now=_at(40),
    )

    assert telemetry.scheduled is not None
    assert [
        (cycle.cycle, cycle.status, cycle.duration_seconds, cycle.usd_actual)
        for cycle in telemetry.scheduled.cycles
    ] == [
        (1, "succeeded", 60.0, Decimal("0.10")),
        (2, "failed", 60.0, Decimal("0.20")),
        (3, "failed", 60.0, Decimal("0.30")),
    ]
    assert telemetry.scheduled.success_rate == 1 / 3
    assert telemetry.scheduled.consecutive_failures == 2
    assert telemetry.scheduled.last_success_at == _at(2)
    assert telemetry.scheduled.overdue is True


def test_cycle_telemetry_keys_burn_and_spend_by_started_cycle() -> None:
    task_events = (
        _task_event(1, TaskEventType.CYCLE_STARTED, {"cycle": 1}, at=_at(1)),
        _task_event(
            2,
            TaskEventType.STEP_WORK_STARTED,
            {"step_id": "step-1", "work_id": "work-1"},
            at=_at(1),
        ),
        _task_event(
            3,
            TaskEventType.BUDGET_RECORDED,
            {
                "budget_used": BudgetUsed(
                    works=1,
                    attempts=1,
                    seconds=30,
                    usd_actual=Decimal("0.10"),
                    usd_reserved=Decimal("0.40"),
                ).model_dump(mode="json")
            },
            at=_at(2),
        ),
        _task_event(
            4,
            TaskEventType.CYCLE_COMPLETED,
            {"next_run_at": _at(10).isoformat(), "outcome": "succeeded"},
            at=_at(3),
        ),
        _task_event(5, TaskEventType.CYCLE_STARTED, {"cycle": 2}, at=_at(10)),
        _task_event(
            6,
            TaskEventType.STEP_WORK_STARTED,
            {"step_id": "step-2", "work_id": "work-2"},
            at=_at(10),
        ),
        _task_event(
            7,
            TaskEventType.BUDGET_RECORDED,
            {
                "budget_used": BudgetUsed(
                    works=1,
                    attempts=1,
                    seconds=20,
                    usd_actual=Decimal("0.70"),
                    usd_reserved=Decimal("1.00"),
                ).model_dump(mode="json")
            },
            at=_at(11),
        ),
        _task_event(
            8,
            TaskEventType.CYCLE_COMPLETED,
            {"next_run_at": _at(20).isoformat(), "outcome": "failed"},
            at=_at(12),
        ),
    )

    telemetry = derive_task_telemetry(
        record=_record(kind=TaskKind.SCHEDULED),
        task_events=task_events,
        work_events={
            "work-1": (
                _selection("work-1", 1, run_id="work-1:implement:1", runtime="harness"),
            ),
            "work-2": (
                _selection("work-2", 1, run_id="work-2:implement:1", runtime="claude"),
            ),
        },
        spend={
            1: SpendTotals(
                usd_reserved=Decimal("0.40"),
                usd_actual=Decimal("0.10"),
                unknown_settlements=0,
                reservations=1,
            ),
            2: SpendTotals(
                usd_reserved=Decimal("1.00"),
                usd_actual=Decimal("0.70"),
                unknown_settlements=0,
                reservations=1,
            ),
        },
        budget=Budget(),
        project_selections=(),
        now=_at(13),
    )

    assert [cycle.cycle for cycle in telemetry.cycles] == [1, 2]
    assert [cycle.usd_actual for cycle in telemetry.cycles] == [
        Decimal("0.10"),
        Decimal("0.70"),
    ]
    assert [[point.usd_actual for point in cycle.burn_series] for cycle in telemetry.cycles] == [
        [Decimal("0.10")],
        [Decimal("0.70")],
    ]
    assert telemetry.scheduled is not None
    assert [
        (cycle.cycle, cycle.duration_seconds, cycle.usd_actual)
        for cycle in telemetry.scheduled.cycles
    ] == [
        (1, 120.0, Decimal("0.10")),
        (2, 120.0, Decimal("0.70")),
    ]


def test_worst_case_next_attempt_follows_latest_runtime_kind() -> None:
    budget = Budget(claude_max_budget_usd_per_attempt=Decimal("2.50"))
    spend = {
        cycle: SpendTotals(
            usd_reserved=Decimal("0"),
            usd_actual=Decimal("0"),
            unknown_settlements=0,
            reservations=0,
        )
        for cycle in (1, 2, 3)
    }
    task_events = (
        _task_event(1, TaskEventType.CYCLE_STARTED, {"cycle": 1}, at=_at(1)),
        _task_event(2, TaskEventType.STEP_WORK_STARTED, {"step_id": "s1", "work_id": "harness"}),
        _task_event(3, TaskEventType.CYCLE_STARTED, {"cycle": 2}, at=_at(2)),
        _task_event(4, TaskEventType.STEP_WORK_STARTED, {"step_id": "s2", "work_id": "claude"}),
        _task_event(5, TaskEventType.CYCLE_STARTED, {"cycle": 3}, at=_at(3)),
        _task_event(6, TaskEventType.STEP_WORK_STARTED, {"step_id": "s3", "work_id": "codex"}),
    )

    telemetry = derive_task_telemetry(
        record=_record(),
        task_events=task_events,
        work_events={
            "harness": (
                _selection("harness", 1, run_id="harness:implement:1", runtime="harness"),
            ),
            "claude": (
                _selection("claude", 1, run_id="claude:implement:1", runtime="claude"),
            ),
            "codex": (
                _selection("codex", 1, run_id="codex:implement:1", runtime="codex"),
            ),
        },
        spend=spend,
        budget=budget,
        project_selections=(),
        now=_at(10),
    )

    assert [cycle.worst_case_next_attempt for cycle in telemetry.cycles] == [
        Decimal("0"),
        Decimal("2.50"),
        None,
    ]


def test_escalation_reason_uses_next_selection_only() -> None:
    events = (
        _selection("work-1", 1, run_id="work-1:implement:1", reason="initial", at=_at(1)),
        _execution(
            "work-1",
            2,
            run_id="work-1:implement:1",
            status="passed",
            verification=(),
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.0,
            at=_at(2),
        ),
        _selection(
            "work-1",
            3,
            run_id="work-1:implement:2",
            position=2,
            reason="repair",
            at=_at(3),
        ),
        _selection(
            "work-1",
            4,
            run_id="work-1:implement:3",
            position=3,
            runtime="claude",
            reason="escalated",
            at=_at(4),
        ),
    )

    telemetry = derive_task_telemetry(
        record=_record(),
        task_events=(
            _task_event(1, TaskEventType.CYCLE_STARTED, {"cycle": 1}),
            _task_event(2, TaskEventType.STEP_WORK_STARTED, {"step_id": "s1", "work_id": "work-1"}),
        ),
        work_events={"work-1": events},
        spend={
            1: SpendTotals(
                usd_reserved=Decimal("0"),
                usd_actual=Decimal("0"),
                unknown_settlements=0,
                reservations=0,
            )
        },
        budget=Budget(),
        project_selections=(),
        now=_at(10),
    )

    attempts = telemetry.works[0].stage_attempts
    assert [attempt.escalation_reason for attempt in attempts] == [None, "escalated", None]


def test_in_flight_attempt_projects_unknown_execution_fields() -> None:
    telemetry = derive_task_telemetry(
        record=_record(),
        task_events=(
            _task_event(1, TaskEventType.CYCLE_STARTED, {"cycle": 1}),
            _task_event(2, TaskEventType.STEP_WORK_STARTED, {"step_id": "s1", "work_id": "work-1"}),
        ),
        work_events={
            "work-1": (
                _selection("work-1", 1, run_id="work-1:implement:1", runtime="claude"),
            )
        },
        spend={
            1: SpendTotals(
                usd_reserved=Decimal("0"),
                usd_actual=Decimal("0"),
                unknown_settlements=0,
                reservations=0,
            )
        },
        budget=Budget(),
        project_selections=(),
        now=_at(10),
    )

    attempt = telemetry.works[0].stage_attempts[0]
    assert attempt.status is None
    assert attempt.duration_seconds is None
    assert attempt.cost_known is False


def test_scheduled_overdue_false_when_future_or_not_scheduled_status() -> None:
    task_events = (_task_event(1, TaskEventType.CYCLE_STARTED, {"cycle": 1}),)
    spend = {
        1: SpendTotals(
            usd_reserved=Decimal("0"),
            usd_actual=Decimal("0"),
            unknown_settlements=0,
            reservations=0,
        )
    }

    future = derive_task_telemetry(
        record=_record(
            kind=TaskKind.SCHEDULED,
            status=TaskStatus.SCHEDULED,
            next_run_at=_at(20),
        ),
        task_events=task_events,
        work_events={},
        spend=spend,
        budget=Budget(),
        project_selections=(),
        now=_at(10),
    )
    executing = derive_task_telemetry(
        record=_record(
            kind=TaskKind.SCHEDULED,
            status=TaskStatus.EXECUTING,
            next_run_at=_at(1),
        ),
        task_events=task_events,
        work_events={},
        spend=spend,
        budget=Budget(),
        project_selections=(),
        now=_at(10),
    )

    assert future.scheduled is not None
    assert future.scheduled.overdue is False
    assert executing.scheduled is not None
    assert executing.scheduled.overdue is False
    assert executing.scheduled.success_rate is None
