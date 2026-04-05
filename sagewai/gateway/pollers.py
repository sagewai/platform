# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Poller framework — cyclic jobs that actively check for new events."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Callable, Awaitable

from sagewai.gateway.triggers import IncomingEvent

logger = logging.getLogger(__name__)

MAX_BACKOFF = timedelta(minutes=5)


class Poller(ABC):
    """Base class for pollers that fetch events on a schedule."""

    connector: str
    interval: timedelta
    channels: list[str]
    min_interval: timedelta = timedelta(seconds=5)

    @abstractmethod
    async def poll(self) -> list[IncomingEvent]:
        """Fetch new events since last check."""
        ...


class PollerManager:
    """Manages all active pollers as asyncio tasks with backoff on errors."""

    def __init__(self) -> None:
        self._pollers: list[tuple[Poller, Callable]] = []
        self._tasks: list[asyncio.Task] = []

    def register(
        self,
        poller: Poller,
        handler: Callable[[IncomingEvent], Awaitable[None]],
    ) -> None:
        self._pollers.append((poller, handler))

    async def start_all(self) -> None:
        for poller, handler in self._pollers:
            task = asyncio.create_task(self._run_poller(poller, handler))
            self._tasks.append(task)

    async def stop_all(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run_poller(
        self,
        poller: Poller,
        handler: Callable[[IncomingEvent], Awaitable[None]],
    ) -> None:
        backoff = poller.interval.total_seconds()
        while True:
            try:
                events = await poller.poll()
                for event in events:
                    try:
                        await handler(event)
                    except Exception:
                        logger.exception("Handler error for %s", poller.connector)
                backoff = poller.interval.total_seconds()  # reset on success
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Poll error for %s", poller.connector)
                backoff = min(backoff * 2, MAX_BACKOFF.total_seconds())
            await asyncio.sleep(backoff)
