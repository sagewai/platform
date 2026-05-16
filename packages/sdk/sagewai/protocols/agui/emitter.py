# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""AGUIEmitter — transport-agnostic event emitter for AG-UI events.

Supports pluggable transports (SSE, WebSocket, or custom) for delivering
AG-UI events to frontend clients.

Usage::

    from sagewai.protocols.agui import AGUIEmitter, SSETransport

    transport = SSETransport(send_func=sse_response.send)
    emitter = AGUIEmitter(transport=transport)
    await emitter.emit(RunStartedEvent(thread_id="t1", run_id="r1"))
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any

from sagewai.protocols.agui.events import BaseEvent

logger = logging.getLogger(__name__)

# Type for async send functions: async (str) -> None
AsyncSendFunc = Callable[[str], Coroutine[Any, Any, None]]


class Transport(ABC):
    """Abstract transport for delivering serialized AG-UI events."""

    @abstractmethod
    async def send(self, event: BaseEvent) -> None:
        """Send a serialized event to the client."""
        ...

    async def close(self) -> None:
        """Close the transport (optional cleanup)."""


class SSETransport(Transport):
    """Server-Sent Events transport.

    Formats events as SSE ``data:`` lines with the event type as the
    ``event:`` field.

    Args:
        send_func: Async callable that sends a raw string to the SSE stream.
    """

    def __init__(self, send_func: AsyncSendFunc) -> None:
        self._send = send_func

    async def send(self, event: BaseEvent) -> None:
        data = event.model_dump_json(by_alias=True)
        sse_message = f"event: {event.type.value}\ndata: {data}\n\n"
        await self._send(sse_message)


class WebSocketTransport(Transport):
    """WebSocket transport.

    Sends events as JSON messages over a WebSocket connection.

    Args:
        send_func: Async callable that sends a raw string over WebSocket.
    """

    def __init__(self, send_func: AsyncSendFunc) -> None:
        self._send = send_func

    async def send(self, event: BaseEvent) -> None:
        data = event.model_dump_json(by_alias=True)
        await self._send(data)


class CallbackTransport(Transport):
    """In-memory transport that collects events for testing.

    Events are appended to the ``events`` list for later inspection.
    """

    def __init__(self) -> None:
        self.events: list[BaseEvent] = []

    async def send(self, event: BaseEvent) -> None:
        self.events.append(event)


class AGUIEmitter:
    """Transport-agnostic AG-UI event emitter.

    Manages one or more transports and broadcasts events to all of them.

    Args:
        transport: A single transport or list of transports to emit to.
            If None, events are silently dropped (useful for testing).
    """

    def __init__(self, transport: Transport | list[Transport] | None = None) -> None:
        if transport is None:
            self._transports: list[Transport] = []
        elif isinstance(transport, list):
            self._transports = transport
        else:
            self._transports = [transport]

    def add_transport(self, transport: Transport) -> None:
        """Add a transport to the emitter."""
        self._transports.append(transport)

    async def emit(self, event: BaseEvent) -> None:
        """Emit an event to all registered transports."""
        for transport in self._transports:
            try:
                await transport.send(event)
            except Exception:
                logger.exception(
                    "Failed to emit event %s via %s", event.type, type(transport).__name__
                )

    async def close(self) -> None:
        """Close all transports."""
        for transport in self._transports:
            try:
                await transport.close()
            except Exception:
                logger.exception("Failed to close transport %s", type(transport).__name__)
