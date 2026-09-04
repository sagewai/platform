# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The Task list and board routes filter, order, and page one project's Tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sagewai.admin.tasks_routes import router
from sagewai.work.tasks import TaskEventType, TaskStore
from sagewai.work.tasks.models import BoardColumn, Schedule, TaskKind, TaskOrigin, TaskStatus
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _event, _record, _task

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


@dataclass
class AdminClient:
    app: Any
    http: httpx.AsyncClient
    headers: dict[str, str]


@pytest.fixture
async def client(dialect_engine) -> AdminClient:  # noqa: F811
    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    app = FastAPI()
    app.state.task_store = task_store
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield AdminClient(app=app, http=http, headers={"X-Project-ID": "p"})


async def _seed(
    client: AdminClient,
    task_id: str,
    *,
    project_id: str = "p",
    minutes: int = 0,
    kind: TaskKind = TaskKind.BATCH,
    origin: TaskOrigin = TaskOrigin.HUMAN,
    status: TaskStatus = TaskStatus.PLANNING,
    board_column: BoardColumn = BoardColumn.INBOX,
) -> None:
    created = NOW + timedelta(minutes=minutes)
    task = _task(task_id, project_id=project_id).model_copy(
        update={
            "kind": kind,
            "origin": origin,
            "created_at": created,
            **(
                {"schedule": Schedule(cron="0 8 * * *", timezone="Europe/Berlin")}
                if kind is TaskKind.SCHEDULED
                else {}
            ),
        }
    )
    record = _record(task).model_copy(
        update={
            "kind": kind,
            "origin": origin,
            "status": status,
            "board_column": board_column,
            "created_at": created,
            "updated_at": created,
        }
    )
    events = (_event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),)
    await client.app.state.task_store.create(task, events=events, record=record)


async def _touch(client: AdminClient, task_id: str, *, updated_at: datetime) -> None:
    store = client.app.state.task_store
    record = await store.load_record(task_id, project_id="p")
    event = _event(
        _task(task_id, project_id="p"),
        record.last_event_sequence + 1,
        TaskEventType.TASK_MESSAGE,
        {"author": "human", "text": "hi", "refs": []},
    )
    await store.append(
        task_id=task_id,
        project_id="p",
        events=(event,),
        expected_sequence=event.sequence,
        record=record.model_copy(
            update={"last_event_sequence": event.sequence, "updated_at": updated_at}
        ),
    )


@pytest.mark.asyncio
async def test_list_returns_only_the_scoped_projects_tasks_oldest_first(
    client: AdminClient,
) -> None:
    await _seed(client, "t-1", minutes=0)
    await _seed(client, "t-2", minutes=1)
    await _seed(client, "t-other", project_id="q", minutes=2)

    response = await client.http.get("/api/v1/tasks", headers=client.headers)

    assert response.status_code == 200
    body = response.json()
    assert [task["task_id"] for task in body["tasks"]] == ["t-1", "t-2"]
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_filters_and_pages(client: AdminClient) -> None:
    await _seed(client, "t-1", minutes=0)
    await _seed(client, "t-2", minutes=1)
    await _seed(client, "t-sched", minutes=2, kind=TaskKind.SCHEDULED)
    await _seed(client, "t-blocked", minutes=3, status=TaskStatus.BLOCKED)
    await _seed(
        client,
        "t-trigger",
        minutes=4,
        origin=TaskOrigin.TRIGGER,
        status=TaskStatus.COMPLETE,
    )
    await _seed(
        client,
        "t-planned",
        minutes=5,
        status=TaskStatus.CANCELLED,
        board_column=BoardColumn.PLANNED,
    )
    await _touch(client, "t-1", updated_at=NOW + timedelta(minutes=9))
    await _touch(client, "t-2", updated_at=NOW + timedelta(minutes=7))

    filtered = await client.http.get(
        "/api/v1/tasks", headers=client.headers, params={"kind": "scheduled"}
    )
    by_status = await client.http.get(
        "/api/v1/tasks", headers=client.headers, params={"status": "BLOCKED"}
    )
    by_origin = await client.http.get(
        "/api/v1/tasks", headers=client.headers, params={"origin": "trigger"}
    )
    by_column = await client.http.get(
        "/api/v1/tasks", headers=client.headers, params={"column": "planned"}
    )
    first = await client.http.get(
        "/api/v1/tasks",
        headers=client.headers,
        params={"status": "PLANNING", "order_by": "updated_at", "limit": 2},
    )
    second = await client.http.get(
        "/api/v1/tasks",
        headers=client.headers,
        params={
            "status": "PLANNING",
            "order_by": "updated_at",
            "limit": 2,
            "cursor": first.json()["next_cursor"],
        },
    )

    assert [task["task_id"] for task in filtered.json()["tasks"]] == ["t-sched"]
    assert [task["task_id"] for task in by_status.json()["tasks"]] == ["t-blocked"]
    assert [task["task_id"] for task in by_origin.json()["tasks"]] == ["t-trigger"]
    assert [task["task_id"] for task in by_column.json()["tasks"]] == ["t-planned"]
    assert [task["task_id"] for task in first.json()["tasks"]] == ["t-sched", "t-2"]
    assert first.json()["next_cursor"] is not None
    assert [task["task_id"] for task in second.json()["tasks"]] == ["t-1"]


@pytest.mark.asyncio
async def test_list_pages_descending_with_the_cursor(client: AdminClient) -> None:
    await _seed(client, "t-1", minutes=0)
    await _seed(client, "t-2", minutes=1)
    await _seed(client, "t-3", minutes=2)
    await _seed(client, "t-4", minutes=3)
    await _seed(client, "t-5", minutes=4)

    first = await client.http.get(
        "/api/v1/tasks",
        headers=client.headers,
        params={"order_by": "updated_at", "descending": "true", "limit": 3},
    )
    second = await client.http.get(
        "/api/v1/tasks",
        headers=client.headers,
        params={
            "order_by": "updated_at",
            "descending": "true",
            "limit": 3,
            "cursor": first.json()["next_cursor"],
        },
    )

    assert [task["task_id"] for task in first.json()["tasks"]] == ["t-5", "t-4", "t-3"]
    assert first.json()["next_cursor"] is not None
    assert [task["task_id"] for task in second.json()["tasks"]] == ["t-2", "t-1"]
    assert {task["task_id"] for task in first.json()["tasks"]}.isdisjoint(
        {task["task_id"] for task in second.json()["tasks"]}
    )


@pytest.mark.asyncio
async def test_list_rejects_an_unknown_filter_value(client: AdminClient) -> None:
    response = await client.http.get(
        "/api/v1/tasks", headers=client.headers, params={"status": "SLEEPING"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_rejects_a_malformed_cursor(client: AdminClient) -> None:
    response = await client.http.get(
        "/api/v1/tasks", headers=client.headers, params={"cursor": "not-a-cursor"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "cursor is not a list cursor"


@pytest.mark.asyncio
async def test_list_rejects_a_separatorless_cursor(client: AdminClient) -> None:
    response = await client.http.get(
        "/api/v1/tasks", headers=client.headers, params={"cursor": NOW.isoformat()}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "cursor is not a list cursor"


@pytest.mark.asyncio
async def test_list_rejects_a_cursor_minted_for_a_different_order(
    client: AdminClient,
) -> None:
    await _seed(client, "t-1", minutes=0)
    await _seed(client, "t-2", minutes=1)

    first = await client.http.get(
        "/api/v1/tasks",
        headers=client.headers,
        params={"order_by": "created_at", "limit": 1},
    )
    response = await client.http.get(
        "/api/v1/tasks",
        headers=client.headers,
        params={
            "order_by": "updated_at",
            "limit": 1,
            "cursor": first.json()["next_cursor"],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "cursor does not match order_by/descending"


@pytest.mark.asyncio
async def test_list_rejects_a_cursor_minted_for_a_different_direction(
    client: AdminClient,
) -> None:
    await _seed(client, "t-1", minutes=0)
    await _seed(client, "t-2", minutes=1)

    first = await client.http.get(
        "/api/v1/tasks",
        headers=client.headers,
        params={"order_by": "updated_at", "limit": 1},
    )
    response = await client.http.get(
        "/api/v1/tasks",
        headers=client.headers,
        params={
            "order_by": "updated_at",
            "descending": "true",
            "limit": 1,
            "cursor": first.json()["next_cursor"],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "cursor does not match order_by/descending"


@pytest.mark.asyncio
async def test_board_groups_every_column_even_when_empty(client: AdminClient) -> None:
    await _seed(client, "t-inbox-old", minutes=0)
    await _seed(client, "t-inbox-new", minutes=1)
    await _seed(
        client,
        "t-needs",
        minutes=2,
        status=TaskStatus.BLOCKED,
        board_column=BoardColumn.NEEDS_YOU,
    )
    await _touch(client, "t-inbox-new", updated_at=NOW + timedelta(minutes=9))

    response = await client.http.get("/api/v1/tasks/board", headers=client.headers)

    assert response.status_code == 200
    columns = response.json()["columns"]
    assert list(columns) == ["inbox", "needs_you", "planned", "in_progress", "done"]
    assert [task["task_id"] for task in columns["inbox"]] == [
        "t-inbox-new",
        "t-inbox-old",
    ]
    assert [task["task_id"] for task in columns["needs_you"]] == ["t-needs"]
    assert columns["done"] == []


@pytest.mark.asyncio
async def test_list_and_board_refuse_the_global_scope(client: AdminClient) -> None:
    listed = await client.http.get("/api/v1/tasks", headers={"X-Project-ID": "global"})
    board = await client.http.get("/api/v1/tasks/board", headers={"X-Project-ID": "global"})

    assert listed.status_code == 400
    assert board.status_code == 400
