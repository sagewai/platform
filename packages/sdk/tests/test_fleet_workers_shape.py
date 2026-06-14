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
