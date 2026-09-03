# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Health signals and actions are derived from the Task's own cycle records."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sagewai.work.tasks.health import (
    AlertOperator,
    HealthPolicy,
    PauseSchedule,
    RetryCycle,
    cycle_history,
    evaluate_health,
)
from sagewai.work.tasks.telemetry import ScheduledCycleTelemetry
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_coordinator import stores  # noqa: F401

NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


def _cycle(number: int, *, status: str = "succeeded", seconds: float = 60.0, usd: str = "1.00"):
    return ScheduledCycleTelemetry(
        cycle=number,
        status=status,
        completed_at=NOW + timedelta(hours=number),
        duration_seconds=seconds,
        usd_actual=Decimal(usd),
    )


def test_three_consecutive_failures_pause_the_schedule() -> None:
    cycles = [_cycle(1), _cycle(2), _cycle(3, status="failed"), _cycle(4, status="failed"), _cycle(5, status="failed")]
    signal, action = evaluate_health(cycles, policy=HealthPolicy(), last_action_cycle=None)
    assert signal.kind == "consecutive_failures" and signal.cycle == 5
    assert isinstance(action, PauseSchedule)


def test_one_failure_retries_the_cycle() -> None:
    cycles = [_cycle(1), _cycle(2), _cycle(3), _cycle(4), _cycle(5, status="failed")]
    signal, action = evaluate_health(cycles, policy=HealthPolicy(), last_action_cycle=None)
    assert isinstance(action, RetryCycle)
    assert signal.kind == "consecutive_failures"


def test_a_cost_spike_alerts_the_operator() -> None:
    cycles = [_cycle(number, usd="1.00") for number in range(1, 5)] + [_cycle(5, usd="9.00")]
    signal, action = evaluate_health(cycles, policy=HealthPolicy(), last_action_cycle=None)
    assert signal.kind == "cost_spike"
    assert isinstance(action, AlertOperator) and action.severity == "warning"


def test_a_duration_spike_alerts_the_operator() -> None:
    cycles = [_cycle(number, seconds=60.0) for number in range(1, 5)] + [_cycle(5, seconds=600.0)]
    signal, _action = evaluate_health(cycles, policy=HealthPolicy(), last_action_cycle=None)
    assert signal.kind == "duration_spike"


def test_a_low_success_rate_over_the_window_alerts() -> None:
    cycles = [_cycle(1, status="failed"), _cycle(2), _cycle(3, status="failed"), _cycle(4), _cycle(5)]
    signal, action = evaluate_health(cycles, policy=HealthPolicy(), last_action_cycle=None)
    assert signal.kind == "low_success_rate"
    assert isinstance(action, AlertOperator)


def test_the_cooldown_suppresses_a_second_action_within_one_window() -> None:
    cycles = [_cycle(1), _cycle(2), _cycle(3, status="failed"), _cycle(4, status="failed"), _cycle(5, status="failed")]
    signal, action = evaluate_health(cycles, policy=HealthPolicy(), last_action_cycle=3)
    assert signal is not None and action is None
    _signal, action = evaluate_health(cycles, policy=HealthPolicy(), last_action_cycle=-1)
    assert action is not None


def test_retry_cycle_actions_do_not_hold_the_pause_cooldown() -> None:
    from sagewai.work.tasks.coordinator import TaskCoordinator
    from sagewai.work.tasks.events import TaskEvent, TaskEventType

    retry = TaskEvent(
        id="health-retry-1",
        project_id="project-a",
        task_id="task-health",
        sequence=1,
        event_type=TaskEventType.HEALTH_ACTION,
        actor_type="system",
        actor_ref="coordinator",
        payload_json={"kind": "retry_cycle", "reason": "the last cycle failed", "cycle": 1},
        created_at=NOW,
    )
    signal, action = evaluate_health(
        [_cycle(1, status="failed"), _cycle(2, status="failed"), _cycle(3, status="failed")],
        policy=HealthPolicy(),
        last_action_cycle=TaskCoordinator._last_health_cycle((retry,)),
    )

    assert signal.kind == "consecutive_failures" and signal.cycle == 3
    assert isinstance(action, PauseSchedule)


def test_a_healthy_history_produces_nothing() -> None:
    assert evaluate_health([_cycle(n) for n in range(1, 6)], policy=HealthPolicy(), last_action_cycle=None) == (None, None)
    assert evaluate_health([], policy=HealthPolicy(), last_action_cycle=None) == (None, None)


def test_cycle_history_reads_the_task_stream_and_the_ledger() -> None:
    from sagewai.work.tasks.events import TaskEventType
    from sagewai.work.tasks.models import SpendTotals
    from tests.work.tasks.test_decide import _apply
    from tests.work.tasks.test_store import _record, _task

    task = _task()
    _, events = _apply(
        _record(task),
        [
            (TaskEventType.CYCLE_STARTED, {"cycle": 1, "scheduled_for": None}),
            (TaskEventType.CYCLE_COMPLETED, {"cycle": 1, "outcome": "succeeded", "next_run_at": None}),
        ],
    )
    history = cycle_history(
        events,
        spend={1: SpendTotals(usd_reserved=Decimal("0"), usd_actual=Decimal("2"), unknown_settlements=0, reservations=1)},
    )
    assert [item.cycle for item in history] == [1]
    assert history[0].status == "succeeded" and history[0].usd_actual == Decimal("2")
    assert history[0].duration_seconds == 0.0


@pytest.mark.asyncio
async def test_a_cost_spike_on_a_scheduled_task_holds_needs_you(
    stores, monkeypatch  # noqa: F811
) -> None:  # noqa: F811
    from sagewai.work.tasks.coordinator import TaskCoordinator
    from sagewai.work.tasks.events import TaskEventType
    from sagewai.work.tasks.models import (
        AttentionOwner,
        BoardColumn,
        TaskStatus,
    )
    from sagewai.work.tasks.store import SpendReservation
    from sagewai.work.tasks.writer import TaskWriter
    from tests.work.tasks.test_coordinator import (
        FakeProfileRunner,
        RecordingDecisionChannel,
        _plan_result,
    )
    from tests.work.tasks.test_schedules import _scheduled
    from tests.work.tasks.test_store import _create

    task_store, work_store = stores
    task = _scheduled("task-health")
    record = await _create(task_store, task)
    plan = _plan_result()
    entries = [
        (
            TaskEventType.PLAN_PROPOSED,
            {
                "version": 1,
                "steps": [step.model_dump(mode="json") for step in plan.steps],
                "acceptance_matrix": [
                    item.model_dump(mode="json") for item in plan.acceptance_matrix
                ],
            },
        ),
        (TaskEventType.PLAN_ACCEPTED, {"version": 1}),
        (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.EXECUTING.value}),
    ]
    for cycle in range(1, 7):
        entries.extend(
            [
                (
                    TaskEventType.CYCLE_STARTED,
                    {"cycle": cycle, "scheduled_for": f"2026-09-0{cycle}T08:00:00+00:00"},
                ),
                (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.ASSESSING.value}),
                (
                    TaskEventType.ASSESSMENT_RECORDED,
                    {
                        "cycle": cycle,
                        "attempt_id": f"assess-{cycle}",
                        "matrix_results": [],
                        "gaps": [],
                        "verdict": "accept",
                    },
                ),
                (
                    TaskEventType.CYCLE_COMPLETED,
                    {
                        "cycle": cycle,
                        "outcome": "succeeded",
                        "next_run_at": "2026-09-08T08:00:00+00:00",
                    },
                ),
                (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.SCHEDULED.value}),
                (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.EXECUTING.value}),
            ]
        )
    entries.append(
        (
            TaskEventType.CYCLE_STARTED,
            {"cycle": 7, "scheduled_for": "2026-09-08T08:00:00+00:00"},
        )
    )
    for step in plan.steps:
        entries.extend(
            [
                (
                    TaskEventType.STEP_WORK_STARTED,
                    {
                        "step_id": step.id,
                        "work_id": f"work-{step.id}",
                        "issue_url": f"https://github.com/o/r/issues/{step.id}",
                        "base_sha": "a" * 40,
                    },
                ),
                (
                    TaskEventType.STEP_WORK_OUTCOME,
                    {"step_id": step.id, "work_id": f"work-{step.id}", "outcome": "accepted"},
                ),
            ]
        )
    entries.extend(
        [
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.ASSESSING.value}),
            (
                TaskEventType.ASSESSMENT_RECORDED,
                {
                    "cycle": 7,
                    "attempt_id": "assess-7",
                    "matrix_results": [],
                    "gaps": [],
                    "verdict": "accept",
                },
            ),
        ]
    )
    record = await TaskWriter(task_store).append(record, entries, now=NOW)
    for cycle in range(1, 8):
        await task_store.reserve_spend(
            SpendReservation(
                reservation_id=f"cost-{cycle}",
                project_id=task.project_id,
                task_id=task.id,
                cycle=cycle,
                role="implementer",
                runtime="claude",
                usd_reserved=Decimal("0"),
            )
        )
        await task_store.settle_spend(
            f"cost-{cycle}",
            project_id=task.project_id,
            usd_actual=Decimal("9.00" if cycle == 7 else "1.00"),
        )
    queried_cycles: list[int] = []
    original_spend_totals = task_store.spend_totals

    async def capture_spend_totals(*args, **kwargs):
        queried_cycles.append(kwargs["cycle"])
        return await original_spend_totals(*args, **kwargs)

    monkeypatch.setattr(task_store, "spend_totals", capture_spend_totals)
    channel = RecordingDecisionChannel()
    coordinator = TaskCoordinator(
        task_store=task_store,
        work_store=work_store,
        profile_runner=FakeProfileRunner(work_store, plan_result=plan),
        decision_channels=(channel,),
    )
    epoch = await task_store.claim(task.id, project_id=task.project_id, owner="r1", ttl_seconds=90)
    record = await coordinator.drive(record, lease_epoch=epoch)

    assert record.status is TaskStatus.SCHEDULED
    assert record.attention_owner is AttentionOwner.USER
    assert record.board_column is BoardColumn.NEEDS_YOU
    assert queried_cycles[1:-1] == [3, 4, 5, 6, 7]
    types = [e.event_type for e in await task_store.read_events(task.id, project_id=task.project_id)]
    assert types[-4:] == [
        TaskEventType.HEALTH_SIGNAL,
        TaskEventType.HEALTH_ACTION,
        TaskEventType.ATTENTION_CHANGED,
        TaskEventType.NOTIFICATION_PRESENTED,
    ]
    assert channel.calls[-1].attention_id == "health:cost_spike:7"
