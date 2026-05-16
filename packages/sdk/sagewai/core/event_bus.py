# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""In-memory event bus for workflow execution events.

Provides pub/sub for live SSE streaming of workflow progress. Events are
also persisted to the ``workflow_events`` table separately — this bus is
purely for real-time delivery to connected clients.

Usage::

    bus = WorkflowEventBus()

    # Publisher (worker)
    bus.publish("run-abc", {"type": "step_completed", "agent": "writer"})

    # Subscriber (SSE endpoint)
    async for event in bus.subscribe("run-abc"):
        yield event  # → SSE
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class WorkflowEventBus:
    """In-memory pub/sub for workflow run events.

    Each subscriber gets its own asyncio.Queue. When a subscriber disconnects,
    its queue is removed to prevent memory leaks.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any] | None]]] = (
            defaultdict(list)
        )

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        """Publish an event to all subscribers for a given run_id."""
        queues = self._subscribers.get(run_id)
        if not queues:
            return
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "Event queue full for run %s — dropping event %s",
                    run_id,
                    event.get("type", "unknown"),
                )

    async def subscribe(self, run_id: str):
        """Subscribe to events for a run. Yields events until a terminal event.

        Terminal events: workflow_finished, workflow_failed, workflow_cancelled.
        Yields None sentinel to signal end-of-stream.
        """
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
        self._subscribers[run_id].append(queue)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    # Sentinel — stream closed
                    break
                yield event
                # Auto-close on terminal events
                event_type = event.get("type", "")
                if event_type in ("workflow_finished", "workflow_failed", "workflow_cancelled"):
                    break
        finally:
            self._unsubscribe(run_id, queue)

    def close_run(self, run_id: str) -> None:
        """Send None sentinel to all subscribers for a run, closing their streams."""
        queues = self._subscribers.pop(run_id, [])
        for q in queues:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def _unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        queues = self._subscribers.get(run_id)
        if queues:
            try:
                queues.remove(queue)
            except ValueError:
                pass
            if not queues:
                del self._subscribers[run_id]
