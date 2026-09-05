# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Activity reads for a Task's Works and for one Work."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sagewai.admin.tasks_routes import router, work_router
from sagewai.work import OperatorActivity, WorkActivityStore, WorkRecord, WorkStore
from sagewai.work.tasks import TaskStore
from sagewai.work.tasks.events import TaskEventType, fold_record
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _event, _record, _task

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


@dataclass
class AdminClient:
    app: Any
    http: httpx.AsyncClient
    headers: dict[str, str]


def _activity(
    work_id: str,
    run_id: str,
    sequence: int,
    *,
    project_id: str | None = "p",
    source: str = "codex",
) -> OperatorActivity:
    return OperatorActivity(
        project_id=project_id,
        work_id=work_id,
        run_id=run_id,
        sequence=sequence,
        at=NOW,
        source=source,
        kind="message",
        summary=f"{work_id}/{sequence}",
    )


def _work_record(work_id: str, *, project_id: str | None) -> WorkRecord:
    return WorkRecord(
        work_id=work_id,
        project_id=project_id,
        source_ref=f"issue:{work_id}",
        profile="software",
        status="COMPLETE",
        active_run_id=None,
        pending_gate=None,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
async def client(dialect_engine) -> AdminClient:  # noqa: F811
    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    activity_store = WorkActivityStore(engine=dialect_engine)
    await activity_store.init()

    task = _task("t-1", project_id="p")
    events = (
        _event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),
        _event(task, 2, TaskEventType.STEP_WORK_STARTED, {"step_id": "s1", "work_id": "w1"}),
        _event(task, 3, TaskEventType.STEP_WORK_STARTED, {"step_id": "s2", "work_id": "w2"}),
    )
    await task_store.create(task, events=events, record=fold_record(_record(task), events))
    empty = _task("t-empty", project_id="p")
    empty_events = (_event(empty, 1, TaskEventType.TASK_CREATED, {"title": empty.title}),)
    await task_store.create(
        empty, events=empty_events, record=fold_record(_record(empty), empty_events)
    )
    foreign = _task("t-foreign", project_id="q")
    foreign_events = (_event(foreign, 1, TaskEventType.TASK_CREATED, {"title": foreign.title}),)
    await task_store.create(
        foreign, events=foreign_events, record=fold_record(_record(foreign), foreign_events)
    )
    await work_store.save_work(_work_record("w1", project_id="p"))
    await activity_store.append(
        [
            _activity("w1", "w1:implement:1", 1),
            _activity("w1", "w1:implement:1", 2, source="verifier"),
            _activity("w1", "w1:review:1", 1),
            _activity("w1", "w1:review:1", 2),
            _activity("w2", "w2:review:1", 1, source="claude"),
        ]
    )

    app = FastAPI()
    app.state.task_store = task_store
    app.state.work_store = work_store
    app.state.activity_store = activity_store
    app.include_router(router)
    app.include_router(work_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield AdminClient(app=app, http=http, headers={"X-Project-ID": "p"})


@pytest.mark.asyncio
async def test_task_activity_spans_every_work_the_task_started(client: AdminClient) -> None:
    response = await client.http.get("/api/v1/tasks/t-1/activity", headers=client.headers)

    assert response.status_code == 200
    body = response.json()
    assert [item["work_id"] for item in body["items"]] == ["w1", "w1", "w1", "w1", "w2"]
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_task_activity_covers_the_planning_work(client: AdminClient) -> None:
    await client.app.state.work_store.save_work(
        _work_record("t-1:plan:0:1", project_id="p")
    )
    await client.app.state.activity_store.append(
        [_activity("t-1:plan:0:1", "t-1:plan:0:1:plan:1", 1, source="claude")]
    )

    response = await client.http.get(
        "/api/v1/tasks/t-1/activity?work_id=t-1:plan:0:1", headers=client.headers
    )

    assert response.status_code == 200
    assert [item["summary"] for item in response.json()["items"]] == ["t-1:plan:0:1/1"]


@pytest.mark.asyncio
async def test_task_activity_refuses_the_global_scope(client: AdminClient) -> None:
    response = await client.http.get(
        "/api/v1/tasks/t-1/activity", headers={"X-Project-ID": "global"}
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_task_activity_filters_by_work_run_and_source(client: AdminClient) -> None:
    by_work = await client.http.get(
        "/api/v1/tasks/t-1/activity", headers=client.headers, params={"work_id": "w2"}
    )
    by_run = await client.http.get(
        "/api/v1/tasks/t-1/activity", headers=client.headers, params={"run_id": "w1:review:1"}
    )
    by_source = await client.http.get(
        "/api/v1/tasks/t-1/activity", headers=client.headers, params={"source": "verifier"}
    )

    assert [item["work_id"] for item in by_work.json()["items"]] == ["w2"]
    assert [item["run_id"] for item in by_run.json()["items"]] == ["w1:review:1", "w1:review:1"]
    assert [item["source"] for item in by_source.json()["items"]] == ["verifier"]


@pytest.mark.asyncio
async def test_task_activity_refuses_a_work_the_task_did_not_start(client: AdminClient) -> None:
    response = await client.http.get(
        "/api/v1/tasks/t-1/activity", headers=client.headers, params={"work_id": "w9"}
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Not found"


@pytest.mark.asyncio
async def test_task_activity_pages(client: AdminClient) -> None:
    first = await client.http.get(
        "/api/v1/tasks/t-1/activity", headers=client.headers, params={"limit": 2}
    )
    second = await client.http.get(
        "/api/v1/tasks/t-1/activity",
        headers=client.headers,
        params={"limit": 2, "cursor": first.json()["next_cursor"]},
    )

    assert len(first.json()["items"]) == 2
    assert [item["run_id"] for item in second.json()["items"]] == [
        "w1:review:1",
        "w1:review:1",
    ]


@pytest.mark.asyncio
async def test_task_activity_is_empty_for_a_task_with_no_works_and_hides_foreign_task(
    client: AdminClient,
) -> None:
    empty = await client.http.get("/api/v1/tasks/t-empty/activity", headers=client.headers)
    foreign = await client.http.get("/api/v1/tasks/t-foreign/activity", headers=client.headers)

    assert (empty.status_code, empty.json()) == (200, {"items": [], "next_cursor": None})
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_work_activity_reads_one_work(client: AdminClient) -> None:
    response = await client.http.get("/api/v1/work/w1/activity", headers=client.headers)

    assert response.status_code == 200
    assert {item["work_id"] for item in response.json()["items"]} == {"w1"}


@pytest.mark.asyncio
async def test_work_activity_refuses_the_global_scope(
    client: AdminClient,
) -> None:
    response = await client.http.get("/api/v1/work/w1/activity", headers={"X-Project-ID": "global"})

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_work_activity_404s_when_activity_exists_without_a_work_record(
    client: AdminClient,
) -> None:
    response = await client.http.get("/api/v1/work/w2/activity", headers=client.headers)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_an_unparsable_cursor_is_a_400(client: AdminClient) -> None:
    cursor = base64.urlsafe_b64encode(json.dumps("abc").encode()).decode()

    response = await client.http.get(
        "/api/v1/tasks/t-1/activity", headers=client.headers, params={"cursor": cursor}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "cursor is not an activity cursor"}
