# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Adversarial cross-tenant isolation suite (release gate, W2/W3 — RFC §9).

Drives the AuthMiddleware end-to-end in multi-tenant mode against an inline app
and asserts the boundary holds: tenancy is derived from the authenticated
session (never the forgeable X-Project-ID), cross-tenant access returns 404 (no
existence leak), and RBAC denials return 403. Runs on SQLite always, and on
Postgres when SAGEWAI_TEST_DATABASE_URL is set.

Grown across W4–W8 as real resource routes adopt the scoping primitive.
"""

import httpx
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport

from sagewai.admin.auth_middleware import AuthMiddleware
from sagewai.admin.authz import PermissionDeniedError, Resource, require
from sagewai.admin.identity_store import IdentityStore


class _StubSF:
    """Minimal AdminStateFile stand-in for the middleware's public-path + CSRF hooks."""

    def is_setup_complete(self) -> bool:
        return True

    def get_or_create_csrf_secret(self) -> str:
        return "csrf-secret"


def _make_app(store: IdentityStore) -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware, sf=_StubSF(), identity_store=store)

    @app.get("/api/v1/whoami")
    async def whoami(request: Request):
        ctx = request.state.context
        return {"org": ctx.org_id, "project": ctx.project_id, "roles": sorted(ctx.roles)}

    @app.post("/api/v1/write")
    async def write(request: Request):
        ctx = request.state.context
        try:
            require("resource:write", ctx, on=Resource(ctx.org_id, ctx.project_id))
        except PermissionDeniedError:
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        return {"ok": True}

    return app


@pytest_asyncio.fixture
async def env(dialect_engine, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    store = IdentityStore(engine=dialect_engine)
    await store.init()
    oid = (await store.bootstrap_org("Acme", "acme"))["id"]
    admin = await store.create_user(oid, "admin@acme.io", password="pw0000", role="org:admin")
    pa = (await store.create_project(oid, "pa", "PA"))["id"]
    pb = (await store.create_project(oid, "pb", "PB"))["id"]
    member = await store.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, member["id"], "project:member", project_id=pa)
    viewer = await store.create_user(oid, "v@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, viewer["id"], "project:viewer", project_id=pa)
    # A genuinely project-only user (no org-level role) — via a project invitation.
    _rec, tok = await store.create_invitation(
        oid, "po@acme.io", "project:member", admin["id"], project_id=pa
    )
    po = await store.accept_invitation(tok, password="pw0000")
    return {
        "app": _make_app(store),
        "oid": oid,
        "pa": pa,
        "pb": pb,
        "sess_admin": await store.issue_session(oid, admin["id"]),
        "sess_member": await store.issue_session(oid, member["id"]),
        "sess_viewer": await store.issue_session(oid, viewer["id"]),
        "sess_po": await store.issue_session(oid, po["id"]),
    }


async def _req(app, method, path, *, token=None, project=None):
    headers = {}
    if token:
        headers["authorization"] = f"Bearer {token}"
    if project:
        headers["x-project-id"] = project
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, path, headers=headers)


async def test_unauthenticated_is_401(env):
    r = await _req(env["app"], "GET", "/api/v1/whoami")
    assert r.status_code == 401


async def test_invalid_session_is_401(env):
    r = await _req(env["app"], "GET", "/api/v1/whoami", token="not-a-real-session")
    assert r.status_code == 401


async def test_member_resolves_own_project_from_session(env):
    r = await _req(env["app"], "GET", "/api/v1/whoami", token=env["sess_member"], project=env["pa"])
    assert r.status_code == 200
    body = r.json()
    assert body["project"] == env["pa"]
    assert "project:member" in body["roles"]


async def test_org_member_no_hint_is_org_scope(env):
    # An org-role user with no hint defaults to org scope (project None), per RFC §4.
    r = await _req(env["app"], "GET", "/api/v1/whoami", token=env["sess_member"])
    assert r.status_code == 200
    assert r.json()["project"] is None


async def test_project_only_user_no_hint_lands_in_single_project(env):
    # A project-only user (no org role) with one project resolves to it without a hint.
    r = await _req(env["app"], "GET", "/api/v1/whoami", token=env["sess_po"])
    assert r.status_code == 200
    assert r.json()["project"] == env["pa"]


async def test_forged_project_header_is_404_not_403(env):
    # Member of PA forges X-Project-ID: PB -> hidden (404), never a 403 leak.
    r = await _req(env["app"], "GET", "/api/v1/whoami", token=env["sess_member"], project=env["pb"])
    assert r.status_code == 404


async def test_org_admin_can_enter_any_project(env):
    r = await _req(env["app"], "GET", "/api/v1/whoami", token=env["sess_admin"], project=env["pb"])
    assert r.status_code == 200
    assert r.json()["project"] == env["pb"]


async def test_rbac_viewer_cannot_write(env):
    r = await _req(env["app"], "POST", "/api/v1/write", token=env["sess_viewer"], project=env["pa"])
    assert r.status_code == 403


async def test_rbac_member_can_write(env):
    r = await _req(env["app"], "POST", "/api/v1/write", token=env["sess_member"], project=env["pa"])
    assert r.status_code == 200
