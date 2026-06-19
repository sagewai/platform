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
from sagewai.admin.admin_resource_store import AdminResourceStore
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.provider_store import PostgresProviderStore
from sagewai.admin.state_file import AdminStateFile
from sagewai.admin.tenancy import RequestContext, UserRef
from sagewai.admin.tenant_audit import TenantAuditStore
from sagewai.connections.postgres_store import PostgresConnectionStore
from sagewai.connections.protocols import DEFAULT_KEY_FOR, PROTOCOLS
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
    # A member of the OTHER project (PB) — the cross-tenant actor for the
    # two-project isolation smoke; sees PB, must never see PA.
    member_b = await store.create_user(oid, "mb@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, member_b["id"], "project:member", project_id=pb)
    # An org owner — the only actor allowed to mutate org/project/token routes.
    owner = await store.create_user(oid, "o@acme.io", password="pw0000", role="org:owner")

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

    connections = PostgresConnectionStore(
        engine=engine,
        allowed_protocols=tuple(p.id for p in PROTOCOLS),
        default_key_for=DEFAULT_KEY_FOR,
    )
    await connections.init()
    conn_pa = await connections.create(
        protocol="http",
        project_id=pa,
        display_name="pa-http",
        tags=[],
        protocol_data={"url": "https://pa.example.com"},
    )
    conn_pb = await connections.create(
        protocol="http",
        project_id=pb,
        display_name="pb-http",
        tags=[],
        protocol_data={"url": "https://pb.example.com"},
    )
    conn_global_mcp = await connections.create(
        protocol="mcp",
        project_id=None,
        display_name="shared-mcp",
        tags=[],
        protocol_data={
            "server_ref": "shared",
            "transport": "http",
            "url": "https://mcp.example.com",
            "discovered_tools": [
                {"name": "shared_tool", "description": "", "input_schema": {}}
            ],
        },
    )
    conn_pa_mcp = await connections.create(
        protocol="mcp",
        project_id=pa,
        display_name="pa-mcp",
        tags=[],
        protocol_data={
            "server_ref": "pa",
            "transport": "http",
            "url": "https://pa-mcp.example.com",
            "discovered_tools": [
                {"name": "pa_tool", "description": "", "input_schema": {}}
            ],
        },
    )
    conn_pb_mcp = await connections.create(
        protocol="mcp",
        project_id=pb,
        display_name="pb-mcp",
        tags=[],
        protocol_data={
            "server_ref": "pb",
            "transport": "http",
            "url": "https://pb-mcp.example.com",
            "discovered_tools": [
                {"name": "pb_tool", "description": "", "input_schema": {}}
            ],
        },
    )

    # Run + prompt-log stores on the SAME engine; seed one of each in PA and PB so
    # the admin-route isolation assertions are meaningful (PB's genuinely exist).
    from sagewai.admin.store import RunStore
    from sagewai.observability.prompt_store import PromptStore

    runs = RunStore(engine=engine)
    await runs.init()
    run_a = await runs.save_run_for(_ctx(pa), agent_name="scout", input_text="ina")
    run_b = await runs.save_run_for(_ctx(pb), agent_name="scout", input_text="inb")
    # A run with a caller-supplied fixed id: proves save_run_for(run_id=X) persists
    # X (route emits an id before the run finishes, then resolves it by that id).
    run_fixed = await runs.save_run_for(_ctx(pa), run_id="run-fixed123", agent_name="scout")
    plogs = PromptStore(engine=engine)
    await plogs.init()
    log_a = await plogs.save_prompt_log_for(_ctx(pa), agent_name="scout", run_id=run_a)
    log_b = await plogs.save_prompt_log_for(_ctx(pb), agent_name="scout", run_id=run_b)
    # A PA example carrying recognisable text, for export/training regressions.
    example_a = await plogs.save_prompt_log_for(
        _ctx(pa),
        agent_name="scout",
        is_example=True,
        input_text="PA-EXAMPLE-INPUT",
        output_text="PA-EXAMPLE-OUTPUT",
    )

    sf.mutate(
        lambda d: d.setdefault("workflow_runs", []).extend(
            [
                {
                    "run_id": "wf-pa",
                    "workflow_name": "tenant-wf",
                    "status": "completed",
                    "project_id": pa,
                    "events": [
                        {
                            "event_type": "workflow_finished",
                            "data": {"output": "pa-output"},
                        }
                    ],
                },
                {
                    "run_id": "wf-pb",
                    "workflow_name": "tenant-wf",
                    "status": "running",
                    "project_id": pb,
                    "events": [
                        {
                            "event_type": "workflow_finished",
                            "data": {"output": "pb-output"},
                        }
                    ],
                },
            ]
        )
    )

    # Generic admin-resource store on the SAME engine; the durable backing for
    # budgets + guardrails (kind-keyed). Seed PB's budget + guardrail THROUGH the
    # store so cross-project isolation assertions are meaningful (they genuinely
    # exist in PB's scope).
    admin_resources = AdminResourceStore(engine=engine)
    await admin_resources.init()
    await admin_resources.upsert_for(
        _ctx(pb), "budget_limit", "pb-budget",
        {"agent_name": "pb-budget", "daily_limit_usd": 99, "project_id": pb},
        name="pb-budget",
    )
    await admin_resources.upsert_for(
        _ctx(pb), "guardrail_config", "pb-guardrail",
        {"agent_name": "pb-guardrail", "guardrails": [], "project_id": pb},
        name="pb-guardrail",
    )

    from sagewai.admin.api_token_store import ApiTokenStore
    from sagewai.admin.serve import create_admin_serve_app

    api_tokens = ApiTokenStore(engine=engine)
    await api_tokens.init()
    app = create_admin_serve_app(
        sf,
        identity_store=store,
        provider_store=pg,
        agent_store=agents,
        connection_store=connections,
        run_store=runs,
        prompt_log_store=plogs,
        admin_resource_store=admin_resources,
        api_token_store=api_tokens,
    )
    # Durable W8 audit fires on successful tenant mutations and fails the write
    # closed if it can't record. ASGITransport skips the lifespan, so bind the
    # audit store to the SAME test engine here; otherwise _emit_audit lazily
    # builds one against the process db and the chain append fails under test.
    audit = TenantAuditStore(engine=engine)
    await audit.init()
    app.state.tenant_audit = audit
    yield {
        "app": app,
        "identity_store": store,
        "org_id": oid,
        "sf": sf,
        "pa": pa,
        "pb": pb,
        "prov_b": prov_b["id"],
        "prov_a": prov_a["id"],
        "conn_pa": conn_pa.id,
        "conn_pb": conn_pb.id,
        "conn_global_mcp": conn_global_mcp.id,
        "conn_pa_mcp": conn_pa_mcp.id,
        "conn_pb_mcp": conn_pb_mcp.id,
        "run_a": run_a,
        "run_b": run_b,
        "run_fixed": run_fixed,
        "workflow_run_a": "wf-pa",
        "workflow_run_b": "wf-pb",
        "log_a": log_a,
        "log_b": log_b,
        "example_a": example_a,
        "sess_member": await store.issue_session(oid, member["id"]),
        "sess_viewer": await store.issue_session(oid, viewer["id"]),
        "sess_owner": await store.issue_session(oid, owner["id"]),
        "sess_member_b": await store.issue_session(oid, member_b["id"]),
        "member_b_id": member_b["id"],
        "api_token_store": api_tokens,
        "pa_slug": "pa",
    }
    await engine.dispose()


async def _req(app, method, path, *, token, project=None, json=None):
    headers = {"authorization": f"Bearer {token}"}
    if project:
        headers["x-project-id"] = project
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, path, headers=headers, json=json)


async def _post_yaml(app, path, *, token, project, content):
    """POST a raw YAML body (connection import accepts application/yaml)."""
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/yaml",
    }
    if project:
        headers["x-project-id"] = project
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request("POST", path, headers=headers, content=content)


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


async def test_multi_tenant_harness_uses_durable_store(real_app):
    from sagewai.harness.postgres_store import PostgresHarnessStore

    assert isinstance(real_app["app"].state.harness_store, PostgresHarnessStore)


# ── Connection isolation on the real admin routes ───────────────────


async def test_connection_list_uses_tenant_store_with_inheritance(real_app):
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/admin/connections/",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    names = {c.get("display_name") for c in r.json()}
    assert "pa-http" in names
    assert "shared-mcp" in names
    assert "pb-http" not in names


_IMPORT_HTTP_YAML = (
    "version: 1\n"
    "secrets_mode: redacted\n"
    "connections:\n"
    "  - protocol: http\n"
    "    display_name: imported-http\n"
    "    tags: [fresh]\n"
    "    protocol_data:\n"
    "      base_url: https://imported.example.com\n"
    "      auth:\n"
    "        kind: none\n"
)


async def test_connection_import_writes_to_tenant_store(real_app):
    """POST /import in multi mode persists through the async tenant store.

    (Was a hard 501 — the sync importer couldn't drive the AsyncEngine store.)
    """
    r = await _post_yaml(
        real_app["app"],
        "/api/v1/admin/connections/import",
        token=real_app["sess_member"],
        project=real_app["pa"],
        content=_IMPORT_HTTP_YAML,
    )
    assert r.status_code == 200, r.text
    assert [c["display_name"] for c in r.json()["created"]] == ["imported-http"]

    # Persisted in PA's scope, with the imported tags...
    pa = await _req(
        real_app["app"],
        "GET",
        "/api/v1/admin/connections/",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    pa_conns = {c["display_name"]: c for c in pa.json()}
    assert "imported-http" in pa_conns
    assert pa_conns["imported-http"]["project_id"] == real_app["pa"]
    assert pa_conns["imported-http"]["tags"] == ["fresh"]

    # ...and invisible to another project (owner scoped to PB does not see it).
    pb = await _req(
        real_app["app"],
        "GET",
        "/api/v1/admin/connections/",
        token=real_app["sess_owner"],
        project=real_app["pb"],
    )
    assert "imported-http" not in {c["display_name"] for c in pb.json()}


async def test_connection_import_upsert_updates_tenant_store(real_app):
    """upsert mode replays an update through the async tenant store."""
    yaml_text = (
        "version: 1\n"
        "secrets_mode: redacted\n"
        "connections:\n"
        "  - protocol: http\n"
        "    display_name: pa-http\n"  # already exists in PA
        "    tags: [upserted]\n"
        "    protocol_data:\n"
        "      base_url: https://pa-updated.example.com\n"
        "      auth:\n"
        "        kind: none\n"
    )
    r = await _post_yaml(
        real_app["app"],
        "/api/v1/admin/connections/import?mode=upsert",
        token=real_app["sess_member"],
        project=real_app["pa"],
        content=yaml_text,
    )
    assert r.status_code == 200, r.text
    assert [c["display_name"] for c in r.json()["updated"]] == ["pa-http"]

    # The real tenant row reflects the update (not just the importer's report).
    pa = await _req(
        real_app["app"],
        "GET",
        "/api/v1/admin/connections/",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    pa_http = next(c for c in pa.json() if c["display_name"] == "pa-http")
    assert pa_http["tags"] == ["upserted"]


async def test_connection_import_create_only_conflict_aborts(real_app):
    """create-only against an existing tenant name is all-or-nothing (400, no write)."""
    yaml_text = (
        "version: 1\n"
        "secrets_mode: redacted\n"
        "connections:\n"
        "  - protocol: http\n"
        "    display_name: pa-http\n"  # collides with the existing PA row
        "    tags: [should-not-land]\n"
        "    protocol_data:\n"
        "      base_url: https://dupe.example.com\n"
        "      auth:\n"
        "        kind: none\n"
    )
    r = await _post_yaml(
        real_app["app"],
        "/api/v1/admin/connections/import",
        token=real_app["sess_member"],
        project=real_app["pa"],
        content=yaml_text,
    )
    assert r.status_code == 400, r.text
    # The existing row is untouched (the conflicting tags never landed).
    pa = await _req(
        real_app["app"],
        "GET",
        "/api/v1/admin/connections/",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    pa_http = next(c for c in pa.json() if c["display_name"] == "pa-http")
    assert pa_http["tags"] == []


async def test_connection_import_dry_run_does_not_persist(real_app):
    """dry_run reports the would-be writes but persists nothing to the tenant store."""
    r = await _post_yaml(
        real_app["app"],
        "/api/v1/admin/connections/import?dry_run=true",
        token=real_app["sess_member"],
        project=real_app["pa"],
        content=_IMPORT_HTTP_YAML,
    )
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is True
    assert [c["display_name"] for c in r.json()["created"]] == ["imported-http"]

    pa = await _req(
        real_app["app"],
        "GET",
        "/api/v1/admin/connections/",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert "imported-http" not in {c["display_name"] for c in pa.json()}


async def test_mcp_extra_route_uses_tenant_connection_context(real_app):
    r = await _req(
        real_app["app"],
        "GET",
        f"/api/v1/admin/connections/mcp/{real_app['conn_pa_mcp']}/tools",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    assert {t["name"] for t in r.json()["tools"]} == {"pa_tool"}


async def test_capabilities_include_inherited_mcp_connections(real_app):
    r = await _req(
        real_app["app"],
        "GET",
        "/playground/capabilities",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    names = {c.get("name") for c in r.json()["mcp_servers"]}
    assert "pa-mcp" in names
    assert "shared-mcp" in names
    assert "pb-mcp" not in names


async def test_mcp_server_list_uses_tenant_connection_context(real_app):
    # Legacy /api/v1/mcp/servers must not read the process-global state-file
    # mcp_servers list in multi-tenant mode. It should project-filter through the
    # same tenant connection store as /playground/capabilities.
    real_app["sf"].mutate(
        lambda d: d.setdefault("mcp_servers", []).append(
            {"name": "state-file-leak", "path": "/tmp/leak", "status": "ready"}
        )
    )

    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/mcp/servers",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    names = {c.get("name") for c in r.json()}
    assert "pa-mcp" in names
    assert "shared-mcp" in names
    assert "pb-mcp" not in names
    assert "state-file-leak" not in names


# ── Run + prompt-log isolation on the real admin routes (PR2) ────────


async def test_runs_list_isolated(real_app):
    # A PA member's /admin/runs shows only PA's run, never PB's.
    r = await _req(
        real_app["app"],
        "GET",
        "/admin/runs",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    items = r.json()["items"]
    ids = {x.get("run_id") for x in items}
    assert real_app["run_a"] in ids
    assert real_app["run_b"] not in ids


async def test_run_detail_cross_project_404(real_app):
    r = await _req(
        real_app["app"],
        "GET",
        f"/admin/runs/{real_app['run_b']}",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 404


async def test_run_cancel_by_owner(real_app):
    # The owning member cancels their own run -> 200 + status flips to cancelled,
    # and a re-read of the run detail reflects the new status.
    r = await _req(
        real_app["app"],
        "POST",
        f"/admin/runs/{real_app['run_a']}/cancel",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    detail = await _req(
        real_app["app"],
        "GET",
        f"/admin/runs/{real_app['run_a']}",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "cancelled"


async def test_run_cancel_cross_project_404(real_app):
    # PA member cancels PB's run by id. run_b genuinely EXISTS in PB's scope,
    # so this 404 is isolation (not absence) and PB's run stays untouched.
    r = await _req(
        real_app["app"],
        "POST",
        f"/admin/runs/{real_app['run_b']}/cancel",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 404


async def test_run_cancel_unknown_id_404(real_app):
    r = await _req(
        real_app["app"],
        "POST",
        "/admin/runs/does-not-exist/cancel",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 404


async def test_workflow_history_list_isolated(real_app):
    r = await _req(
        real_app["app"],
        "GET",
        "/workflows/history",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    ids = {x.get("run_id") for x in r.json()}
    assert real_app["workflow_run_a"] in ids
    assert real_app["workflow_run_b"] not in ids


async def test_workflow_run_detail_cross_project_404(real_app):
    r = await _req(
        real_app["app"],
        "GET",
        f"/workflows/runs/{real_app['workflow_run_b']}",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 404


async def test_workflow_history_detail_cross_project_404(real_app):
    r = await _req(
        real_app["app"],
        "GET",
        f"/workflows/history/{real_app['workflow_run_b']}",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 404


async def test_workflow_events_cross_project_404(real_app):
    r = await _req(
        real_app["app"],
        "GET",
        f"/workflows/runs/{real_app['workflow_run_b']}/events",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 404


async def test_workflow_cancel_cross_project_404(real_app):
    r = await _req(
        real_app["app"],
        "POST",
        f"/workflows/runs/{real_app['workflow_run_b']}/cancel",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 404


async def test_workflow_approve_cross_project_404(real_app):
    r = await _req(
        real_app["app"],
        "POST",
        f"/workflows/runs/{real_app['workflow_run_b']}/approve",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 404


async def test_workflow_dispatch_no_longer_returns_fake_success(real_app):
    r = await _req(
        real_app["app"],
        "POST",
        "/workflows/dispatch",
        token=real_app["sess_member"],
        project=real_app["pa"],
        json={"workflow_name": "wf"},
    )
    assert r.status_code == 501


async def test_prompt_logs_list_isolated(real_app):
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/prompts/logs",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    body = r.json()
    # CursorPage<PromptLogSummary> envelope: { items, next_cursor, has_more }.
    if isinstance(body, dict):
        logs = body.get("items", body.get("logs", []))
    else:
        logs = body
    ids = {x.get("log_id") for x in logs}
    assert real_app["log_a"] in ids
    assert real_app["log_b"] not in ids


async def test_prompt_log_cross_project_get_404(real_app):
    r = await _req(
        real_app["app"],
        "GET",
        f"/api/v1/prompts/logs/{real_app['log_b']}",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 404


async def test_prompt_log_cross_project_delete_404(real_app):
    r = await _req(
        real_app["app"],
        "DELETE",
        f"/api/v1/prompts/logs/{real_app['log_b']}",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 404


# ── PR2 P1 review regressions ────────────────────────────────────────


async def test_run_with_fixed_id_resolvable(real_app):
    # P1 #1: a run saved via save_run_for(run_id="run-fixed123") is retrievable
    # at /admin/runs/run-fixed123 by the owning member (no uuid mismatch).
    r = await _req(
        real_app["app"],
        "GET",
        f"/admin/runs/{real_app['run_fixed']}",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    assert r.json().get("run_id") == "run-fixed123"


async def test_prompt_log_create_cross_tenant_run_ref_404(real_app):
    # P1 #2: a PA member cannot create a PA log that references PB's run id.
    r = await _req(
        real_app["app"],
        "POST",
        "/api/v1/prompts/logs",
        token=real_app["sess_member"],
        project=real_app["pa"],
        json={"agent_name": "scout", "run_id": real_app["run_b"]},
    )
    assert r.status_code == 404


async def test_prompt_log_create_own_run_ref_201(real_app):
    # P1 #2: referencing the caller's OWN run id succeeds.
    r = await _req(
        real_app["app"],
        "POST",
        "/api/v1/prompts/logs",
        token=real_app["sess_member"],
        project=real_app["pa"],
        json={"agent_name": "scout", "run_id": real_app["run_a"]},
    )
    assert r.status_code == 201


async def test_prompts_export_isolated_through_pg(real_app):
    # P1 #3: /prompts/export goes through the PG store; PA's logs are present,
    # PB's are excluded.
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/prompts/export",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    ids = {log.get("log_id") for log in r.json()}
    assert real_app["log_a"] in ids
    assert real_app["example_a"] in ids
    assert real_app["log_b"] not in ids


async def test_training_stats_counts_pg_examples(real_app):
    # P1 #3: /training/stats reads the PG store (the seeded PA example counts).
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/training/stats",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    assert r.json().get("total_samples", 0) >= 1


async def test_training_export_contains_pg_sample(real_app):
    # P1 #3: /training/export (PG store) is non-empty and contains the PA sample.
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/training/export?format=raw",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    assert r.text.strip() != ""
    assert "PA-EXAMPLE-INPUT" in r.text


async def test_quality_route_cross_project_404(real_app):
    # P1 #3: rating a cross-project log id -> 404 (existence-hidden).
    r = await _req(
        real_app["app"],
        "POST",
        f"/api/v1/training/samples/{real_app['log_b']}/quality",
        token=real_app["sess_member"],
        project=real_app["pa"],
        json={"quality": 5},
    )
    assert r.status_code == 404


async def test_quality_route_viewer_403(real_app):
    # P1 #3: a project:viewer cannot rate a sample -> RBAC 403 (on an in-scope log).
    r = await _req(
        real_app["app"],
        "POST",
        f"/api/v1/training/samples/{real_app['example_a']}/quality",
        token=real_app["sess_viewer"],
        project=real_app["pa"],
        json={"quality": 5},
    )
    assert r.status_code == 403


async def test_quality_route_member_persists(real_app):
    # P1 #3: a member rating an in-scope example succeeds and the quality is
    # persisted (read back via the export route's to_dict()).
    r = await _req(
        real_app["app"],
        "POST",
        f"/api/v1/training/samples/{real_app['example_a']}/quality",
        token=real_app["sess_member"],
        project=real_app["pa"],
        json={"quality": 5},
    )
    assert r.status_code == 200
    assert r.json().get("quality") == 5
    r2 = await _req(
        real_app["app"],
        "GET",
        "/api/v1/prompts/export",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    rec = next(log for log in r2.json() if log.get("log_id") == real_app["example_a"])
    assert rec.get("quality") == 5


# ── Org-level routes: org-admin gate (privesc fixes) ─────────────────────
# A plain project member must not mutate org settings, projects, or API
# tokens; an org owner can. require_org_admin no-ops in single-org.


async def test_org_patch_member_403(real_app):
    r = await _req(
        real_app["app"], "PATCH", "/api/v1/organization",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"name": "Hijacked"},
    )
    assert r.status_code == 403


async def test_org_patch_owner_ok(real_app):
    r = await _req(
        real_app["app"], "PATCH", "/api/v1/organization",
        token=real_app["sess_owner"], json={"name": "Renamed"},
    )
    assert r.status_code == 200


async def test_auth_me_returns_tenant_actor_not_file_admin(real_app):
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/auth/me",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "m@acme.io"
    assert body["role"] == "member"
    assert body["email"] != "a@acme.io"


async def test_project_create_member_403(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/projects",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"name": "Sneaky", "slug": "sneaky"},
    )
    assert r.status_code == 403


async def test_project_update_member_403(real_app):
    r = await _req(
        real_app["app"], "PATCH", f"/api/v1/projects/{real_app['pa_slug']}",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"name": "Renamed"},
    )
    assert r.status_code == 403


async def test_project_delete_member_403(real_app):
    r = await _req(
        real_app["app"], "DELETE", f"/api/v1/projects/{real_app['pa_slug']}",
        token=real_app["sess_member"], project=real_app["pa"],
    )
    assert r.status_code == 403


async def test_project_create_owner_uses_identity_store(real_app):
    r = await _req(
        real_app["app"],
        "POST",
        "/api/v1/projects",
        token=real_app["sess_owner"],
        json={"name": "Created", "slug": "created"},
    )
    assert r.status_code == 201
    created = r.json()
    identity_project = await real_app["identity_store"].get_project_by_slug(
        real_app["org_id"], "created"
    )
    assert identity_project is not None
    assert identity_project["id"] == created["id"]

    # The created project is immediately usable as the auth project id. The old
    # file-state split-brain returned 201 but then 404'd when selected.
    selected = await _req(
        real_app["app"],
        "GET",
        "/api/v1/providers",
        token=real_app["sess_owner"],
        project=created["id"],
    )
    assert selected.status_code == 200


async def test_project_delete_owner_409_until_cascade_exists(real_app):
    r = await _req(
        real_app["app"],
        "DELETE",
        f"/api/v1/projects/{real_app['pa_slug']}",
        token=real_app["sess_owner"],
    )
    assert r.status_code == 409


async def test_token_create_member_403(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/tokens/",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"name": "evil", "scopes": ["admin"]},
    )
    assert r.status_code == 403


async def test_token_create_owner_mints_org_shared_token(real_app):
    # The org owner (no X-Project-ID -> org scope) mints an org-shared CI token.
    r = await _req(
        real_app["app"], "POST", "/api/v1/tokens/",
        token=real_app["sess_owner"], json={"name": "ci", "scopes": ["read"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token"].startswith("swt_")     # plaintext returned ONCE
    assert body["project_id"] is None           # org-shared scope (NOT all-projects)
    # The list redacts: never the plaintext or the hash.
    listed = await _req(
        real_app["app"], "GET", "/api/v1/tokens/", token=real_app["sess_owner"]
    )
    assert listed.status_code == 200, listed.text
    blob = str(listed.json())
    assert body["token"] not in blob and "token_hash" not in blob


async def test_legacy_audit_events_project_member_403(real_app):
    real_app["sf"].mutate(
        lambda d: d.setdefault("audit_events", []).append(
            {"event_type": "token.created", "actor": "owner", "target": "tok-secret"}
        )
    )
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/audit/events",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 403


async def test_sandbox_defaults_cross_project_read_404(real_app):
    real_app["sf"].create_project(name="PA legacy", slug="pa")
    real_app["sf"].create_project(name="PB legacy", slug="pb")

    def _seed(d):
        for p in d.get("projects", []):
            if p.get("slug") == "pb":
                p["default_sandbox_requirements"] = {
                    "sandbox_mode": "per_run",
                    "image": "private/pb-runtime:latest",
                    "network_policy": "egress_allowlist",
                    "required_secret_scopes": ["pb-secret-scope"],
                }

    real_app["sf"].mutate(_seed)
    r = await _req(
        real_app["app"],
        "GET",
        "/api/v1/admin/projects/pb/sandbox-defaults",
        token=real_app["sess_member"],
        project=real_app["pa"],
    )
    assert r.status_code == 404


async def test_missing_prompt_store_fails_closed_in_multi(real_app):
    from sagewai.admin.serve import create_admin_serve_app

    real_app["sf"].mutate(
        lambda d: d.setdefault("prompt_logs", []).append(
            {
                "log_id": "pb-file-log",
                "project_id": real_app["pb"],
                "agent_name": "scout",
                "is_example": False,
            }
        )
    )
    app = create_admin_serve_app(
        real_app["sf"],
        identity_store=real_app["identity_store"],
    )
    r = await _req(
        app,
        "POST",
        "/api/v1/training/samples/pb-file-log/quality",
        token=real_app["sess_member"],
        project=real_app["pa"],
        json={"quality": 5},
    )
    assert r.status_code == 503


async def test_fleet_register_uses_authenticated_org_not_body_org(real_app):
    r = await _req(
        real_app["app"],
        "POST",
        "/api/v1/fleet/register",
        token=real_app["sess_member"],
        project=real_app["pa"],
        json={"name": "tenant-worker", "org_id": "forged-org", "models": ["gpt-4o"]},
    )
    assert r.status_code == 201
    listed = await _req(
        real_app["app"],
        "GET",
        "/api/v1/fleet/workers",
        token=real_app["sess_owner"],
    )
    assert listed.status_code == 200
    assert "tenant-worker" in {w.get("name") for w in listed.json()["workers"]}


# ── Project-scoped file-backed routes + remaining org-admin stubs ─────────


async def test_workflow_registry_create_member_scoped_ok(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/workflow-registry",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"name": "wf", "yaml_content": "x: 1"},
    )
    assert r.status_code == 201
    assert r.json()["project_id"] == real_app["pa"]

    listed = await _req(
        real_app["app"], "GET", "/api/v1/workflow-registry",
        token=real_app["sess_member"], project=real_app["pa"],
    )
    assert listed.status_code == 200
    assert {wf["name"] for wf in listed.json()["items"]} == {"wf"}


async def test_budget_limit_create_member_scoped_ok(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/budget/limits",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"agent_name": "scout", "daily_limit_usd": 5, "project_id": real_app["pb"]},
    )
    assert r.status_code == 201
    assert r.json()["project_id"] == real_app["pa"]

    listed = await _req(
        real_app["app"], "GET", "/api/v1/budget/limits",
        token=real_app["sess_member"], project=real_app["pa"],
    )
    assert listed.status_code == 200
    assert {row["agent_name"] for row in listed.json()} == {"scout"}


async def test_guardrail_config_member_scoped_ok(real_app):
    r = await _req(
        real_app["app"], "PUT", "/api/v1/guardrails/configs/scout",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"guardrails": [], "project_id": real_app["pb"]},
    )
    assert r.status_code == 200
    assert r.json()["project_id"] == real_app["pa"]


async def test_eval_dataset_create_member_scoped_ok(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/eval/datasets",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"name": "ds", "project_id": real_app["pb"]},
    )
    assert r.status_code == 201
    assert r.json()["project_id"] == real_app["pa"]


async def test_notification_channel_member_scoped_and_redacted(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/notifications/channels",
        token=real_app["sess_member"], project=real_app["pa"],
        json={
            "channel_type": "slack",
            "webhook_url": "https://hooks.slack.com/services/pa",
            "project_id": real_app["pb"],
        },
    )
    assert r.status_code == 201
    assert r.json()["project_id"] == real_app["pa"]
    assert r.json()["webhook_url"] == "***"
    assert "hooks.slack.com/services/pa" not in r.text

    listed = await _req(
        real_app["app"], "GET", "/api/v1/notifications/channels",
        token=real_app["sess_member"], project=real_app["pa"],
    )
    assert listed.status_code == 200
    assert "hooks.slack.com/services/pa" not in listed.text
    assert {row["channel_type"] for row in listed.json()} == {"slack"}


async def test_notification_trigger_member_scoped_ok(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/notifications/triggers",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"event_type": "budget.warning", "channel_id": "ch-pa", "project_id": real_app["pb"]},
    )
    assert r.status_code == 201
    assert r.json()["project_id"] == real_app["pa"]


async def test_trigger_create_member_scoped_ok(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/triggers",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"name": "t", "project_id": real_app["pb"]},
    )
    assert r.status_code == 201
    assert r.json()["project_id"] == real_app["pa"]


async def test_notification_test_ignores_body_supplied_webhook_in_multi_tenant(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/notifications/test",
        token=real_app["sess_member"], project=real_app["pa"],
        json={
            "channel_type": "slack",
            "webhook_url": "https://hooks.slack.com/services/body-injected",
        },
    )
    assert r.status_code == 200
    assert r.json()["sent"] is False


async def test_file_backed_project_resources_do_not_list_cross_project_rows(real_app):
    # NB: budget + guardrail rows are durable now (seeded through the
    # AdminResourceStore in the fixture as pb-budget / pb-guardrail), so they're
    # checked below against the durable store, not these file-store seeds.
    def _seed(d):
        d.setdefault("eval_datasets", []).append(
            {"id": "ds-pb", "name": "pb-dataset", "project_id": real_app["pb"]}
        )
        d.setdefault("triggers", []).append(
            {"id": "trig-pb", "name": "pb-trigger", "project_id": real_app["pb"]}
        )
        d.setdefault("notification_channels", []).append(
            {
                "id": "ch-pb",
                "channel_type": "slack",
                "webhook_url": "https://hooks.slack.com/services/pb",
                "project_id": real_app["pb"],
            }
        )
        d.setdefault("notification_triggers", []).append(
            {
                "id": "notif-tr-pb",
                "event_type": "pb-event",
                "project_id": real_app["pb"],
            }
        )

    real_app["sf"].mutate(_seed)

    checks = [
        ("/api/v1/budget/limits", "agent_name", "pb-budget"),
        ("/api/v1/guardrails/configs", "agent_name", "pb-guardrail"),
        ("/api/v1/eval/datasets", "name", "pb-dataset"),
        ("/api/v1/triggers", "name", "pb-trigger"),
        ("/api/v1/notifications/channels", "id", "ch-pb"),
        ("/api/v1/notifications/triggers", "id", "notif-tr-pb"),
    ]
    for path, key, forbidden in checks:
        r = await _req(
            real_app["app"], "GET", path,
            token=real_app["sess_member"], project=real_app["pa"],
        )
        assert r.status_code == 200
        assert forbidden not in {row.get(key) for row in r.json()}


async def test_memory_vector_ingest_member_allowed(real_app):
    # Project memory is now a real tenant feature: a project MEMBER may write to
    # its own project's memory (previously org-admin-gated). The write gate passes;
    # this fixture doesn't wire the engine resolver, so the ingest is a 200 no-op.
    r = await _req(
        real_app["app"], "POST", "/api/v1/memory/vector/ingest",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"content": "member-owned memory"},
    )
    assert r.status_code == 200


async def test_memory_vector_ingest_viewer_403(real_app):
    # A project VIEWER stays read-only — the write perimeter denies the ingest.
    r = await _req(
        real_app["app"], "POST", "/api/v1/memory/vector/ingest",
        token=real_app["sess_viewer"], project=real_app["pa"],
        json={"content": "viewer should be denied"},
    )
    assert r.status_code == 403


async def test_workflow_registry_create_owner_ok(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/workflow-registry",
        token=real_app["sess_owner"], json={"name": "wf", "yaml_content": "x: 1"},
    )
    assert r.status_code == 201


# ── Artifact destinations: project-scoped admin overrides ────────────────


async def test_artifact_destination_put_member_scoped_ok(real_app):
    r = await _req(
        real_app["app"], "PUT",
        "/api/v1/admin/workflows/wf1/artifact_destination",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"type": "local", "target": "/tmp/x"},
    )
    assert r.status_code == 200

    get = await _req(
        real_app["app"], "GET",
        "/api/v1/admin/workflows/wf1/artifact_destination",
        token=real_app["sess_member"], project=real_app["pa"],
    )
    assert get.status_code == 200
    assert get.json()["target"] == "/tmp/x"

    # Durable AdminResourceStore (MT) now backs artifact destinations, so the
    # destination is project-scoped at the DB layer rather than in the file
    # state. Prove the scoping holds via the API: a *different* project must not
    # see PA's destination.
    other = await _req(
        real_app["app"], "GET",
        "/api/v1/admin/workflows/wf1/artifact_destination",
        token=real_app["sess_owner"], project=real_app["pb"],
    )
    assert other.status_code == 404


async def test_artifact_destination_delete_member_scoped_ok(real_app):
    put = await _req(
        real_app["app"], "PUT",
        "/api/v1/admin/workflows/wf1/artifact_destination",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"type": "local", "target": "/tmp/x"},
    )
    assert put.status_code == 200

    r = await _req(
        real_app["app"], "DELETE",
        "/api/v1/admin/workflows/wf1/artifact_destination",
        token=real_app["sess_member"], project=real_app["pa"],
    )
    assert r.status_code == 204


async def test_artifact_destination_put_owner_ok(real_app):
    r = await _req(
        real_app["app"], "PUT",
        "/api/v1/admin/workflows/wf1/artifact_destination",
        token=real_app["sess_owner"],
        json={"type": "local", "target": "/tmp/x"},
    )
    assert r.status_code == 200


# ── api.py admin router: agent-config mutation gate (FLAGGED) ─────────────
# The in-memory registry isn't project-tagged; the config mutation is gated
# org-admin. serve.py mounts the router without a registry, so the owner
# passes the gate then hits "No registry configured" (404) — proving the gate
# precedes the handler and the member is blocked at the gate (403).


async def test_agent_config_patch_member_403(real_app):
    r = await _req(
        real_app["app"], "PATCH", "/admin/agents/scout/config",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"model": "evil"},
    )
    assert r.status_code == 403


async def test_agent_config_patch_owner_passes_gate(real_app):
    # Owner clears the org-admin gate; 404 is from the no-registry handler,
    # not the gate (proves the gate is org-admin, not a blanket block).
    r = await _req(
        real_app["app"], "PATCH", "/admin/agents/scout/config",
        token=real_app["sess_owner"], json={"model": "ok"},
    )
    assert r.status_code == 404


# ── Two-project isolation smoke (end-to-end capstone) ────────────────
#
# A single, readable walk of the cross-tenant boundary on the REAL multi-tenant
# admin app: for each control-plane resource, project A's member sees its own
# and a project B member gets 404/empty; and a B-bound API token can neither
# read A's resources nor be widened to A via a forged X-Project-ID. This is the
# runnable form of the pre-launch "manual two-project isolation smoke".
#
# Backed by SQLite — the multi-mode isolation logic is byte-identical to
# Postgres (the dual-dialect store tests prove parity), so it runs anywhere
# without infra and is unaffected by the Actions-billing block. Durable
# cross-tenant *memory* isolation (B's vector store cannot read A's docs) is
# proven separately in test_mt_memory_isolation.py — real_app intentionally does
# not wire the memory engine resolver, so this smoke checks only the memory
# write perimeter.


async def _mint_token(real_app, *, user_id, scopes, project_id):
    """Mint a project-bound API token via the store (ctx stamps org/subject)."""
    ctx = await real_app["identity_store"].build_context(
        real_app["org_id"], user_id, project_id=project_id
    )
    _, plaintext = await real_app["api_token_store"].create_for(
        ctx, name="smoke", scopes=set(scopes), project_id=project_id
    )
    return plaintext


async def test_two_project_isolation_smoke(real_app):
    app, pa, pb = real_app["app"], real_app["pa"], real_app["pb"]
    a, b = real_app["sess_member"], real_app["sess_member_b"]  # PA member, PB member

    # 1. CREDENTIAL (provider). A owns "anthropic" (id prov_a, secret seeded); B owns "openai".
    a_provs = await _req(app, "GET", "/api/v1/providers", token=a, project=pa)
    assert a_provs.status_code == 200
    assert "anthropic" in {p.get("provider_name") for p in a_provs.json()}  # A sees its own
    assert "sk-SECRET-PA" not in a_provs.text and "fernet:" not in a_provs.text  # redacted
    b_provs = await _req(app, "GET", "/api/v1/providers", token=b, project=pb)
    assert b_provs.status_code == 200
    assert real_app["prov_a"] not in {p.get("id") for p in b_provs.json()}  # A's row absent for B

    # 2. AGENT. Both own "scout"; each sees only its own (A model "x", B model "y").
    a_scout = await _req(app, "GET", "/playground/agents/scout", token=a, project=pa)
    assert a_scout.status_code == 200 and a_scout.json().get("model") == "x"
    b_scout = await _req(app, "GET", "/playground/agents/scout", token=b, project=pb)
    assert b_scout.status_code == 200 and b_scout.json().get("model") == "y"  # never A's "x"

    # 3. CONNECTION. A owns pa-http; B owns pb-http. Lists are scoped; a cross GET 404s.
    a_names = {
        c["display_name"]
        for c in (await _req(app, "GET", "/api/v1/admin/connections/", token=a, project=pa)).json()
    }
    assert "pa-http" in a_names and "pb-http" not in a_names
    b_names = {
        c["display_name"]
        for c in (await _req(app, "GET", "/api/v1/admin/connections/", token=b, project=pb)).json()
    }
    assert "pb-http" in b_names and "pa-http" not in b_names
    b_get_conn = await _req(
        app, "GET", f"/api/v1/admin/connections/{real_app['conn_pa']}", token=b, project=pb
    )
    assert b_get_conn.status_code == 404

    # 4. RUN (agent run). A owns run_a.
    assert (
        await _req(app, "GET", f"/admin/runs/{real_app['run_a']}", token=a, project=pa)
    ).status_code == 200
    assert (
        await _req(app, "GET", f"/admin/runs/{real_app['run_a']}", token=b, project=pb)
    ).status_code == 404
    b_runs = await _req(app, "GET", "/admin/runs", token=b, project=pb)
    assert real_app["run_a"] not in {r.get("run_id") for r in b_runs.json()["items"]}

    # 5. WORKFLOW run. A owns wf-pa (project_id=pa); B owns wf-pb.
    a_wf = {r.get("run_id") for r in (await _req(app, "GET", "/workflows/history", token=a, project=pa)).json()}
    assert "wf-pa" in a_wf and "wf-pb" not in a_wf
    b_wf = {r.get("run_id") for r in (await _req(app, "GET", "/workflows/history", token=b, project=pb)).json()}
    assert "wf-pb" in b_wf and "wf-pa" not in b_wf

    # 6. MEMORY write perimeter (durable isolation: see test_mt_memory_isolation).
    assert (
        await _req(app, "POST", "/api/v1/memory/vector/ingest", token=a, project=pa,
                   json={"content": "A-owned memory"})
    ).status_code == 200  # a member may write its own project's memory
    assert (
        await _req(app, "POST", "/api/v1/memory/vector/ingest", token=b, project=pa,
                   json={"content": "B forging into A"})
    ).status_code == 404  # B is not a member of PA — middleware 404s the forge

    # 7. SESSION-based switch: a PA member forging X-Project-ID: PB is 404'd at the
    #    middleware (member of PA, not PB).
    assert (
        await _req(app, "GET", "/api/v1/providers", token=a, project=pb)
    ).status_code == 404

    # 8. TOKEN-based switch: a B-bound read token is pinned to PB by its token row;
    #    a forged X-Project-ID: PA is ignored, so it cannot be widened to A.
    b_token = await _mint_token(
        real_app, user_id=real_app["member_b_id"], scopes={"read"}, project_id=pb
    )
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        own = await c.get("/api/v1/providers", headers={"authorization": f"Bearer {b_token}"})
        assert own.status_code == 200
        assert real_app["prov_a"] not in {p.get("id") for p in own.json()}
        forged = await c.get(
            "/api/v1/providers",
            headers={"authorization": f"Bearer {b_token}", "x-project-id": pa},
        )
        assert forged.status_code == 200
        assert real_app["prov_a"] not in {p.get("id") for p in forged.json()}
