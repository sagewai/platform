# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Due scheduled Tasks are listed off one index and each fire runs exactly once."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sagewai.work.tasks.events import TaskEventType
from sagewai.work.tasks.models import Schedule, TaskKind, TaskStatus
from sagewai.work.tasks.store import TaskStore
from sagewai.work.tasks.writer import TaskWriter
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import NOW, _create, _task


def _scheduled(task_id: str = "task-s"):
    task = _task(task_id)
    return task.model_copy(
        update={
            "kind": TaskKind.SCHEDULED,
            "schedule": Schedule(cron="0 8 * * *", timezone="Europe/Berlin"),
        }
    )


@pytest.fixture
async def store(dialect_engine) -> TaskStore:  # noqa: F811
    result = TaskStore(engine=dialect_engine)
    await result.init()
    return result


async def _mark_scheduled(store: TaskStore, task_id: str, run_at: datetime) -> None:
    record = await store.load_record(task_id, project_id="project-a")
    await TaskWriter(store).append(
        record,
        [
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.EXECUTING.value}),
            (TaskEventType.CYCLE_STARTED, {"cycle": 1, "scheduled_for": None}),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.ASSESSING.value}),
            (
                TaskEventType.CYCLE_COMPLETED,
                {
                    "cycle": 1,
                    "outcome": "succeeded",
                    "next_run_at": run_at.isoformat(),
                },
            ),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.SCHEDULED.value}),
        ],
        now=NOW,
    )


@pytest.mark.asyncio
async def test_list_due_returns_only_scheduled_tasks_whose_run_time_has_passed(store) -> None:
    due = _scheduled("task-due")
    later = _scheduled("task-later")
    planning = _task("task-planning")
    for task in (due, later, planning):
        await _create(store, task)
    for task, run_at in ((due, NOW - timedelta(minutes=1)), (later, NOW + timedelta(hours=1))):
        await _mark_scheduled(store, task.id, run_at)
    records = await store.list_due(project_id="project-a", now=NOW, limit=10)
    assert [record.task_id for record in records] == ["task-due"]
    assert await store.list_due(project_id="project-b", now=NOW, limit=10) == []


@pytest.mark.asyncio
async def test_list_due_orders_by_next_run_at_and_honours_the_limit(store) -> None:
    for index in range(3):
        task = _scheduled(f"task-{index}")
        await _create(store, task)
        await _mark_scheduled(store, task.id, NOW - timedelta(minutes=10 - index))
    records = await store.list_due(project_id="project-a", now=NOW, limit=2)
    assert [record.task_id for record in records] == ["task-0", "task-1"]


@pytest.mark.asyncio
async def test_a_missed_fire_runs_once(store) -> None:
    scheduled_for = (NOW - timedelta(hours=6)).isoformat()
    assert await store.record_command(
        task_id="task-s", project_id="project-a", command_id=f"cycle:{scheduled_for}", payload={}
    )
    assert not await store.record_command(
        task_id="task-s", project_id="project-a", command_id=f"cycle:{scheduled_for}", payload={}
    )


def test_the_next_fire_keeps_the_wall_clock_across_the_spring_forward_night() -> None:
    """Berlin's 08:00 schedule stays 08:00 local: 07:00 UTC before, 06:00 UTC after."""
    from sagewai.work.tasks.decide import _next_run_at

    task = _scheduled()
    assert _next_run_at(task, datetime(2026, 3, 28, 6, 30, tzinfo=timezone.utc)) == (
        "2026-03-28T07:00:00+00:00"
    )
    assert _next_run_at(task, datetime(2026, 3, 28, 7, 30, tzinfo=timezone.utc)) == (
        "2026-03-29T06:00:00+00:00"
    )
    assert _next_run_at(_task("batch"), datetime(2026, 3, 28, 7, 30, tzinfo=timezone.utc)) is None
