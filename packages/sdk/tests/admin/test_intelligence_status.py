# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""GET /api/v1/intelligence/status reports the runtime intelligence stack.

The admin "Intelligence dashboard" previously rendered hardcoded DEMO data
because there was no backend. This builds the SINGLE-ORG app via
``create_admin_serve_app`` + an authenticated bearer session and drives the
real route, asserting it introspects the ``ProviderRegistry`` stack and returns
a stable ``{"components": [...]}`` shape.

It must NEVER 500: with the default config (no optional models installed) every
component still reports — degraded components fall back rather than raising.

``httpx.ASGITransport`` does not run the app lifespan, but this route reads the
intelligence registry directly (no ``app.state`` engines needed), so no extra
setup helper is required.
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport

from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile


@pytest_asyncio.fixture
async def single_org_app(tmp_path, monkeypatch):
    # SINGLE-ORG mode (the default — do NOT set SAGEWAI_TENANCY_MODE=multi).
    monkeypatch.delenv("SAGEWAI_TENANCY_MODE", raising=False)
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(
        org_name="Acme",
        admin_email="a@acme.io",
        admin_password="pw123456",
    )
    login = sf.validate_login("a@acme.io", "pw123456")
    token = login["access_token"]

    app = create_admin_serve_app(sf)
    yield {"app": app, "token": token}


async def _get(app, path, *, token):
    headers = {"authorization": f"Bearer {token}"}
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.get(path, headers=headers)


async def test_intelligence_status_returns_components_list(single_org_app):
    app, token = single_org_app["app"], single_org_app["token"]

    resp = await _get(app, "/api/v1/intelligence/status", token=token)

    # Never a 500 — resilient introspection even with no optional models.
    assert resp.status_code == 200
    body = resp.json()

    assert isinstance(body.get("components"), list)
    components = body["components"]
    assert components, "expected at least one intelligence component"

    # Every item has the stable contract the frontend wires against.
    for comp in components:
        assert isinstance(comp.get("name"), str) and comp["name"]
        assert isinstance(comp.get("impl"), str) and comp["impl"]
        assert isinstance(comp.get("available"), bool)
        # config is always a (possibly empty) dict — never null/missing.
        assert isinstance(comp.get("config"), dict)


async def test_intelligence_status_includes_embedder(single_org_app):
    app, token = single_org_app["app"], single_org_app["token"]

    resp = await _get(app, "/api/v1/intelligence/status", token=token)
    assert resp.status_code == 200

    by_name = {c["name"]: c for c in resp.json()["components"]}
    assert "embedder" in by_name
    emb = by_name["embedder"]
    # impl resolves to a concrete backend class name; not the empty string.
    assert emb["impl"]


async def test_intelligence_status_covers_full_stack(single_org_app):
    app, token = single_org_app["app"], single_org_app["token"]

    resp = await _get(app, "/api/v1/intelligence/status", token=token)
    assert resp.status_code == 200

    names = {c["name"] for c in resp.json()["components"]}
    # The registry exposes embedder + NER + relation + language + multimodal
    # (vision/transcriber) + graph. Assert the headline components are present.
    assert {"embedder", "entity_extractor", "language", "vision"} <= names


async def test_intelligence_status_requires_auth(single_org_app):
    app = single_org_app["app"]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/intelligence/status")
    # Unauthenticated read is rejected by the auth middleware (not a 500).
    assert resp.status_code in (401, 403)
