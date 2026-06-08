# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cross-tenant isolation on the REAL connection routes (release gate, PR1b).

The connections router never adopted the W2/W4 tenant boundary: it scoped from
the forgeable ``x-project-id`` header (not the session) and the by-id routes did
no scope check at all, so in multi-tenant mode a project member could read /
mutate / delete / export another project's connections by id. These tests drive
the actual routes (file-backed store; the Postgres store is PR1c) and assert the
boundary now holds: cross-project ids are 404 (hidden), inherited org-shared rows
are read-only to project members (403), viewers cannot write, and export is
pinned to the session project.
"""

import httpx
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport

from sagewai.admin import tenant_keys
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.state_file import AdminStateFile
from sagewai.connections.store import ConnectionStore
from sagewai.db.engine import create_engine


@pytest_asyncio.fixture
async def conn_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))
    _master = (Fernet.generate_key(), "test")
    monkeypatch.setattr(tenant_keys, "_master_key_source", lambda: _master)

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

    # Seed connections into the SAME file the routes read (tmp_path/config/...).
    cstore = ConnectionStore(tmp_path / "config" / "connections.json")
    pd = {"url": "https://example.com"}
    conn_pa = cstore.create(
        protocol="http", project_id=pa, display_name="pa-conn", tags=[], protocol_data=pd
    )
    conn_pb = cstore.create(
        protocol="http", project_id=pb, display_name="pb-conn", tags=[], protocol_data=pd
    )
    conn_global = cstore.create(
        protocol="http", project_id=None, display_name="shared", tags=[], protocol_data=pd
    )
    # A PB-owned MCP connection to exercise the plugin extra-route guard.
    conn_pb_mcp = cstore.create(
        protocol="mcp", project_id=pb, display_name="pb-mcp", tags=[], protocol_data={}
    )

    from sagewai.admin.serve import create_admin_serve_app

    app = create_admin_serve_app(sf, identity_store=store)
    yield {
        "app": app,
        "pa": pa,
        "pb": pb,
        "conn_pa": conn_pa.id,
        "conn_pb": conn_pb.id,
        "conn_global": conn_global.id,
        "conn_pb_mcp": conn_pb_mcp.id,
        "sess_member": await store.issue_session(oid, member["id"]),
        "sess_viewer": await store.issue_session(oid, viewer["id"]),
    }
    await engine.dispose()


async def _req(app, method, path, *, token, project=None, json=None):
    headers = {"authorization": f"Bearer {token}", "cookie": f"sagewai_auth={token}"}
    if project:
        headers["x-project-id"] = project
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, path, headers=headers, json=json)


_BASE = "/api/v1/admin/connections"


async def test_cross_project_get_404(conn_app):
    r = await _req(
        conn_app["app"],
        "GET",
        f"{_BASE}/{conn_app['conn_pb']}",
        token=conn_app["sess_member"],
        project=conn_app["pa"],
    )
    assert r.status_code == 404  # PB's connection is hidden from a PA member


async def test_cross_project_delete_404(conn_app):
    r = await _req(
        conn_app["app"],
        "DELETE",
        f"{_BASE}/{conn_app['conn_pb']}",
        token=conn_app["sess_member"],
        project=conn_app["pa"],
    )
    assert r.status_code == 404


async def test_cross_project_patch_404(conn_app):
    r = await _req(
        conn_app["app"],
        "PATCH",
        f"{_BASE}/{conn_app['conn_pb']}",
        token=conn_app["sess_member"],
        project=conn_app["pa"],
        json={"display_name": "hijacked"},
    )
    assert r.status_code == 404


async def test_cross_project_set_default_404(conn_app):
    r = await _req(
        conn_app["app"],
        "POST",
        f"{_BASE}/{conn_app['conn_pb']}/set-default",
        token=conn_app["sess_member"],
        project=conn_app["pa"],
    )
    assert r.status_code == 404


async def test_list_is_scoped_to_own_plus_global(conn_app):
    r = await _req(
        conn_app["app"], "GET", f"{_BASE}/", token=conn_app["sess_member"], project=conn_app["pa"]
    )
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert conn_app["conn_pa"] in ids  # own
    assert conn_app["conn_global"] in ids  # inherited org-shared
    assert conn_app["conn_pb"] not in ids  # never another project's


async def test_member_reads_global_but_cannot_delete_it(conn_app):
    # Org-shared (global) connection is readable via inheritance...
    r = await _req(
        conn_app["app"],
        "GET",
        f"{_BASE}/{conn_app['conn_global']}",
        token=conn_app["sess_member"],
        project=conn_app["pa"],
    )
    assert r.status_code == 200
    # ...but a project member may not mutate it (org-shared write = org admin).
    r2 = await _req(
        conn_app["app"],
        "DELETE",
        f"{_BASE}/{conn_app['conn_global']}",
        token=conn_app["sess_member"],
        project=conn_app["pa"],
    )
    assert r2.status_code == 403  # visible (not 404), but under-privileged


async def test_viewer_cannot_create_403(conn_app):
    r = await _req(
        conn_app["app"],
        "POST",
        f"{_BASE}/",
        token=conn_app["sess_viewer"],
        project=conn_app["pa"],
        json={"protocol": "http", "display_name": "v", "protocol_data": {"url": "https://e.com"}},
    )
    assert r.status_code == 403


async def test_forged_header_404(conn_app):
    # PA member forges X-Project-ID: PB -> middleware 404s before the route runs.
    r = await _req(
        conn_app["app"], "GET", f"{_BASE}/", token=conn_app["sess_member"], project=conn_app["pb"]
    )
    assert r.status_code == 404


async def test_export_pinned_to_session_project(conn_app):
    # Even with ?project_id=PB, a PA member's export only contains PA's connections.
    r = await _req(
        conn_app["app"],
        "GET",
        f"{_BASE}/export?project_id={conn_app['pb']}",
        token=conn_app["sess_member"],
        project=conn_app["pa"],
    )
    assert r.status_code == 200
    assert "pa-conn" in r.text
    assert "pb-conn" not in r.text


# ── Review round 1 (sagecurator) regressions ────────────────────────────


async def test_plugin_extra_route_cross_project_404(conn_app):
    # Plugin extra routes (mcp/oauth2/...) resolve a connection by id too; a PA
    # member hitting PB's MCP tools route must 404, not leak PB's cached tools.
    r = await _req(
        conn_app["app"],
        "GET",
        f"{_BASE}/mcp/{conn_app['conn_pb_mcp']}/tools",
        token=conn_app["sess_member"],
        project=conn_app["pa"],
    )
    assert r.status_code == 404


async def test_viewer_cannot_test_own_connection_403(conn_app):
    # /test mutates last_test_ok, so a read-only viewer must be forbidden even on
    # a connection in their own project.
    r = await _req(
        conn_app["app"],
        "POST",
        f"{_BASE}/{conn_app['conn_pa']}/test",
        token=conn_app["sess_viewer"],
        project=conn_app["pa"],
    )
    assert r.status_code == 403


async def test_member_cannot_test_global_connection_403(conn_app):
    # Testing an inherited org-shared connection mutates a global row -> a project
    # member is forbidden (org-shared mutation needs an org admin).
    r = await _req(
        conn_app["app"],
        "POST",
        f"{_BASE}/{conn_app['conn_global']}/test",
        token=conn_app["sess_member"],
        project=conn_app["pa"],
    )
    assert r.status_code == 403


async def test_viewer_cannot_export_encrypted_403(conn_app):
    # Encrypted export emits fernet: ciphertext (credential material); a read-only
    # viewer must not be able to request it.
    r = await _req(
        conn_app["app"],
        "GET",
        f"{_BASE}/export?secrets=encrypted",
        token=conn_app["sess_viewer"],
        project=conn_app["pa"],
    )
    assert r.status_code == 403
