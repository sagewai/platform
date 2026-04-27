# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for /api/v1/admin/workflows/{name}/artifact_destination routes — Plan ART."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """Use a tmp admin-state.json file scoped to the test."""
    path = tmp_path / "admin-state.json"
    path.write_text(json.dumps({"workflows": {}}))

    from sagewai.admin import state_file as state_file_module

    monkeypatch.setattr(
        state_file_module, "_DEFAULT_STATE_FILE", path,
    )
    return path


@pytest.fixture
async def client(state_file):
    from sagewai.admin import artifact_destination_routes

    app = FastAPI()
    artifact_destination_routes.register(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test",
    ) as cl:
        yield cl


@pytest.mark.asyncio
async def test_get_returns_404_when_no_override(client):
    res = await client.get(
        "/api/v1/admin/workflows/some-wf/artifact_destination",
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_put_then_get_round_trips_destination(client, state_file):
    body = {
        "type": "github",
        "target": "https://github.com/acme/portfolio.git",
        "env_keys": ["GITHUB_TOKEN"],
        "options": {"branch": "main"},
    }
    put = await client.put(
        "/api/v1/admin/workflows/build-portfolio/artifact_destination",
        json=body,
    )
    assert put.status_code == 200, put.text
    assert put.json() == body

    get = await client.get(
        "/api/v1/admin/workflows/build-portfolio/artifact_destination",
    )
    assert get.status_code == 200
    assert get.json() == body

    # Verify state persisted to disk
    state = json.loads(state_file.read_text())
    assert state["workflows"]["build-portfolio"]["artifact_destination"] == body


@pytest.mark.asyncio
async def test_put_rejects_invalid_target(client):
    bad = {
        "type": "github",
        "target": "https://gitlab.com/acme/portfolio.git",
        "env_keys": [],
        "options": {},
    }
    put = await client.put(
        "/api/v1/admin/workflows/some-wf/artifact_destination",
        json=bad,
    )
    assert put.status_code == 400
    assert "github" in put.text.lower()


@pytest.mark.asyncio
async def test_put_rejects_unknown_type(client):
    bad = {
        "type": "ftp",
        "target": "ftp://example.com",
        "env_keys": [],
        "options": {},
    }
    put = await client.put(
        "/api/v1/admin/workflows/some-wf/artifact_destination",
        json=bad,
    )
    # Pydantic schema validation rejects unknown enum value with 422
    assert put.status_code == 422


@pytest.mark.asyncio
async def test_delete_clears_override(client, state_file):
    body = {
        "type": "local",
        "target": "/host/output",
        "env_keys": [],
        "options": {},
    }
    await client.put(
        "/api/v1/admin/workflows/wf-x/artifact_destination",
        json=body,
    )
    delete = await client.delete(
        "/api/v1/admin/workflows/wf-x/artifact_destination",
    )
    assert delete.status_code == 204

    get = await client.get(
        "/api/v1/admin/workflows/wf-x/artifact_destination",
    )
    assert get.status_code == 404

    state = json.loads(state_file.read_text())
    assert "artifact_destination" not in state["workflows"].get("wf-x", {})
