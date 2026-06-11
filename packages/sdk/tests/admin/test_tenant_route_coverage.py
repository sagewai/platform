# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tenant-scope coverage matrix (multi-tenancy finalization — release gate).

Two universal invariants enumerated over EVERY route of the real admin app, so a
newly-added route that forgets tenant enforcement fails CI rather than shipping a
leak:

1. A ``project:viewer`` (read-only) must be DENIED on every mutating route
   (POST/PUT/PATCH/DELETE). With role-derived token scopes, the write perimeter
   rejects a viewer before the handler runs — so any 2xx here is a route that
   escaped the perimeter.
2. A ``project:member`` (read+write, but not org-admin) must be DENIED on every
   org/system-admin route — proving those carry an org-admin gate.

These complement the per-router adversarial tests (which prove isolation-not-
absence on specific resources); this file proves COVERAGE — that no mutating or
org-level route is left ungated.
"""

from __future__ import annotations

import re

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from sagewai.admin.admin_resource_store import AdminResourceStore
from sagewai.admin.auth_middleware import _MULTI_ORG_PREFIXES
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.state_file import AdminStateFile
from sagewai.admin.tenant_audit import TenantAuditStore
from sagewai.db.engine import create_engine

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# Routes that are intentionally public (pre-auth) — excluded from the viewer gate.
_PUBLIC = (
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/auth/refresh",
    "/api/v1/setup",
)

# Org/system-admin route prefixes a project member must be denied on — imported
# from the middleware itself (single source of truth; the test can't drift from
# the gate). The artifact-destination read is checked explicitly below (its prefix
# overlaps project-scoped replay, so it's gated per-handler, not via the list).
_ORG_ADMIN_PREFIXES = _MULTI_ORG_PREFIXES


@pytest_asyncio.fixture
async def app_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'id.db'}")
    store = IdentityStore(engine=engine)
    await store.init()
    oid = (await store.bootstrap_org("Acme", "acme"))["id"]
    pa = (await store.create_project(oid, "pa", "PA"))["id"]
    viewer = await store.create_user(oid, "v@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, viewer["id"], "project:viewer", project_id=pa)
    member = await store.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, member["id"], "project:member", project_id=pa)
    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")
    # Inject the durable resource store (and audit on the SAME engine) so the
    # now-durable control-plane routes exercise their store path under
    # ASGITransport (the lifespan that would lazily build it never runs here);
    # without it a multi-tenant resource read would fail closed (503).
    res = AdminResourceStore(engine=engine)
    await res.init()
    from sagewai.admin.serve import create_admin_serve_app

    app = create_admin_serve_app(sf, identity_store=store, admin_resource_store=res)
    audit = TenantAuditStore(engine=engine)
    await audit.init()
    app.state.tenant_audit = audit
    yield {
        "app": app,
        "pa": pa,
        "viewer": await store.issue_session(oid, viewer["id"]),
        "member": await store.issue_session(oid, member["id"]),
    }
    await engine.dispose()


def _routes(app, methods: set[str] | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        rm = getattr(route, "methods", None)
        if not path or not rm:
            continue
        for m in rm:
            if methods is None or m in methods:
                out.append((m, path))
    return out


def _mutating_routes(app) -> list[tuple[str, str]]:
    return _routes(app, _MUTATING)


def _is_public(path: str) -> bool:
    return any(path.startswith(p) for p in _PUBLIC)


async def _hit(app, method: str, path: str, *, token: str, project: str):
    url = re.sub(r"\{[^}]+\}", "x", path)  # fill path params with a dummy value
    headers = {"authorization": f"Bearer {token}", "x-project-id": project}
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, url, headers=headers, json={})


@pytest.mark.asyncio
async def test_viewer_denied_on_every_mutating_route(app_ctx):
    """A read-only viewer must never get a 2xx on any mutating route."""
    app, token, pa = app_ctx["app"], app_ctx["viewer"], app_ctx["pa"]
    routes = _mutating_routes(app)
    assert routes, "no mutating routes discovered — fixture/app wiring broken"
    leaked = []
    for method, path in routes:
        if _is_public(path):
            continue
        r = await _hit(app, method, path, token=token, project=pa)
        if 200 <= r.status_code < 300:
            leaked.append(f"{method} {path} -> {r.status_code}")
    assert not leaked, "viewer reached a mutating route (missing write gate):\n" + "\n".join(leaked)


@pytest.mark.asyncio
async def test_member_denied_on_org_admin_routes_all_methods(app_ctx):
    """A project member must be denied on org/system routes — for EVERY method.

    Covers GET/list (not just mutations): a member must not READ org credentials,
    token metadata, or the remaining not-yet-project-scoped stores, whose reads
    would otherwise leak across projects.
    """
    app, token, pa = app_ctx["app"], app_ctx["member"], app_ctx["pa"]
    leaked = []
    for method, path in _routes(app):  # ALL methods, incl GET
        if _is_public(path):
            continue
        if not any(path.startswith(p) for p in _ORG_ADMIN_PREFIXES):
            continue
        r = await _hit(app, method, path, token=token, project=pa)
        if 200 <= r.status_code < 300:
            leaked.append(f"{method} {path} -> {r.status_code}")
    assert not leaked, "member reached an org-admin route (missing org-admin gate):\n" + "\n".join(
        leaked
    )


@pytest.mark.asyncio
async def test_unset_artifact_destination_is_not_accidentally_created(app_ctx):
    """A project member may read in-scope artifact destinations, but an unset one
    must stay 404 and must not be synthesized by the route."""
    app, token, pa = app_ctx["app"], app_ctx["member"], app_ctx["pa"]
    r = await _hit(
        app, "GET", "/api/v1/admin/workflows/wf-x/artifact_destination", token=token, project=pa
    )
    assert r.status_code == 404
