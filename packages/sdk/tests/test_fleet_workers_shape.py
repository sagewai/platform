# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""GET /api/v1/fleet/workers returns the { workers, total } envelope.

Regression: this endpoint returned a flattened bare array, but the admin UI
reads `data.workers` (typed { workers: FleetWorker[], total }). A bare array
means `data.workers` is undefined → the fleet page's `workers.map()` crashes
the whole page.
"""
from __future__ import annotations

from datetime import datetime

import httpx
import pytest


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    from sagewai.admin.state_file import AdminStateFile

    path = tmp_path / "admin-state.json"
    sf = AdminStateFile(path=path)
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")

    import sagewai.admin.state_file as _sf_mod

    monkeypatch.setattr(_sf_mod, "default_admin_state_path", lambda: path)
    return path


@pytest.fixture
async def client(state_path):
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=state_path)
    app = create_admin_serve_app(sf)
    token = sf.validate_login("a@b.com", "pw123456")["access_token"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_fleet_workers_returns_envelope_not_bare_array(client):
    r = await client.get("/api/v1/fleet/workers")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, dict), "must be a { workers, total } object, not a bare array"
    assert isinstance(body.get("workers"), list)
    assert "total" in body
    assert body["total"] == len(body["workers"])


@pytest.mark.asyncio
async def test_fleet_worker_detail_and_decisions_return_ui_worker_envelopes(client):
    registered = await client.post(
        "/api/v1/fleet/register",
        json={
            "name": "mac-mini",
            "models": [],
            "capability_names": ["runtime.codex", "filesystem.write"],
            "pool": "coding",
        },
    )
    assert registered.status_code == 201, registered.text
    worker_id = registered.json()["worker_id"]

    detail = await client.get(f"/api/v1/fleet/workers/{worker_id}")
    assert detail.status_code == 200, detail.text
    worker = detail.json()["worker"]
    assert worker["name"] == "mac-mini"
    assert worker["approval_status"] == "pending"
    assert worker["capabilities"]["capability_names"] == [
        "runtime.codex",
        "filesystem.write",
    ]
    assert worker["capabilities"]["pool"] == "coding"

    approved = await client.post(f"/api/v1/fleet/workers/{worker_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["worker"]["approval_status"] == "approved"


@pytest.mark.asyncio
async def test_enrollment_key_creation_matches_admin_ui_contract(client):
    expires_at = "2026-09-30T12:00:00+00:00"
    created = await client.post(
        "/api/v1/fleet/enrollment-keys",
        json={
            "name": "two-device-test",
            "max_uses": 2,
            "expires_at": expires_at,
            "allowed_pools": ["coding"],
            "allowed_models": ["codex", "claude"],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "two-device-test"
    assert body["max_uses"] == 2
    assert body["allowed_pools"] == ["coding"]
    assert body["allowed_models"] == ["codex", "claude"]
    assert datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")) == (
        datetime.fromisoformat(expires_at)
    )
    assert isinstance(body["raw_key"], str) and len(body["raw_key"]) > 16
    assert "key_hash" not in body

    listed = await client.get("/api/v1/fleet/enrollment-keys")
    assert listed.status_code == 200, listed.text
    listed_key = next(item for item in listed.json()["keys"] if item["id"] == body["id"])
    assert listed_key["allowed_pools"] == ["coding"]
    assert listed_key["allowed_models"] == ["codex", "claude"]
    assert "raw_key" not in listed_key
    assert "key_hash" not in listed_key
