# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Listener framework — persistent connections for real-time event ingestion."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable, Awaitable

from sagewai.gateway.triggers import IncomingEvent

logger = logging.getLogger(__name__)


class Listener(ABC):
    """Base class for persistent connection listeners."""

    connector: str
    channels: list[str]
    on_event: Callable[[IncomingEvent], Awaitable[None]] | None = None

    @abstractmethod
    async def start(self) -> None:
        """Begin listening."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown."""
        ...


class ListenerManager:
    """Manages all active listeners."""

    def __init__(self) -> None:
        self._listeners: list[Listener] = []

    def register(self, listener: Listener) -> None:
        self._listeners.append(listener)

    async def start_all(self) -> None:
        for listener in self._listeners:
            try:
                await listener.start()
            except Exception:
                logger.exception("Failed to start listener %s", listener.connector)

    async def stop_all(self) -> None:
        for listener in self._listeners:
            try:
                await listener.stop()
            except Exception:
                logger.exception("Failed to stop listener %s", listener.connector)
