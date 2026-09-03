# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task API routes for feed replay and live events."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from sagewai.admin.serve import _work_project_scope
from sagewai.work.tasks.feed import FeedEntry
from sagewai.work.tasks.store import TaskStore

router = APIRouter(prefix="/api/v1/tasks")


@router.get("/{task_id}/events")
async def task_events(task_id: str, request: Request) -> EventSourceResponse:
    project_id = _work_project_scope(request)
    store: TaskStore = request.app.state.task_store
    if await store.load_record(task_id, project_id=project_id) is None:
        raise HTTPException(status_code=404, detail="Not found")
    after = int(request.headers.get("last-event-id", "0") or 0)
    heartbeat_seconds = float(os.environ.get("TASK_SSE_HEARTBEAT", "15"))
    queue = store.feed_bus.subscribe(project_id, task_id)
    return EventSourceResponse(
        _task_event_stream(
            store=store,
            project_id=project_id,
            task_id=task_id,
            queue=queue,
            after=after,
            heartbeat_seconds=heartbeat_seconds,
        )
    )


async def _task_event_stream(
    *,
    store: TaskStore,
    project_id: str | None,
    task_id: str,
    queue: asyncio.Queue[FeedEntry],
    after: int,
    heartbeat_seconds: float,
) -> AsyncIterator[dict[str, str]]:
    seen = after
    try:
        while True:
            page = await store.read_feed(task_id, project_id=project_id, after=seen, limit=500)
            if not page:
                break
            for entry in page:
                seen = entry.feed_sequence
                yield _sse(entry)
        while True:
            try:
                entry = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield {"event": "heartbeat", "data": "{}"}
                continue
            if entry.feed_sequence <= seen:
                continue
            seen = entry.feed_sequence
            yield _sse(entry)
    finally:
        store.feed_bus.unsubscribe(project_id, task_id, queue)


def _sse(entry: FeedEntry) -> dict[str, str]:
    return {
        "id": str(entry.feed_sequence),
        "event": entry.event_type,
        "data": entry.model_dump_json(),
    }


__all__ = ["router"]
