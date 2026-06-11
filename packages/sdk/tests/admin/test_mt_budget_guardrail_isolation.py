# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cross-tenant isolation for budget + guardrail routes on the DURABLE store.

Mirrors :mod:`test_real_route_isolation`: builds the full multi-tenant admin app
with an injected, seeded ``AdminResourceStore`` and the audit store bound to the
SAME engine (else a successful mutation fail-closes to 503 under ASGITransport,
which skips the lifespan). Budget + guardrail rows are seeded THROUGH the store
into PB's scope, so a PA actor getting a 404/empty result is isolation (the row
genuinely exists in PB's scope), not absence.

Asserts, for each of budget (kind ``budget_limit``) and guardrails
(kind ``guardrail_config``):

* PA's list/get does NOT see PB's row;
* PA cannot update/delete PB's row (404, existence-hidden);
* a project:viewer is denied writes (403);
* PA can CRUD its OWN row end-to-end (create -> list -> get -> delete).
"""

import httpx
import pytest_asyncio
from httpx import ASGITransport

from sagewai.admin.admin_resource_store import AdminResourceStore
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.state_file import AdminStateFile
from sagewai.admin.tenancy import RequestContext, UserRef
from sagewai.admin.tenant_audit import TenantAuditStore
from sagewai.db.engine import create_engine


@pytest_asyncio.fixture
async def mt_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))

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

    # Durable resource store on the SAME engine the real routes read.
    res = AdminResourceStore(engine=engine)
    await res.init()

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

    # Seed PB's budget + guardrail THROUGH the store so they live in PB's scope
    # (resource_id == agent_name; name == agent_name for the unique index).
    await res.upsert_for(
        _ctx(pb),
        "budget_limit",
        "pb-agent",
        {"agent_name": "pb-agent", "daily_limit_usd": 99, "project_id": pb},
        name="pb-agent",
    )
    await res.upsert_for(
        _ctx(pb),
        "guardrail_config",
        "pb-agent",
        {"agent_name": "pb-agent", "guardrails": [], "project_id": pb},
        name="pb-agent",
    )

    from sagewai.admin.serve import create_admin_serve_app

    app = create_admin_serve_app(
        sf,
        identity_store=store,
        admin_resource_store=res,
    )
    audit = TenantAuditStore(engine=engine)
    await audit.init()
    app.state.tenant_audit = audit
    yield {
        "app": app,
        "pa": pa,
        "pb": pb,
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


# ── Budget isolation ─────────────────────────────────────────────────


async def test_budget_list_excludes_cross_project(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/budget/limits",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 200
    assert "pb-agent" not in {row.get("agent_name") for row in r.json()}


async def test_budget_status_cross_project_hidden(mt_app):
    # PB's budget exists in PB's scope; PA's status read must not surface it.
    r = await _req(
        mt_app["app"], "GET", "/api/v1/budget/status/pb-agent",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 200
    assert r.json().get("limit") is None


async def test_budget_update_cross_project_404(mt_app):
    r = await _req(
        mt_app["app"], "PUT", "/api/v1/budget/limits/pb-agent",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"daily_limit_usd": 1},
    )
    assert r.status_code == 404


async def test_budget_delete_cross_project_404(mt_app):
    r = await _req(
        mt_app["app"], "DELETE", "/api/v1/budget/limits/pb-agent",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404


async def test_budget_viewer_cannot_create_403(mt_app):
    r = await _req(
        mt_app["app"], "POST", "/api/v1/budget/limits",
        token=mt_app["sess_viewer"], project=mt_app["pa"],
        json={"agent_name": "scout", "daily_limit_usd": 5},
    )
    assert r.status_code == 403


async def test_budget_own_crud_end_to_end(mt_app):
    # Create (body project_id is ignored; stamped from session = PA).
    r = await _req(
        mt_app["app"], "POST", "/api/v1/budget/limits",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"agent_name": "scout", "daily_limit_usd": 5, "project_id": mt_app["pb"]},
    )
    assert r.status_code == 201
    assert r.json()["project_id"] == mt_app["pa"]

    # List shows exactly PA's own row.
    listed = await _req(
        mt_app["app"], "GET", "/api/v1/budget/limits",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert listed.status_code == 200
    assert {row["agent_name"] for row in listed.json()} == {"scout"}

    # Update succeeds in-scope.
    upd = await _req(
        mt_app["app"], "PUT", "/api/v1/budget/limits/scout",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"daily_limit_usd": 7},
    )
    assert upd.status_code == 200
    assert upd.json()["daily_limit_usd"] == 7

    # Status reads it back.
    status = await _req(
        mt_app["app"], "GET", "/api/v1/budget/status/scout",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert status.status_code == 200
    assert status.json()["limit"]["daily_limit_usd"] == 7

    # Delete -> gone.
    d = await _req(
        mt_app["app"], "DELETE", "/api/v1/budget/limits/scout",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert d.status_code == 200
    after = await _req(
        mt_app["app"], "GET", "/api/v1/budget/limits",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert after.json() == []


async def test_budget_delete_own_then_missing_404(mt_app):
    # Deleting a never-created own row is a 404 (not a false 200).
    r = await _req(
        mt_app["app"], "DELETE", "/api/v1/budget/limits/never-existed",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404


# ── Guardrail isolation ──────────────────────────────────────────────


async def test_guardrail_list_excludes_cross_project(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/guardrails/configs",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 200
    assert "pb-agent" not in {row.get("agent_name") for row in r.json()}


async def test_guardrail_get_cross_project_404(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/guardrails/configs/pb-agent",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404


async def test_guardrail_viewer_cannot_write_403(mt_app):
    r = await _req(
        mt_app["app"], "PUT", "/api/v1/guardrails/configs/scout",
        token=mt_app["sess_viewer"], project=mt_app["pa"],
        json={"guardrails": []},
    )
    assert r.status_code == 403


async def test_guardrail_own_crud_end_to_end(mt_app):
    # Upsert (project_id ignored; stamped from session = PA).
    r = await _req(
        mt_app["app"], "PUT", "/api/v1/guardrails/configs/scout",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"guardrails": [{"type": "pii_filter"}], "project_id": mt_app["pb"]},
    )
    assert r.status_code == 200
    assert r.json()["project_id"] == mt_app["pa"]
    assert r.json()["agent_name"] == "scout"

    # List + get see PA's own row only.
    listed = await _req(
        mt_app["app"], "GET", "/api/v1/guardrails/configs",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert {row["agent_name"] for row in listed.json()} == {"scout"}
    got = await _req(
        mt_app["app"], "GET", "/api/v1/guardrails/configs/scout",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert got.status_code == 200
    assert {g["type"] for g in got.json()["guardrails"]} == {"pii_filter"}

    # Delete a single guardrail type from the config (sub-resource mutation).
    d = await _req(
        mt_app["app"], "DELETE", "/api/v1/guardrails/configs/scout/pii_filter",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert d.status_code == 200
    got2 = await _req(
        mt_app["app"], "GET", "/api/v1/guardrails/configs/scout",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert got2.status_code == 200
    assert got2.json()["guardrails"] == []


async def test_guardrail_delete_type_cross_project_404(mt_app):
    r = await _req(
        mt_app["app"], "DELETE", "/api/v1/guardrails/configs/pb-agent/pii_filter",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404
