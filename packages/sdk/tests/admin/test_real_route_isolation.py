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

    from sagewai.admin.serve import create_admin_serve_app

    app = create_admin_serve_app(
        sf,
        identity_store=store,
        provider_store=pg,
        agent_store=agents,
        run_store=runs,
        prompt_log_store=plogs,
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
        "pa": pa,
        "pb": pb,
        "prov_b": prov_b["id"],
        "prov_a": prov_a["id"],
        "run_a": run_a,
        "run_b": run_b,
        "run_fixed": run_fixed,
        "log_a": log_a,
        "log_b": log_b,
        "example_a": example_a,
        "sess_member": await store.issue_session(oid, member["id"]),
        "sess_viewer": await store.issue_session(oid, viewer["id"]),
        "sess_owner": await store.issue_session(oid, owner["id"]),
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
    logs = body["logs"] if isinstance(body, dict) and "logs" in body else body
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


async def test_token_create_member_403(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/tokens/",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"name": "evil", "scopes": ["admin"]},
    )
    assert r.status_code == 403


async def test_token_create_owner_ok(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/tokens/",
        token=real_app["sess_owner"], json={"name": "ci", "scopes": ["read"]},
    )
    assert r.status_code == 201


# ── Project-scoped routes whose store lacks a project_id column ───────────
# These are gated org-admin as a safe interim (FLAGGED: need a project column):
# saved_workflows, budget_limits, guardrail_configs, eval_datasets,
# notification channels/triggers, triggers, memory write stubs.


async def test_workflow_registry_create_member_403(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/workflow-registry",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"name": "wf", "yaml_content": "x: 1"},
    )
    assert r.status_code == 403


async def test_budget_limit_create_member_403(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/budget/limits",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"agent_name": "scout", "daily_limit_usd": 5},
    )
    assert r.status_code == 403


async def test_guardrail_config_member_403(real_app):
    r = await _req(
        real_app["app"], "PUT", "/api/v1/guardrails/configs/scout",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"guardrails": []},
    )
    assert r.status_code == 403


async def test_eval_dataset_create_member_403(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/eval/datasets",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"name": "ds"},
    )
    assert r.status_code == 403


async def test_notification_channel_member_403(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/notifications/channels",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"type": "slack"},
    )
    assert r.status_code == 403


async def test_trigger_create_member_403(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/triggers",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"name": "t"},
    )
    assert r.status_code == 403


async def test_memory_vector_ingest_member_403(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/memory/vector/ingest",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"documents": []},
    )
    assert r.status_code == 403


async def test_workflow_registry_create_owner_ok(real_app):
    r = await _req(
        real_app["app"], "POST", "/api/v1/workflow-registry",
        token=real_app["sess_owner"], json={"name": "wf", "yaml_content": "x: 1"},
    )
    assert r.status_code == 201


# ── Artifact destinations: org-admin gate (FLAGGED: no project column) ───
# Keyed globally by workflow name in the admin state file; PUT/DELETE gated
# org-admin until a project column lands. GET stays open.


async def test_artifact_destination_put_member_403(real_app):
    # Body is a valid ArtifactDestination (target is a str) so FastAPI's 422
    # body-validation layer passes and the request reaches the org-admin gate.
    r = await _req(
        real_app["app"], "PUT",
        "/api/v1/admin/workflows/wf1/artifact_destination",
        token=real_app["sess_member"], project=real_app["pa"],
        json={"type": "local", "target": "/tmp/x"},
    )
    assert r.status_code == 403


async def test_artifact_destination_delete_member_403(real_app):
    r = await _req(
        real_app["app"], "DELETE",
        "/api/v1/admin/workflows/wf1/artifact_destination",
        token=real_app["sess_member"], project=real_app["pa"],
    )
    assert r.status_code == 403


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
