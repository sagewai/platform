# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end cross-tenant isolation on the REAL admin routes (release gate).

Builds the full ``create_admin_serve_app`` in multi-tenant mode with an injected,
seeded IdentityStore and drives the actual provider routes — proving the seam
composes on real endpoints (not just the primitives): a forged project header
404s, a cross-project resource id 404s, and a viewer write 403s. Denial paths
only, so this is deterministic and free of audit/engine entanglement.
"""

import httpx
import pytest_asyncio
from httpx import ASGITransport

from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.state_file import AdminStateFile
from sagewai.db.engine import create_engine


@pytest_asyncio.fixture
async def real_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))  # isolate home (no real master.key)

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'id.db'}")
    store = IdentityStore(engine=engine)
    await store.init()
    oid = (await store.bootstrap_org("Acme", "acme"))["id"]
    pa = (await store.create_project(oid, "pa", "PA"))["id"]
    pb = (await store.create_project(oid, "pb", "PB"))["id"]
    member = await store.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, member["id"], "project:member", project_id=pa)
    viewer = await store.create_user(oid, "v@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, viewer["id"], "project:viewer", project_id=pa)

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")
    prov_b = sf.upsert_provider({"provider_name": "openai", "config": {}, "project_id": pb})

    from sagewai.admin.serve import create_admin_serve_app

    app = create_admin_serve_app(sf, identity_store=store)
    yield {
        "app": app,
        "pa": pa,
        "pb": pb,
        "prov_b": prov_b["id"],
        "sess_member": await store.issue_session(oid, member["id"]),
        "sess_viewer": await store.issue_session(oid, viewer["id"]),
    }
    await engine.dispose()


async def _req(app, method, path, *, token, project=None, json=None):
    headers = {"authorization": f"Bearer {token}"}
    if project:
        headers["x-project-id"] = project
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, path, headers=headers, json=json)


async def test_forged_project_header_404_on_real_route(real_app):
    # Member of PA forges X-Project-ID: PB -> middleware 404s before the route runs.
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/providers",
        token=real_app["sess_member"],
        project=real_app["pb"],
    )
    assert r.status_code == 404


async def test_cross_project_provider_delete_404_on_real_route(real_app):
    # Member of PA (scoped to PA) deletes PB's provider by id -> route scope 404.
    r = await _req(
        real_app["app"],
        "DELETE",
        f"/api/v1/providers/{real_app['prov_b']}",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 404


async def test_viewer_cannot_write_provider_403_on_real_route(real_app):
    # A project:viewer (read-only) cannot create a provider -> route RBAC 403.
    r = await _req(
        real_app["app"],
        "POST",
        "/api/v1/providers",
        token=real_app["sess_viewer"],
        project=real_app["pa"],
        json={"provider_name": "evil", "config": {}},
    )
    assert r.status_code == 403


async def test_member_reads_own_project_200_on_real_route(real_app):
    # Sanity: a member CAN list providers in their own project (not a blanket deny).
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/providers",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
