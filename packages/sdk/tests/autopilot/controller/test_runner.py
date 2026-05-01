# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Tests for SchedulerRunner — the async loop driving MissionScheduler.tick()."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.autopilot._types import MissionState
from sagewai.autopilot.controller.runner import SchedulerRunner
from sagewai.autopilot.controller.scheduler import MissionScheduler
from sagewai.autopilot.mission import Mission


def _scheduled_mission(mission_id: str = "m-1") -> Mission:
    """Build a stub Mission with the slots a scheduled mission needs."""
    m = Mission(
        mission_id=mission_id,
        project_id="test-project",
        blueprint_id="bp-1",
        blueprint_version="1",
        slots={"schedule": "* * * * *", "__blueprint_json__": "{}"},
    )
    m.transition_to(MissionState.APPROVED)
    m.transition_to(MissionState.SCHEDULED)
    return m


@pytest.mark.asyncio
async def test_runner_calls_tick_on_interval():
    """SchedulerRunner.start() invokes scheduler.tick() repeatedly."""
    scheduler = MagicMock(spec=MissionScheduler)
    scheduler.tick = MagicMock(return_value=[])
    driver = MagicMock()
    driver.execute = AsyncMock()

    runner = SchedulerRunner(scheduler=scheduler, driver=driver, interval_seconds=0.05)
    await runner.start()
    await asyncio.sleep(0.18)  # ~3 ticks at 0.05s
    await runner.stop()

    assert scheduler.tick.call_count >= 2  # be lenient about exact timing


@pytest.mark.asyncio
async def test_runner_dispatches_fired_missions_to_driver():
    """Every Mission returned by tick() is passed to driver.execute()."""
    mission = _scheduled_mission("m-1")
    scheduler = MagicMock(spec=MissionScheduler)
    scheduler.tick = MagicMock(side_effect=[[mission], [], [], []])
    driver = MagicMock()
    driver.execute = AsyncMock()

    runner = SchedulerRunner(scheduler=scheduler, driver=driver, interval_seconds=0.05)
    await runner.start()
    await asyncio.sleep(0.18)
    await runner.stop()

    driver.execute.assert_awaited_once_with(mission)


@pytest.mark.asyncio
async def test_runner_handles_multiple_missions_per_tick():
    """If tick() returns multiple missions, each one is dispatched."""
    m1 = _scheduled_mission("m-1")
    m2 = _scheduled_mission("m-2")
    scheduler = MagicMock(spec=MissionScheduler)
    scheduler.tick = MagicMock(side_effect=[[m1, m2], [], [], []])
    driver = MagicMock()
    driver.execute = AsyncMock()

    runner = SchedulerRunner(scheduler=scheduler, driver=driver, interval_seconds=0.05)
    await runner.start()
    await asyncio.sleep(0.18)
    await runner.stop()

    assert driver.execute.await_count == 2
    awaited_missions = {call.args[0].mission_id for call in driver.execute.await_args_list}
    assert awaited_missions == {"m-1", "m-2"}


@pytest.mark.asyncio
async def test_runner_continues_after_dispatch_exception():
    """If driver.execute() raises, the runner logs and keeps ticking."""
    m1 = _scheduled_mission("m-1")
    m2 = _scheduled_mission("m-2")
    scheduler = MagicMock(spec=MissionScheduler)
    scheduler.tick = MagicMock(side_effect=[[m1], [m2], [], []])
    driver = MagicMock()
    driver.execute = AsyncMock(side_effect=[RuntimeError("boom"), None, None])

    runner = SchedulerRunner(scheduler=scheduler, driver=driver, interval_seconds=0.05)
    await runner.start()
    await asyncio.sleep(0.18)
    await runner.stop()

    assert driver.execute.await_count == 2


@pytest.mark.asyncio
async def test_runner_stop_is_idempotent():
    """Calling stop() twice does not raise."""
    scheduler = MagicMock(spec=MissionScheduler)
    scheduler.tick = MagicMock(return_value=[])
    driver = MagicMock()
    driver.execute = AsyncMock()

    runner = SchedulerRunner(scheduler=scheduler, driver=driver, interval_seconds=0.05)
    await runner.start()
    await runner.stop()
    await runner.stop()  # should be no-op


@pytest.mark.asyncio
async def test_runner_start_is_idempotent():
    """Calling start() twice does not double-spawn."""
    scheduler = MagicMock(spec=MissionScheduler)
    scheduler.tick = MagicMock(return_value=[])
    driver = MagicMock()
    driver.execute = AsyncMock()

    runner = SchedulerRunner(scheduler=scheduler, driver=driver, interval_seconds=0.05)
    await runner.start()
    await runner.start()  # should be no-op
    await asyncio.sleep(0.12)
    await runner.stop()

    assert scheduler.tick.call_count >= 2
    assert scheduler.tick.call_count <= 4  # not 4-8 from a doubled loop
