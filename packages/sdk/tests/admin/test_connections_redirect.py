# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Verify /api/v1/admin/inference-providers/* still returns 308 to /connections."""
from __future__ import annotations

import httpx
import pytest

from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile


@pytest.fixture()
def app(tmp_path):
    state = AdminStateFile(tmp_path / "state.json")
    return create_admin_serve_app(state)


@pytest.mark.asyncio
async def test_old_inference_providers_path_redirects(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        resp = await c.get("/api/v1/admin/inference-providers/runpod")
    assert resp.status_code == 308
    assert resp.headers["location"].startswith("/api/v1/admin/connections")


@pytest.mark.asyncio
async def test_old_inference_providers_root_redirects(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        resp = await c.get("/api/v1/admin/inference-providers")
    assert resp.status_code == 308
    assert resp.headers["location"] == "/api/v1/admin/connections"


@pytest.mark.asyncio
async def test_old_inference_providers_post_redirects(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        resp = await c.post("/api/v1/admin/inference-providers/runpod/test")
    assert resp.status_code == 308
    assert "/api/v1/admin/connections" in resp.headers["location"]
