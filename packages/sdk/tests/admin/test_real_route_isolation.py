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
seeded IdentityStore **and an injected, PG-backed provider store**, then drives
the actual provider routes — proving the seam composes on real endpoints (not
just the primitives).

The provider store is injected explicitly because ``httpx.ASGITransport`` does
not run the app lifespan, so the lifespan auto-build of the resource stores never
fires under test. Seeding through the injected ``PostgresProviderStore`` (not the
file store ``sf``) is what makes the isolation assertions meaningful: PB's
provider genuinely EXISTS in PB's scope, so a PA actor getting a 404 on it is
isolation (not absence), and PA's list excluding PB's provider is a real scope
boundary. A secret seeded on PA's provider lets us assert it never leaves the
store in cleartext on the real read route.
"""

import httpx
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport

from sagewai.admin import tenant_keys
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.provider_store import PostgresProviderStore
from sagewai.admin.state_file import AdminStateFile
from sagewai.admin.tenancy import RequestContext, UserRef
from sagewai.admin.tenant_audit import TenantAuditStore
from sagewai.db.engine import create_engine


@pytest_asyncio.fixture
async def real_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))  # isolate home (no real master.key)

    # Pin ONE deterministic org master key for the whole fixture so secret
    # encryption (seed) and any decryption use the same key.
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

    # PG provider store on the SAME engine (identity + provider tables share one
    # sqlite db) so per-project data keys minted by the identity store are
    # readable when the provider store encrypts/decrypts.
    pg = PostgresProviderStore(engine=engine, identity_store=store)
    await pg.init()

    def _ctx(project_id):
        return RequestContext(
            actor=UserRef("seed", "seed"),
            org_id=oid,
            project_id=project_id,
            roles=frozenset({"project:admin"}),
            scopes=frozenset({"read", "write", "admin"}),
            request_id="seed",
            tenancy_mode="multi",
        )

    # Seed THROUGH the PG store so the rows live in the PG scope the real routes
    # read. PB owns "openai" (no secret); PA owns "anthropic" with a secret.
    prov_b = await pg.upsert({"provider_name": "openai", "config": {}}, ctx=_ctx(pb))
    prov_a = await pg.upsert(
        {"provider_name": "anthropic", "config": {"api_key": "sk-SECRET-PA"}},
        ctx=_ctx(pa),
    )

    # PG agent store on the SAME engine. Seed an agent named "scout" in BOTH
    # PA and PB so the isolation assertions are meaningful: PB's "scout"
    # genuinely EXISTS in PB's scope, so a PA actor only ever seeing its own
    # "scout" (model "x", not "y") is a real scope boundary, not absence.
    from sagewai.admin.tenant_agent_store import PostgresTenantAgentStore

    agents = PostgresTenantAgentStore(engine=engine)
    await agents.init()
    await agents.create({"name": "scout", "model": "x"}, ctx=_ctx(pa))
    await agents.create({"name": "scout", "model": "y"}, ctx=_ctx(pb))

    from sagewai.admin.serve import create_admin_serve_app

    app = create_admin_serve_app(sf, identity_store=store, provider_store=pg, agent_store=agents)
    # Durable W8 audit fires on successful tenant mutations and fails the write
    # closed if it can't record. ASGITransport skips the lifespan, so bind the
    # audit store to the SAME test engine here; otherwise _emit_audit lazily
    # builds one against the process db and the chain append fails under test.
    audit = TenantAuditStore(engine=engine)
    await audit.init()
    app.state.tenant_audit = audit
    yield {
        "app": app,
        "pa": pa,
        "pb": pb,
        "prov_b": prov_b["id"],
        "prov_a": prov_a["id"],
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
    # Member of PA (scoped to PA) deletes PB's provider by id. prov_b genuinely
    # EXISTS in PB's PG scope, so this 404 is isolation (not absence).
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
    # A member lists providers in their own project: 200, and the body shows
    # PA's own "anthropic" but NOT PB's "openai" (cross-project invisible).
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/providers",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    names = {p.get("provider_name") for p in r.json()}
    assert "anthropic" in names
    assert "openai" not in names


async def test_provider_secret_never_in_response(real_app):
    # PA's provider carries a secret; the real read route must redact it — the
    # raw secret and the storage marker must never appear in the response body.
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/providers",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    assert "sk-SECRET-PA" not in r.text
    assert "fernet:" not in r.text


async def test_cross_project_provider_invisible_in_list(real_app):
    # Explicit cross-project invisibility: PB's provider id never appears in
    # PA's list, even though it exists in PB's scope.
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/providers",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    ids = {p.get("id") for p in r.json()}
    assert real_app["prov_b"] not in ids


# ── Agent isolation on the real playground-agent routes ──────────────


async def test_agent_forged_header_404(real_app):
    # Member of PA forges X-Project-ID: PB -> middleware 404s before the route runs.
    r = await _req(
        real_app["app"],
        "GET",
        "/playground/agents",
        token=real_app["sess_member"],
        project=real_app["pb"],
    )
    assert r.status_code == 404


async def test_agent_cross_project_get_404(real_app):
    # Both projects own a "scout"; PA's member sees ITS OWN (model "x"), never
    # PB's (model "y"). The shared name proves isolation, not absence.
    r = await _req(
        real_app["app"],
        "GET",
        "/playground/agents/scout",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    assert r.json().get("model") == "x"


async def test_agent_list_isolated(real_app):
    # PA's list contains exactly one "scout" and it is PA's (model "x").
    r = await _req(
        real_app["app"],
        "GET",
        "/playground/agents",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    scouts = [a for a in r.json() if a.get("name") == "scout"]
    assert len(scouts) == 1
    assert scouts[0].get("model") == "x"


async def test_agent_viewer_cannot_create_403(real_app):
    # A project:viewer (read-only) cannot create an agent -> route RBAC 403.
    r = await _req(
        real_app["app"],
        "POST",
        "/playground/agent",
        token=real_app["sess_viewer"],
        project=real_app["pa"],
        json={"name": "evil", "model": "z"},
    )
    assert r.status_code == 403


async def test_agent_delete_isolation(real_app):
    # PA deletes ITS OWN "scout" -> 200; a follow-up PA GET of scout is 404.
    # PB's "scout" is untouched (it lives in PB's scope, never matched here).
    r = await _req(
        real_app["app"],
        "DELETE",
        "/playground/agents/scout",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    r2 = await _req(
        real_app["app"],
        "GET",
        "/playground/agents/scout",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r2.status_code == 404


# ── Non-CRUD read/execution paths must use the active tenant store ───────
# (regressions for the split-brain where CRUD wrote to Postgres but test /
# debug / model-discovery still resolved from the empty file store)


async def test_provider_test_route_uses_pg_store(real_app, monkeypatch):
    # POST /providers/{id}/test must resolve the provider from the PG store, not
    # the (empty) file store — otherwise a PG-created provider 404s on test.
    import sagewai.admin.provider_probes as probes

    async def _fake_test(name, config):
        return {"connected": False, "latency_ms": 0}

    monkeypatch.setattr(probes, "test_cloud_provider", _fake_test)
    r = await _req(
        real_app["app"],
        "POST",
        f"/api/v1/providers/{real_app['prov_a']}/test",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200  # provider found via the PG store (not 404)


async def test_agent_debug_route_uses_pg_store(real_app):
    # /playground/agents/{name}/debug is an execution-adjacent read; it must
    # resolve the agent through the ctx-scoped tenant store (RFC §4), returning
    # PA's own "scout" (model "x"), not 404 from the empty file store.
    r = await _req(
        real_app["app"],
        "GET",
        "/playground/agents/scout/debug",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    assert r.json().get("model") == "x"
