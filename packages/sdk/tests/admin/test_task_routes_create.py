# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Creating a Task, previewing intake, and reading the template catalogue."""

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
    app = FastAPI()
    app.state.task_store = task_store
    app.state.task_service = TaskService(
        store=task_store, artifact_store=LocalArtifactStore(root=tmp_path / "objects")
    )
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield AdminClient(app=app, http=http, headers={"X-Project-ID": "p"})


@pytest.mark.asyncio
async def test_create_returns_the_task_and_its_record(client: AdminClient) -> None:
    response = await client.http.post(
        "/api/v1/tasks", headers=client.headers, json={"brief": BRIEF}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["task"]["project_id"] == "p"
    assert body["task"]["origin"] == "human"
    assert body["task"]["created_by"] == "admin"
    assert body["task"]["brief_summary"] == BRIEF
    assert body["record"]["task_id"] == body["task"]["id"]
    stored = await client.app.state.task_store.load(body["task"]["id"], project_id="p")
    assert stored is not None


@pytest.mark.asyncio
async def test_create_accepts_origin_and_source_refs(client: AdminClient) -> None:
    response = await client.http.post(
        "/api/v1/tasks",
        headers=client.headers,
        json={"brief": BRIEF, "origin_ref": "issue:7", "source_ref": "console"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["task"]["origin_ref"] == "issue:7"
    assert body["task"]["source_ref"] == "console"


@pytest.mark.asyncio
async def test_create_refuses_unknown_fields(client: AdminClient) -> None:
    response = await client.http.post(
        "/api/v1/tasks",
        headers=client.headers,
        json={"brief": BRIEF, "surprise": "no"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_without_a_matching_target_is_a_409(client: AdminClient) -> None:
    await client.app.state.task_store.put_defaults(
        TaskDefaults(project_id="p"), expected_revision=1
    )

    response = await client.http.post(
        "/api/v1/tasks", headers=client.headers, json={"brief": BRIEF}
    )

    assert response.status_code == 409
    assert "target" in response.json()["detail"]


@pytest.mark.asyncio
async def test_intake_and_create_agree_when_the_project_has_no_target(
    client: AdminClient,
) -> None:
    await client.app.state.task_store.put_defaults(
        TaskDefaults(project_id="p"), expected_revision=1
    )

    preview = await client.http.post(
        "/api/v1/tasks/intake", headers=client.headers, json={"brief": BRIEF}
    )
    created = await client.http.post(
        "/api/v1/tasks", headers=client.headers, json={"brief": BRIEF}
    )

    assert preview.status_code == 200
    assert preview.json()["questions"] == []
    assert created.status_code == 409
    assert created.json()["detail"] in preview.json()["preview"]


@pytest.mark.asyncio
async def test_create_refuses_an_empty_brief(client: AdminClient) -> None:
    response = await client.http.post("/api/v1/tasks", headers=client.headers, json={"brief": ""})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_refuses_a_whitespace_only_brief(client: AdminClient) -> None:
    response = await client.http.post(
        "/api/v1/tasks", headers=client.headers, json={"brief": "   \n\t"}
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_brief_bodies_refuse_overlong_briefs(client: AdminClient) -> None:
    oversized = "x" * 64_001

    created = await client.http.post(
        "/api/v1/tasks", headers=client.headers, json={"brief": oversized}
    )
    previewed = await client.http.post(
        "/api/v1/tasks/intake", headers=client.headers, json={"brief": oversized}
    )

    assert created.status_code == 422
    assert previewed.status_code == 422


@pytest.mark.asyncio
async def test_intake_previews_without_writing_anything(client: AdminClient) -> None:
    response = await client.http.post(
        "/api/v1/tasks/intake", headers=client.headers, json={"brief": BRIEF}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["template_id"] == "software_delivery"
    assert body["band"] in {"auto_route", "picker", "synthesis"}
    assert body["preview"]
    assert await client.app.state.task_store.list_records(project_id="p") == []


@pytest.mark.asyncio
async def test_templates_lists_the_catalogue(client: AdminClient) -> None:
    response = await client.http.get("/api/v1/tasks/templates", headers=client.headers)

    assert response.status_code == 200
    body = response.json()
    templates = body["templates"]
    assert [template["id"] for template in templates] == [
        "software_delivery",
        "scheduled_research_report",
    ]
    assert body["reserved"] == ["event_triage", "batch_extract"]
    assert templates[0]["profile"] == "software"


@pytest.mark.asyncio
async def test_create_and_intake_refuse_the_global_scope(client: AdminClient) -> None:
    created = await client.http.post(
        "/api/v1/tasks", headers={"X-Project-ID": "global"}, json={"brief": BRIEF}
    )
    previewed = await client.http.post(
        "/api/v1/tasks/intake", headers={"X-Project-ID": "global"}, json={"brief": BRIEF}
    )
    templated = await client.http.get("/api/v1/tasks/templates", headers={"X-Project-ID": "global"})

    assert created.status_code == 400
    assert previewed.status_code == 400
    assert templated.status_code == 400


def test_service_errors_map_the_lookup_and_the_refusals() -> None:
    from fastapi import HTTPException

    from sagewai.admin.tasks_routes import _service_errors
    from sagewai.work.tasks.service import TaskDecisionError, TaskNotFoundError

    with pytest.raises(HTTPException) as missing:
        with _service_errors():
            raise TaskNotFoundError("t-1")
    assert (missing.value.status_code, missing.value.detail) == (404, "Not found")
    with pytest.raises(HTTPException) as refused:
        with _service_errors():
            raise TaskDecisionError("no")
    assert (refused.value.status_code, refused.value.detail) == (409, "no")
