# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The Task defaults and trigger routes expose one project's configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sagewai.admin.tasks_routes import router
from sagewai.work.tasks import TaskStore
from sagewai.work.tasks.models import TaskTriggerSpec
from tests.db.conftest import dialect_engine  # noqa: F401


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


@pytest.mark.asyncio
async def test_defaults_read_returns_revision_zero_before_any_write(
    client: AdminClient,
) -> None:
    response = await client.http.get("/api/v1/tasks/defaults", headers=client.headers)

    assert response.status_code == 200
    assert response.json()["project_id"] == "p"
    assert response.json()["revision"] == 0
    assert response.json()["decision_channels"] == ["console"]


@pytest.mark.asyncio
async def test_defaults_put_fences_on_the_revision(client: AdminClient) -> None:
    body = {
        "defaults": {
            "project_id": "p",
            "timezone": "Europe/Berlin",
            "decision_channels": ["console", "slack_webhook"],
        },
        "expected_revision": 0,
    }

    first = await client.http.put("/api/v1/tasks/defaults", headers=client.headers, json=body)
    stale = await client.http.put("/api/v1/tasks/defaults", headers=client.headers, json=body)

    assert first.status_code == 200
    assert first.json()["revision"] == 1
    assert first.json()["timezone"] == "Europe/Berlin"
    assert stale.status_code == 409
    stored = await client.app.state.task_store.get_defaults(project_id="p")
    assert stored.decision_channels == ("console", "slack_webhook")


@pytest.mark.asyncio
async def test_defaults_put_refuses_a_body_for_another_project(client: AdminClient) -> None:
    response = await client.http.put(
        "/api/v1/tasks/defaults",
        headers=client.headers,
        json={"defaults": {"project_id": "q"}, "expected_revision": 0},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "defaults belong to another project"


@pytest.mark.asyncio
async def test_defaults_put_rejects_defaults_revision(client: AdminClient) -> None:
    response = await client.http.put(
        "/api/v1/tasks/defaults",
        headers=client.headers,
        json={"defaults": {"project_id": "p", "revision": 1}, "expected_revision": 0},
    )

    assert response.status_code == 422
    assert "send expected_revision" in str(response.json()["detail"])


@pytest.mark.asyncio
async def test_triggers_round_trip(client: AdminClient) -> None:
    spec = {
        "trigger_id": "tr-1",
        "project_id": "p",
        "source": "github_label",
        "filter": {"owner": "o", "repo": "r", "label": "sagewai"},
        "template_id": "software_delivery",
        "template_version": "1",
    }

    created = await client.http.post(
        "/api/v1/tasks/triggers", headers=client.headers, json={"trigger": spec}
    )
    updated = await client.http.post(
        "/api/v1/tasks/triggers",
        headers=client.headers,
        json={"trigger": spec | {"template_version": "2"}},
    )
    listed = await client.http.get("/api/v1/tasks/triggers", headers=client.headers)
    removed = await client.http.delete("/api/v1/tasks/triggers/tr-1", headers=client.headers)
    missing = await client.http.delete("/api/v1/tasks/triggers/tr-1", headers=client.headers)

    assert created.status_code == 201
    assert updated.status_code == 201
    triggers = listed.json()["triggers"]
    assert len(triggers) == 1
    assert triggers[0]["trigger_id"] == "tr-1"
    assert triggers[0]["template_version"] == "2"
    assert removed.status_code == 200
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_trigger_post_refuses_a_body_for_another_project(client: AdminClient) -> None:
    response = await client.http.post(
        "/api/v1/tasks/triggers",
        headers=client.headers,
        json={
            "trigger": {
                "trigger_id": "tr-2",
                "project_id": "q",
                "source": "github_label",
                "filter": {"owner": "o", "repo": "r", "label": "x"},
                "template_id": "software_delivery",
                "template_version": "1",
            }
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "trigger belongs to another project"


@pytest.mark.asyncio
async def test_triggers_list_includes_disabled_specs(client: AdminClient) -> None:
    await client.app.state.task_store.put_trigger(
        TaskTriggerSpec(
            trigger_id="tr-off",
            project_id="p",
            source="github_label",
            filter={"owner": "o", "repo": "r", "label": "x"},
            template_id="software_delivery",
            template_version="1",
            enabled=False,
        )
    )

    response = await client.http.get("/api/v1/tasks/triggers", headers=client.headers)

    assert [trigger["trigger_id"] for trigger in response.json()["triggers"]] == ["tr-off"]
