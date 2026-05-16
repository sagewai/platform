# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SchedulerRunner — async background loop that drives MissionScheduler.tick().

The :class:`MissionScheduler` is pull-based: it has a ``tick()`` method
that returns missions ready to fire, but does not call itself on any
schedule. :class:`SchedulerRunner` provides the always-on event loop:
on a fixed interval, it asks the scheduler for fired missions and
dispatches each to a :class:`MissionDriver`.

Lifecycle (designed for FastAPI lifespan integration)::

    runner = SchedulerRunner(scheduler=scheduler, driver=driver)
    await runner.start()       # spawns asyncio task
    ...                         # app runs
    await runner.stop()        # cancels task and awaits cleanup
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sagewai.autopilot.controller.driver import MissionDriver
    from sagewai.autopilot.controller.scheduler import MissionScheduler

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS = 60.0


class SchedulerRunner:
    """Async background loop driving :class:`MissionScheduler.tick`.

    Args:
        scheduler: The :class:`MissionScheduler` to tick.
        driver: The :class:`MissionDriver` used to execute fired missions.
        interval_seconds: How often to call ``tick()``. Defaults to 60s.
            Tests should pass a much smaller value.
    """

    def __init__(
        self,
        *,
        scheduler: "MissionScheduler",
        driver: "MissionDriver",
        interval_seconds: float = _DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._scheduler = scheduler
        self._driver = driver
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        """Spawn the background tick loop. Idempotent — calling twice is a no-op."""
        if self._task is not None and not self._task.done():
            logger.debug("SchedulerRunner.start() ignored — already running")
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="sagewai-scheduler-runner")
        logger.info(
            "SchedulerRunner started (interval=%.2fs)", self._interval,
        )

    async def stop(self) -> None:
        """Stop the loop gracefully. Idempotent."""
        if self._task is None or self._task.done():
            return
        if self._stop_event is not None:
            self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=self._interval + 1.0)
        except asyncio.TimeoutError:
            logger.warning("SchedulerRunner.stop() timed out — cancelling task")
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        finally:
            self._task = None
            self._stop_event = None
            logger.info("SchedulerRunner stopped")

    # ── private ─────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """The body of the background task — sleep, tick, dispatch, repeat."""
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self._tick_once()
            except Exception as exc:  # noqa: BLE001
                logger.exception("SchedulerRunner tick raised: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def _tick_once(self) -> None:
        """Run one tick: fetch fired missions and dispatch each to the driver."""
        fired = self._scheduler.tick()
        for mission in fired:
            try:
                await self._driver.execute(mission)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Mission %s dispatch failed in scheduler runner: %s",
                    mission.mission_id,
                    exc,
                )
