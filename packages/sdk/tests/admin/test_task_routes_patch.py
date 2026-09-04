# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task definition PATCH route."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sagewai.admin.tasks_routes import router
from sagewai.artifacts import LocalArtifactStore
from sagewai.work.tasks import Budget, TaskDefaults, TaskService, TaskStatus, TaskStore
from sagewai.work.tasks.writer import TaskWriter, status_entry
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _task

BRIEF = (
    "Implement the retry queue in the payments service repository, add the failing test first, "
    "and open a pull request when the deterministic verification command passes."
)


@dataclass
class AdminClient:
    app: Any
    http: httpx.AsyncClient
    headers: dict[str, str]


@pytest.fixture
async def client(dialect_engine, tmp_path) -> AdminClient:  # noqa: F811
    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    await task_store.put_defaults(
        TaskDefaults(project_id="p", target=_task(project_id="p").target), expected_revision=0
    )
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    app = FastAPI()
    app.state.task_store = task_store
    app.state.task_service = TaskService(store=task_store, artifact_store=artifacts)
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield AdminClient(app=app, http=http, headers={"X-Project-ID": "p"})


async def _create(client: AdminClient, brief: str = BRIEF) -> str:
    response = await client.http.post(
        "/api/v1/tasks", headers=client.headers, json={"brief": brief}
    )
    assert response.status_code == 201, response.text
    return response.json()["task"]["id"]


def _budget(**overrides) -> dict:
    return {**Budget().model_dump(mode="json"), **overrides}


@pytest.mark.asyncio
async def test_a_patch_at_the_current_revision_rewrites_the_budget(
    client: AdminClient,
) -> None:
    task_id = await _create(client)
    current = await client.http.get(f"/api/v1/tasks/{task_id}", headers=client.headers)

    response = await client.http.patch(
        f"/api/v1/tasks/{task_id}",
        headers=client.headers,
        json={
            "budget": _budget(max_cycle_usd="25.00"),
            "revision": current.json()["record"]["revision"],
        },
    )
    reread = await client.http.get(f"/api/v1/tasks/{task_id}", headers=client.headers)
    thread = await client.http.get(f"/api/v1/tasks/{task_id}/thread", headers=client.headers)

    assert response.status_code == 200
    assert response.json()["task"]["budget"]["max_cycle_usd"] == "25.00"
    assert reread.json()["task"]["budget"]["max_cycle_usd"] == "25.00"
    assert [entry["text"] for entry in thread.json()["entries"] if entry["kind"] == "message"] == [
        "budget updated"
    ]


@pytest.mark.asyncio
async def test_a_patch_on_an_exhausted_task_revives_it(client: AdminClient) -> None:
    task_id = await _create(client)
    store = client.app.state.task_store
    _task_def, record = await store.load(task_id, project_id="p")
    running = await TaskWriter(store).append(record, [status_entry(record, TaskStatus.EXECUTING)])
    exhausted = await TaskWriter(store).append(
        running, [status_entry(running, TaskStatus.BUDGET_EXHAUSTED)]
    )

    response = await client.http.patch(
        f"/api/v1/tasks/{task_id}",
        headers=client.headers,
        json={
            "budget": _budget(max_cycle_usd="25.00"),
            "revision": exhausted.revision,
        },
    )

    assert response.status_code == 200
    assert response.json()["record"]["status"] == "EXECUTING"


@pytest.mark.asyncio
async def test_a_patch_at_a_stale_revision_is_a_409(client: AdminClient) -> None:
    task_id = await _create(client)

    response = await client.http.patch(
        f"/api/v1/tasks/{task_id}",
        headers=client.headers,
        json={"budget": _budget(max_cycle_usd="25.00"), "revision": 99},
    )
    reread = await client.http.get(f"/api/v1/tasks/{task_id}", headers=client.headers)

    assert response.status_code == 409
    assert reread.json()["task"]["budget"]["max_cycle_usd"] == "10.00"


@pytest.mark.asyncio
async def test_a_patch_on_an_unknown_task_is_a_404(client: AdminClient) -> None:
    response = await client.http.patch(
        "/api/v1/tasks/missing",
        headers=client.headers,
        json={"budget": _budget(), "revision": 1},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_the_patch_route_refuses_global_scope(client: AdminClient) -> None:
    response = await client.http.patch(
        "/api/v1/tasks/missing",
        headers={"X-Project-ID": "global"},
        json={"budget": _budget(), "revision": 1},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [TaskStatus.COMPLETE, TaskStatus.CANCELLED])
async def test_a_patch_on_a_terminal_task_is_a_409(
    client: AdminClient, terminal: TaskStatus
) -> None:
    task_id = await _create(client)
    store = client.app.state.task_store
    _task_def, record = await store.load(task_id, project_id="p")
    if terminal is TaskStatus.COMPLETE:
        running = await TaskWriter(store).append(
            record, [status_entry(record, TaskStatus.EXECUTING)]
        )
        assessing = await TaskWriter(store).append(
            running, [status_entry(running, TaskStatus.ASSESSING)]
        )
        closed = await TaskWriter(store).append(assessing, [status_entry(assessing, terminal)])
    else:
        closed = await TaskWriter(store).append(record, [status_entry(record, terminal)])

    response = await client.http.patch(
        f"/api/v1/tasks/{task_id}",
        headers=client.headers,
        json={"budget": _budget(max_cycle_usd="25.00"), "revision": closed.revision},
    )
    reread = await client.http.get(f"/api/v1/tasks/{task_id}", headers=client.headers)
    thread = await client.http.get(f"/api/v1/tasks/{task_id}/thread", headers=client.headers)

    assert response.status_code == 409
    assert reread.json()["task"]["budget"]["max_cycle_usd"] == "10.00"
    assert [entry["text"] for entry in thread.json()["entries"] if entry["kind"] == "message"] == []


@pytest.mark.asyncio
async def test_a_patch_of_anything_but_the_budget_is_rejected(client: AdminClient) -> None:
    task_id = await _create(client)

    response = await client.http.patch(
        f"/api/v1/tasks/{task_id}",
        headers=client.headers,
        json={"budget": _budget(), "revision": 1, "routing": {"roles": {}}},
    )

    assert response.status_code == 422
