# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task pause, resume and cancel routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sagewai.admin.tasks_routes import router
from sagewai.artifacts import LocalArtifactStore
from sagewai.work.tasks import TaskDefaults, TaskService, TaskStore
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


@pytest.mark.asyncio
async def test_pause_resume_and_cancel_move_the_status(client: AdminClient) -> None:
    task_id = await _create(client)

    paused = await client.http.post(f"/api/v1/tasks/{task_id}/pause", headers=client.headers)
    resumed = await client.http.post(f"/api/v1/tasks/{task_id}/resume", headers=client.headers)
    cancelled = await client.http.post(
        f"/api/v1/tasks/{task_id}/cancel", headers=client.headers, json={"note": "done by hand"}
    )
    thread = await client.http.get(f"/api/v1/tasks/{task_id}/thread", headers=client.headers)

    assert paused.json()["status"] == "PAUSED"
    assert resumed.json()["status"] == "PLANNING"
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["board_column"] == "done"
    assert "done by hand" in [entry["text"] for entry in thread.json()["entries"]]


@pytest.mark.asyncio
async def test_resuming_a_running_task_is_a_409(client: AdminClient) -> None:
    task_id = await _create(client)

    response = await client.http.post(f"/api/v1/tasks/{task_id}/resume", headers=client.headers)

    assert response.status_code == 409
    assert "not PAUSED" in response.json()["detail"]


@pytest.mark.asyncio
async def test_pausing_a_cancelled_task_is_a_409(client: AdminClient) -> None:
    task_id = await _create(client)
    await client.http.post(f"/api/v1/tasks/{task_id}/cancel", headers=client.headers, json={})

    response = await client.http.post(f"/api/v1/tasks/{task_id}/pause", headers=client.headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "cannot move Task from CANCELLED to PAUSED"


@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
@pytest.mark.asyncio
async def test_the_lifecycle_routes_404_for_an_unknown_task(
    client: AdminClient, action: str
) -> None:
    response = await client.http.post(
        f"/api/v1/tasks/missing/{action}", headers=client.headers, json={}
    )
    assert response.status_code == 404


@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
@pytest.mark.asyncio
async def test_a_lifecycle_route_that_loses_the_append_race_is_a_409(
    client: AdminClient, monkeypatch, action: str
) -> None:
    from sagewai.work.tasks.store import StaleTaskError

    task_id = await _create(client)
    if action == "resume":
        await client.http.post(f"/api/v1/tasks/{task_id}/pause", headers=client.headers)

    async def _stale(*_args, **_kwargs):
        raise StaleTaskError("projection changed under the append")

    monkeypatch.setattr(client.app.state.task_store, "append", _stale)

    response = await client.http.post(
        f"/api/v1/tasks/{task_id}/{action}", headers=client.headers, json={}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "projection changed under the append"


@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
@pytest.mark.asyncio
async def test_the_lifecycle_routes_refuse_global_scope(client: AdminClient, action: str) -> None:
    response = await client.http.post(
        f"/api/v1/tasks/missing/{action}", headers={"X-Project-ID": "global"}, json={}
    )

    assert response.status_code == 400
