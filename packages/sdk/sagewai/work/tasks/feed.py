# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Per-Task feed entries and the in-process fan-out bus used by SSE."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FeedEntry(BaseModel):
    """One totally ordered feed row; replayed from the store, streamed live."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    task_id: str
    feed_sequence: int = Field(ge=1)
    source: Literal["task_event", "work_event", "activity"]
    source_id: str
    event_type: str
    payload_json: dict[str, Any]
    created_at: datetime


class FeedBus:
    """Best-effort in-process fan-out; durable ordering comes from the store."""

    def __init__(self, *, max_queue: int = 1000) -> None:
        self._max_queue = max_queue
        self._queues: dict[tuple[str, str], list[asyncio.Queue[FeedEntry]]] = defaultdict(list)
        self._dropped: dict[tuple[str, str], int] = defaultdict(int)

    def subscribe(self, project_id: str, task_id: str) -> asyncio.Queue[FeedEntry]:
        queue: asyncio.Queue[FeedEntry] = asyncio.Queue(maxsize=self._max_queue)
        self._queues[(project_id, task_id)].append(queue)
        return queue

    def unsubscribe(self, project_id: str, task_id: str, queue: asyncio.Queue[FeedEntry]) -> None:
        key = (project_id, task_id)
        subscribers = self._queues.get(key, [])
        if queue in subscribers:
            subscribers.remove(queue)
        if not subscribers:
            self._queues.pop(key, None)

    async def publish(self, entry: FeedEntry) -> None:
        key = (entry.project_id, entry.task_id)
        for queue in list(self._queues.get(key, [])):
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:
                self._dropped[key] += 1

    def dropped(self, project_id: str, task_id: str) -> int:
        return self._dropped.get((project_id, task_id), 0)


__all__ = ["FeedBus", "FeedEntry"]
