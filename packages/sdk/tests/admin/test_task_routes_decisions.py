# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The Task decisions route merges Task and Work attention."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sagewai.admin.tasks_routes import router
from sagewai.work import WorkStore
from sagewai.work.tasks import TaskStore
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_inbox import NOW, _seed_presented_task, _seed_work_gate


@dataclass
class AdminClient:
    app: Any
    http: httpx.AsyncClient
    headers: dict[str, str]


@pytest.fixture
async def client(dialect_engine) -> AdminClient:  # noqa: F811
    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    await _seed_presented_task(
        task_store, "t-1", urgency="this_week", due_at=NOW + timedelta(days=3)
    )
    await _seed_work_gate(work_store, "w1")
    app = FastAPI()
    app.state.task_store = task_store
    app.state.work_store = work_store
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield AdminClient(app=app, http=http, headers={"X-Project-ID": "p"})


@pytest.mark.asyncio
async def test_decisions_merges_both_sources_soonest_first(client: AdminClient) -> None:
    response = await client.http.get("/api/v1/tasks/decisions", headers=client.headers)

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["kind"] for item in items] == ["work", "task"]
    assert items[0]["work_id"] == "w1"
    assert items[0]["gate_id"] == "merge:w1:3"
    assert items[0]["decided_by"] == "work"
    assert items[1]["task_id"] == "t-1"
    assert items[1]["urgency"] == "this_week"
    assert items[1]["decided_by"] == "task"


@pytest.mark.asyncio
async def test_decisions_is_empty_for_a_quiet_project(client: AdminClient) -> None:
    response = await client.http.get("/api/v1/tasks/decisions", headers={"X-Project-ID": "quiet"})

    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.asyncio
async def test_decisions_refuses_the_global_scope(client: AdminClient) -> None:
    response = await client.http.get("/api/v1/tasks/decisions", headers={"X-Project-ID": "global"})
    assert response.status_code == 400
