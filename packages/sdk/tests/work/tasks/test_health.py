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
async def test_a_cost_spike_on_a_scheduled_task_holds_needs_you(stores, tmp_path, monkeypatch) -> None:  # noqa: F811
    from sagewai.work.tasks.events import TaskEventType
    from sagewai.work.tasks.models import (
        AttentionOwner,
        BoardColumn,
        Schedule,
        TaskKind,
        TaskStatus,
    )
    from tests.work.tasks.test_coordinator import _fixed_task, _seed

    task_store, _work_store = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    task = task.model_copy(
        update={
            "kind": TaskKind.SCHEDULED,
            "schedule": Schedule(cron="0 8 * * *", timezone="Europe/Berlin"),
        }
    )
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    monkeypatch.setattr(
        coordinator,
        "_act_on_health",
        _forced_alert(coordinator),
    )
    epoch = await task_store.claim(task.id, project_id=task.project_id, owner="r1", ttl_seconds=90)
    for _ in range(20):
        record = await coordinator.drive(record, lease_epoch=epoch)
        if record.status is TaskStatus.SCHEDULED:
            break
    assert record.status is TaskStatus.SCHEDULED
    assert record.attention_owner is AttentionOwner.USER
    assert record.board_column is BoardColumn.NEEDS_YOU
    types = [e.event_type for e in await task_store.read_events(task.id, project_id=task.project_id)]
    assert types[-3:] == [
        TaskEventType.HEALTH_ACTION,
        TaskEventType.ATTENTION_CHANGED,
        TaskEventType.NOTIFICATION_PRESENTED,
    ]


def _forced_alert(coordinator):
    """Drive the alert branch on one cycle; evaluate_health itself is unit-tested above."""
    from sagewai.work.tasks.events import TaskEventType
    from sagewai.work.tasks.health import AlertOperator, HealthSignal

    async def _act(task, record, command, lease_epoch):
        signal = HealthSignal(kind="cost_spike", detail="cycle cost 9 above the median", cycle=1)
        action = AlertOperator(reason=signal.detail)
        health = [
            (TaskEventType.HEALTH_SIGNAL, signal.model_dump(mode="json")),
            (
                TaskEventType.HEALTH_ACTION,
                {**action.model_dump(mode="json"), "cycle": signal.cycle},
            ),
            (
                TaskEventType.ATTENTION_CHANGED,
                {"owner": "user", "reason": f"health:{signal.kind}:{signal.cycle}"},
            ),
            *await coordinator._present(
                task,
                record,
                attention_id=f"health:{signal.kind}:{signal.cycle}",
                summary=signal.detail,
                urgency="today",
            ),
        ]
        return await coordinator._append(record, health, lease_epoch)

    return _act
