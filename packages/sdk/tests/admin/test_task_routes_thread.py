# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""One Task read and its pure thread projection route."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sagewai.admin.tasks_routes import router
from sagewai.work.tasks import TaskEventType, TaskStore
from sagewai.work.tasks.events import fold_record
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _event, _record, _task

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
STEP = {
    "id": "s1",
    "title": "Implement queue",
    "goal": "Retry failed jobs",
    "allowed_scope": ["packages/sdk/sagewai/work/tasks/views.py"],
    "acceptance_criteria": [
        {
            "statement": "The task thread renders",
            "verification_kind": "deterministic",
        }
    ],
    "constraints": [],
    "non_goals": [],
    "risk": "low",
    "design_required": False,
    "depends_on": [],
    "domain": "backend",
    "size": "s",
}
MATRIX = [
    {
        "id": "m1",
        "statement": "thread route passes",
        "verification_kind": "deterministic",
        "command": "just smoke",
    }
]


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
) -> None:
    task = _task(task_id, project_id=project_id)
    record = _record(task)
    events = (_event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),)
    await client.app.state.task_store.create(task, events=events, record=record)


async def _seed_thread(client: AdminClient, task_id: str) -> None:
    task = _task(task_id, project_id="p")
    record = _record(task)
    events = (
        _event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),
        _event(
            task,
            2,
            TaskEventType.BRIEF_RECORDED,
            {"brief_ref": "artifact://sha256:" + "a" * 64, "summary": "Build the thing"},
        ),
        _event(
            task,
            3,
            TaskEventType.TASK_MESSAGE,
            {"author": "coordinator", "text": "planning", "refs": []},
        ),
        _event(task, 4, TaskEventType.TASK_STATUS_CHANGED, {"status": "EXECUTING"}),
    )
    await client.app.state.task_store.create(
        task, events=events, record=fold_record(record, events)
    )


async def _seed_accepted_plan(client: AdminClient, task_id: str) -> None:
    task = _task(task_id, project_id="p")
    record = _record(task)
    events = (
        _event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),
        _event(
            task,
            2,
            TaskEventType.PLAN_PROPOSED,
            {"version": 1, "steps": [STEP], "acceptance_matrix": MATRIX},
        ),
        _event(task, 3, TaskEventType.PLAN_ACCEPTED, {"version": 1}),
    )
    await client.app.state.task_store.create(
        task, events=events, record=fold_record(record, events)
    )


@pytest.mark.asyncio
async def test_get_task_returns_the_definition_and_the_projection(client: AdminClient) -> None:
    await _seed(client, "t-1")

    response = await client.http.get("/api/v1/tasks/t-1", headers=client.headers)

    assert response.status_code == 200
    body = response.json()
    assert body["task"]["id"] == "t-1"
    assert body["record"]["task_id"] == "t-1"
    assert body["record"]["board_column"] == "inbox"
    assert body["plan"] is None


@pytest.mark.asyncio
async def test_get_task_does_not_read_the_stream_without_an_accepted_plan(
    client: AdminClient, monkeypatch
) -> None:
    await _seed(client, "t-1")

    async def _read_events(*_args, **_kwargs):
        raise AssertionError("read_events should not be called")

    monkeypatch.setattr(client.app.state.task_store, "read_events", _read_events)

    response = await client.http.get("/api/v1/tasks/t-1", headers=client.headers)

    assert response.status_code == 200
    assert response.json()["plan"] is None


@pytest.mark.asyncio
async def test_get_task_returns_the_accepted_plan_projection(
    client: AdminClient,
) -> None:
    await _seed_accepted_plan(client, "t-plan")

    response = await client.http.get("/api/v1/tasks/t-plan", headers=client.headers)

    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["version"] == 1
    assert body["plan"]["steps"][0]["id"] == "s1"
    assert body["plan"]["acceptance_matrix"][0]["id"] == "m1"


@pytest.mark.asyncio
async def test_get_task_hides_another_projects_task(client: AdminClient) -> None:
    await _seed(client, "t-other", project_id="q")

    response = await client.http.get("/api/v1/tasks/t-other", headers=client.headers)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_thread_renders_the_stream(client: AdminClient) -> None:
    await _seed_thread(client, "t-thread")

    response = await client.http.get("/api/v1/tasks/t-thread/thread", headers=client.headers)

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == "t-thread"
    assert body["brief_ref"] == "artifact://sha256:" + "a" * 64
    assert [(entry["kind"], entry["text"]) for entry in body["entries"]] == [
        ("brief", "Build the thing"),
        ("message", "planning"),
        ("status", "EXECUTING"),
    ]
    assert body["open_question_ids"] == []
    assert body["pending_gate"] is None


@pytest.mark.asyncio
async def test_thread_404s_for_an_unknown_task(client: AdminClient) -> None:
    response = await client.http.get("/api/v1/tasks/missing/thread", headers=client.headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_thread_404s_for_another_projects_task(client: AdminClient) -> None:
    await _seed_thread(client, "t-other")

    response = await client.http.get("/api/v1/tasks/t-other/thread", headers={"X-Project-ID": "q"})

    assert response.status_code == 404
