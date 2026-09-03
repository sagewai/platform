# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The runner claims, heartbeats, drives, and releases; a failure never kills the loop."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from sagewai.work.tasks.runner import TaskCoordinatorRunner
from sagewai.work.tasks.store import TaskStore
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_schedules import _mark_scheduled, _scheduled
from tests.work.tasks.test_store import _create, _task


class RecordingDriver:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, int]] = []
        self.fail = fail
        self.observed_leases: list[tuple[str | None, int]] = []
        self.store: TaskStore | None = None

    async def drive(self, record, *, lease_epoch):
        if self.store is not None:
            held = await self.store.load_record(record.task_id, project_id=record.project_id)
            self.observed_leases.append((held.lease_owner, held.lease_epoch))
        self.calls.append((record.task_id, lease_epoch))
        if self.fail:
            raise RuntimeError("drive exploded")
        return record


@pytest.fixture
async def store(dialect_engine) -> TaskStore:  # noqa: F811
    result = TaskStore(engine=dialect_engine)
    await result.init()
    return result


def _runner(store, driver, **kwargs):
    return TaskCoordinatorRunner(
        task_store=store,
        driver=driver,
        list_project_ids=lambda: _projects(),
        owner="runner-1",
        **kwargs,
    )


async def _projects():
    return ["project-a"]


@pytest.mark.asyncio
async def test_a_tick_claims_drives_and_releases(store) -> None:
    task = _task()
    await _create(store, task)
    driver = RecordingDriver()
    driver.store = store
    assert await _runner(store, driver).tick() == 1
    assert [call[0] for call in driver.calls] == [task.id]
    assert driver.observed_leases == [("runner-1", 1)]
    released = await store.load_record(task.id, project_id=task.project_id)
    assert released.lease_owner is None
    assert released.lease_epoch == 1


@pytest.mark.asyncio
async def test_a_task_held_by_another_runner_is_skipped(store) -> None:
    task = _task()
    await _create(store, task)
    await store.claim(task.id, project_id=task.project_id, owner="runner-2", ttl_seconds=90)
    driver = RecordingDriver()
    assert await _runner(store, driver).tick() == 0
    assert driver.calls == []


@pytest.mark.asyncio
async def test_max_tasks_bounds_one_tick(store) -> None:
    for index in range(4):
        await _create(store, _task(f"task-{index}"))
    driver = RecordingDriver()
    assert await _runner(store, driver, max_tasks=2).tick() == 2
    assert len(driver.calls) == 2


@pytest.mark.asyncio
async def test_a_failing_drive_releases_the_lease_and_the_tick_survives(store, caplog) -> None:
    task = _task()
    await _create(store, task)
    assert await _runner(store, RecordingDriver(fail=True)).tick() == 0
    released = await store.load_record(task.id, project_id=task.project_id)
    assert released.lease_owner is None
    assert "drive exploded" in caplog.text


@pytest.mark.asyncio
async def test_the_heartbeat_extends_the_lease_while_a_drive_runs(store) -> None:
    task = _task()
    await _create(store, task)
    expiries: list[datetime] = []

    class SlowDriver(RecordingDriver):
        async def drive(self, record, *, lease_epoch):
            held = await store.load_record(record.task_id, project_id=record.project_id)
            expiries.append(held.lease_expires_at)
            await asyncio.sleep(0.08)
            renewed = await store.load_record(record.task_id, project_id=record.project_id)
            expiries.append(renewed.lease_expires_at)
            return record

    runner = _runner(store, SlowDriver(), lease_ttl_seconds=90, heartbeat_seconds=0.01)
    assert await runner.tick() == 1
    assert len(expiries) == 2
    assert expiries[1] > expiries[0]


@pytest.mark.asyncio
async def test_every_sweeper_runs_once_per_project_and_a_failure_never_stops_the_tick(
    store, caplog
) -> None:
    calls: list[str] = []

    class Sweeper:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self.name = name
            self.fail = fail

        async def run(self, *, project_id: str, now: datetime) -> int:
            assert now.tzinfo is not None
            calls.append(f"{self.name}:{project_id}")
            if self.fail:
                raise RuntimeError(f"{self.name} exploded")
            return 0

    task = _task()
    await _create(store, task)
    runner = _runner(
        store,
        RecordingDriver(),
        sweepers=(Sweeper("triggers", fail=True), Sweeper("deadlines")),
    )
    assert await runner.tick() == 1
    assert calls == ["triggers:project-a", "deadlines:project-a"]
    assert "triggers exploded" in caplog.text


@pytest.mark.asyncio
async def test_a_due_scheduled_task_is_picked_up(store) -> None:
    task = _scheduled("task-sched")
    await _create(store, task)
    await _mark_scheduled(store, task.id, datetime.now(timezone.utc) - timedelta(minutes=1))
    driver = RecordingDriver()
    assert await _runner(store, driver).tick() == 1
    assert [call[0] for call in driver.calls] == [task.id]


@pytest.mark.asyncio
async def test_start_and_aclose_own_the_loop(store) -> None:
    driver = RecordingDriver()
    runner = _runner(store, driver, interval_seconds=0.01)
    runner.start()
    await asyncio.sleep(0.05)
    await runner.aclose()
