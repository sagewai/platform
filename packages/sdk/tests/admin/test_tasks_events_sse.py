# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task feed entries are replayed and streamed over SSE."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sagewai.admin.tasks_routes import _task_event_stream
from sagewai.work.tasks import FeedEntry, TaskEventType, TaskStore
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _event, _record, _task

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


@dataclass
class AdminClient:
    app: Any
    http: httpx.AsyncClient
    headers: dict[str, str]


@pytest.fixture
async def client(dialect_engine) -> AdminClient:  # noqa: F811
    from sagewai.admin.tasks_routes import router

    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    app = FastAPI()
    app.state.task_store = task_store
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield AdminClient(app=app, http=http, headers={"X-Project-ID": "p"})


@pytest.fixture
async def seeded_task(client: AdminClient):
    task = _task("t1", project_id="p")
    record = _record(task)
    events = (_event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),)
    await client.app.state.task_store.create(task, events=events, record=record)
    return task


def _entry(source_id: str, event_type: str = "activity.message") -> FeedEntry:
    return FeedEntry(
        project_id="p",
        task_id="t1",
        feed_sequence=1,
        source="activity",
        source_id=source_id,
        event_type=event_type,
        payload_json={"source_id": source_id},
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_events_replays_from_last_event_id_then_streams_live(
    client: AdminClient,
    seeded_task,
) -> None:
    store: TaskStore = client.app.state.task_store
    replayed = await store.append_feed((_entry("activity-2"), _entry("activity-3")))
    queue = store.feed_bus.subscribe("p", "t1")
    stream = _task_event_stream(
        store=store,
        project_id="p",
        task_id="t1",
        queue=queue,
        after=1,
        heartbeat_seconds=0.05,
    )
    try:
        chunks = [
            await asyncio.wait_for(anext(stream), timeout=0.5),
            await asyncio.wait_for(anext(stream), timeout=0.5),
        ]
        await store.append_feed((_entry("activity-4", "activity.usage"),))
        await store.feed_bus.publish(replayed[0])
        chunks.append(await asyncio.wait_for(anext(stream), timeout=0.5))
        heartbeat = await asyncio.wait_for(anext(stream), timeout=0.5)
    finally:
        await stream.aclose()

    assert [chunk["id"] for chunk in chunks] == ["2", "3", "4"]
    assert [chunk["event"] for chunk in chunks] == [
        "activity.message",
        "activity.message",
        "activity.usage",
    ]
    payloads = [json.loads(chunk["data"]) for chunk in chunks]
    assert [payload["source_id"] for payload in payloads] == ["activity-2", "activity-3", "activity-4"]
    assert heartbeat == {"event": "heartbeat", "data": "{}"}


@pytest.mark.asyncio
async def test_events_404_for_unknown_task_and_requires_project_scope(
    client: AdminClient,
    seeded_task,
) -> None:
    missing = await client.http.get("/api/v1/tasks/missing/events", headers=client.headers)
    unscoped = await client.http.get("/api/v1/tasks/t1/events")

    assert missing.status_code == 404
    assert unscoped.status_code == 400
    assert unscoped.json() == {"detail": "Work project scope is required"}


@pytest.mark.parametrize("last_event_id", ["abc", "-1", "99999999999999999999"])
@pytest.mark.asyncio
async def test_events_rejects_invalid_last_event_id(
    client: AdminClient,
    seeded_task,
    last_event_id: str,
) -> None:
    async with client.http.stream(
        "GET",
        "/api/v1/tasks/t1/events",
        headers={**client.headers, "Last-Event-ID": last_event_id},
    ) as response:
        assert response.status_code == 400
        assert json.loads((await response.aread()).decode()) == {
            "detail": "Last-Event-ID must be a non-negative integer"
        }


def test_heartbeat_is_emitted_when_idle() -> None:
    class Bus:
        def __init__(self) -> None:
            self.unsubscribed = False

        def unsubscribe(self, project_id, task_id, queue) -> None:
            self.unsubscribed = (project_id, task_id, queue)

    class Store:
        def __init__(self) -> None:
            self.feed_bus = Bus()

        async def read_feed(self, task_id, *, project_id, after=0, limit=500):
            return []

    async def run() -> None:
        store = Store()
        queue: asyncio.Queue[FeedEntry] = asyncio.Queue()
        stream = _task_event_stream(
            store=store,
            project_id="p",
            task_id="t1",
            queue=queue,
            after=0,
            heartbeat_seconds=0.05,
        )
        try:
            event = await asyncio.wait_for(anext(stream), timeout=0.5)
        finally:
            await stream.aclose()
        assert event == {"event": "heartbeat", "data": "{}"}
        assert store.feed_bus.unsubscribed == ("p", "t1", queue)

    asyncio.run(run())
