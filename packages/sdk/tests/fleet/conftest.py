# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from sagewai.work.activity import WorkActivityStore
from sagewai.work.activity_ingestion import ActivityIngestion
from sagewai.work.fleet import FleetOperatorTaskPayload
from sagewai.work.store import WorkStore
from sagewai.work.tasks import TaskStore
from tests.work.test_fleet_runtime import _capabilities, _capsule, _request


@dataclass(frozen=True)
class Stores:
    activity: WorkActivityStore


@dataclass(frozen=True)
class Worker:
    id: str
    secret: str
    headers: dict[str, str]


def _task_payload() -> dict[str, Any]:
    capabilities = _capabilities().model_dump(mode="json")
    capabilities["grants"][0]["credential_ref"] = None
    return FleetOperatorTaskPayload(
        request=_request(),
        capsule=_capsule(),
        capabilities=capabilities,
        required_capabilities=("runtime.claude", "cli.git"),
        workspace=None,
    ).model_dump(mode="json")


@pytest_asyncio.fixture
async def app(tmp_path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FastAPI]:
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.db import factory

    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    factory.reset_engine()
    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(
        org_name="Acme",
        admin_email="admin@example.test",
        admin_password="pw123456",
    )
    app = create_admin_serve_app(sf)
    await factory.ensure_schema()
    engine = factory.get_engine()
    work_store = WorkStore(engine=engine)
    activity_store = WorkActivityStore(engine=engine)
    task_store = TaskStore(engine=engine)
    await work_store.init()
    await activity_store.init()
    await task_store.init()
    app.state.work_store = work_store
    app.state.activity_store = activity_store
    app.state.task_store = task_store
    app.state.activity_ingestion = ActivityIngestion(
        work_store=work_store,
        task_store=task_store,
        activity_store=activity_store,
    )
    try:
        yield app
    finally:
        factory.reset_engine()


@pytest.fixture
def admin_headers(app: FastAPI) -> dict[str, str]:
    token = app.state.admin_state_file.validate_login(
        "admin@example.test",
        "pw123456",
    )["access_token"]
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Project-ID": "project-a",
    }


@pytest_asyncio.fixture
async def client(
    app: FastAPI,
    admin_headers: dict[str, str],
) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=admin_headers,
    ) as http:
        yield http


@pytest.fixture
def stores(app: FastAPI) -> Stores:
    return Stores(activity=app.state.activity_store)


async def _register_worker(
    client: httpx.AsyncClient,
    *,
    name: str,
    approved: bool,
    project_id: str = "project-a",
) -> Worker:
    project_headers = {"X-Project-ID": project_id}
    response = await client.post(
        "/api/v1/fleet/register",
        headers=project_headers,
        json={
            "name": name,
            "models": ["gpt-4o"],
            "capability_names": ["runtime.claude", "cli.git"],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    worker = Worker(
        id=body["worker_id"],
        secret=body["worker_secret"],
        headers={
            "X-Worker-Id": body["worker_id"],
            "X-Worker-Secret": body["worker_secret"],
        },
    )
    if approved:
        approved_response = await client.post(
            f"/api/v1/fleet/workers/{worker.id}/approve",
            headers=project_headers,
        )
        assert approved_response.status_code == 200, approved_response.text
    return worker


@pytest_asyncio.fixture
async def worker(client: httpx.AsyncClient) -> Worker:
    return await _register_worker(client, name="worker-1", approved=True)


@pytest_asyncio.fixture
async def other_worker(client: httpx.AsyncClient) -> Worker:
    return await _register_worker(client, name="worker-2", approved=True)


@pytest_asyncio.fixture
async def other_project_worker(client: httpx.AsyncClient) -> Worker:
    return await _register_worker(
        client,
        name="worker-project-b",
        approved=True,
        project_id="project-b",
    )


@pytest_asyncio.fixture
async def unapproved_worker(client: httpx.AsyncClient) -> Worker:
    return await _register_worker(client, name="worker-pending", approved=False)


@pytest_asyncio.fixture
async def claimed_task(
    app: FastAPI,
    client: httpx.AsyncClient,
    worker: Worker,
) -> dict[str, str]:
    request = _request()
    record = await app.state.fleet_registry.get_worker(worker.id)
    assert record is not None
    await app.state.fleet_task_store.enqueue(
        {
            "run_id": request.run_id,
            "org_id": record.org_id,
            "project_id": request.project_id,
            "pool": "default",
            "labels": {},
            "payload": _task_payload(),
        }
    )
    claim = await client.post("/api/v1/fleet/claim", json={}, headers=worker.headers)
    assert claim.status_code == 200, claim.text
    return {
        "run_id": request.run_id,
        "work_id": request.work_id,
        "project_id": request.project_id,
    }
