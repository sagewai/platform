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
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from sagewai.admin.serve import _work_project_scope
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.store import WorkStore
from sagewai.work.tasks.events import TaskEventType
from sagewai.work.tasks.feed import FeedEntry
from sagewai.work.tasks.models import BoardColumn, TaskKind, TaskOrigin, TaskRecord, TaskStatus
from sagewai.work.tasks.store import TaskStore
from sagewai.work.tasks.telemetry import derive_task_telemetry

router = APIRouter(prefix="/api/v1/tasks")
_CURSOR_SEPARATOR = "|"
_CURSOR_ORDER_SEPARATOR = ":"
_CURSOR_PREFIX_BY_ORDER = {"created_at": "c", "updated_at": "u"}
_CURSOR_ORDER_BY_PREFIX = {"c": "created_at", "u": "updated_at"}


def _task_project_scope(request: Request) -> str:
    """The Task read and write scope: one explicit project, never the global scope.

    ``_work_project_scope`` maps ``X-Project-ID: global`` to ``None``, which is a real
    organization-global scope for Work. A Task cannot live there — ``Task.project_id`` is
    ``Field(min_length=1)`` and section 19 says there is no global Task scope — so the header
    is refused here instead of resolving to an always-empty scope that 404s.
    """
    project_id = _work_project_scope(request)
    if project_id is None:
        raise HTTPException(
            status_code=400,
            detail="Tasks require an explicit project; there is no global Task scope",
        )
    return project_id


def _encode_cursor(record: TaskRecord, order_by: Literal["created_at", "updated_at"]) -> str:
    moment = record.created_at if order_by == "created_at" else record.updated_at
    return (
        f"{_CURSOR_PREFIX_BY_ORDER[order_by]}{_CURSOR_ORDER_SEPARATOR}"
        f"{moment.isoformat()}{_CURSOR_SEPARATOR}{record.task_id}"
    )


def _decode_cursor(
    cursor: str, order_by: Literal["created_at", "updated_at"]
) -> tuple[datetime, str]:
    raw_ordered, separator, task_id = cursor.partition(_CURSOR_SEPARATOR)
    if separator == "" or not task_id:
        raise HTTPException(status_code=400, detail="cursor is not a list cursor")
    prefix, order_separator, raw_moment = raw_ordered.partition(_CURSOR_ORDER_SEPARATOR)
    if order_separator == "" or prefix not in _CURSOR_ORDER_BY_PREFIX:
        raise HTTPException(status_code=400, detail="cursor is not a list cursor")
    if _CURSOR_ORDER_BY_PREFIX[prefix] != order_by:
        raise HTTPException(status_code=400, detail="cursor does not match order_by")
    try:
        return datetime.fromisoformat(raw_moment), task_id
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor is not a list cursor") from exc


@router.get("")
async def list_tasks(
    request: Request,
    status: Annotated[list[TaskStatus] | None, Query()] = None,
    kind: Annotated[list[TaskKind] | None, Query()] = None,
    origin: Annotated[list[TaskOrigin] | None, Query()] = None,
    column: Annotated[list[BoardColumn] | None, Query()] = None,
    order_by: Literal["created_at", "updated_at"] = "created_at",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    records = await store.list_records(
        project_id=project_id,
        statuses=status,
        kinds=kind,
        origins=origin,
        board_columns=column,
        order_by=order_by,
        after=None if cursor is None else _decode_cursor(cursor, order_by),
        limit=limit,
    )
    return {
        "tasks": [record.model_dump(mode="json") for record in records],
        "next_cursor": _encode_cursor(records[-1], order_by) if len(records) == limit else None,
    }


@router.get("/board")
async def task_board(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict:
    """The five section 5.2 columns, newest-touched first, every one present even when empty."""
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    records = await store.list_records(
        project_id=project_id, order_by="updated_at", descending=True, limit=limit
    )
    columns: dict[str, list[dict]] = {column.value: [] for column in BoardColumn}
    for record in records:
        columns[record.board_column.value].append(record.model_dump(mode="json"))
    return {"columns": columns}


@router.get("/{task_id}/events")
async def task_events(task_id: str, request: Request) -> EventSourceResponse:
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    if await store.load_record(task_id, project_id=project_id) is None:
        raise HTTPException(status_code=404, detail="Not found")
    last_event_id = request.headers.get("last-event-id")
    if last_event_id is None:
        after = 0
    else:
        try:
            after = int(last_event_id)
        except ValueError as exc:
            raise HTTPException(400, "Last-Event-ID must be a non-negative integer") from exc
        if after < 0 or after > 2**63 - 1:
            raise HTTPException(400, "Last-Event-ID must be a non-negative integer")
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


@router.get("/{task_id}/telemetry")
async def task_telemetry(task_id: str, request: Request) -> dict:
    project_id = _task_project_scope(request)
    task_store: TaskStore = request.app.state.task_store
    loaded = await task_store.load(task_id, project_id=project_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Not found")
    task, record = loaded
    task_events = await task_store.read_events(task_id, project_id=project_id)
    work_store: WorkStore = request.app.state.work_store
    work_records = await work_store.list_work(project_id=project_id, active_only=False)
    work_events: dict[str, list[WorkEvent]] = {}
    project_selections: list[WorkEvent] = []
    for work_record in work_records:
        events = await work_store.read_events(work_record.work_id, project_id=project_id)
        project_selections.extend(
            event for event in events if event.event_type is WorkEventType.RUNTIME_SELECTED
        )
        if work_record.profile_context.get("task_id") == task_id:
            work_events[work_record.work_id] = events
    cycles = {
        int(event.payload_json["cycle"])
        for event in task_events
        if event.event_type is TaskEventType.CYCLE_STARTED
    }
    spend = {
        cycle: await task_store.spend_totals(
            task_id=task_id,
            project_id=project_id,
            cycle=cycle,
        )
        for cycle in cycles
    }
    return derive_task_telemetry(
        record=record,
        task_events=task_events,
        work_events=work_events,
        spend=spend,
        budget=task.budget,
        project_selections=project_selections,
        now=datetime.now(timezone.utc),
    ).model_dump(mode="json")


async def _task_event_stream(
    *,
    store: TaskStore,
    project_id: str,
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
