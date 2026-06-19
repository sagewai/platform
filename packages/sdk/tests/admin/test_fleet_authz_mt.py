# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Multi-tenant cross-project isolation for the fleet routes.

Mirrors the proven MT wiring in tests/admin/test_real_route_isolation.py:
inject the IdentityStore + ApiTokenStore on the SAME sqlite engine, and bind
app.state.tenant_audit to that engine (tenant mutations — incl. fleet worker
approve — fire durable W8 audit that fails closed under ASGITransport, which
skips the app lifespan).
"""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from sagewai.admin.api_token_store import ApiTokenStore
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile
from sagewai.admin.tenant_audit import TenantAuditStore
from sagewai.db.engine import create_engine


@pytest_asyncio.fixture
async def mt(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'id.db'}")
    store = IdentityStore(engine=engine)
    await store.init()
    oid = (await store.bootstrap_org("Acme", "acme"))["id"]
    pa = (await store.create_project(oid, "pa", "PA"))["id"]
    pb = (await store.create_project(oid, "pb", "PB"))["id"]
    user_a = await store.create_user(oid, "a@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, user_a["id"], "project:member", project_id=pa)
    user_b = await store.create_user(oid, "b@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, user_b["id"], "project:member", project_id=pb)
    owner = await store.create_user(oid, "o@acme.io", password="pw0000", role="org:owner")

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="o@acme.io", admin_password="pw123456")

    api_tokens = ApiTokenStore(engine=engine)
    await api_tokens.init()
    app = create_admin_serve_app(sf, identity_store=store, api_token_store=api_tokens)

    # Bind durable audit to the test engine (else fleet approve 503s under ASGI).
    audit = TenantAuditStore(engine=engine)
    await audit.init()
    app.state.tenant_audit = audit

    async def mint(user_id, project_id, scopes=("read", "write", "admin")):
        ctx = await store.build_context(oid, user_id, project_id=project_id)
        _, plaintext = await api_tokens.create_for(
            ctx, name="t", scopes=set(scopes), project_id=project_id
        )
        return plaintext

    tok_a = await mint(user_a["id"], pa)
    tok_b = await mint(user_b["id"], pb)
    tok_owner = await mint(owner["id"], None)
    return {"app": app, "pa": pa, "pb": pb, "tok_a": tok_a, "tok_b": tok_b, "tok_owner": tok_owner}


def _client(app, token):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )


def _worker_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Content-Type": "application/json"},
    )


def _wh(worker_id, secret):
    return {"X-Worker-Id": worker_id, "X-Worker-Secret": secret}


@pytest.mark.asyncio
async def test_cross_project_token_cannot_act_through_foreign_worker(mt):
    app = mt["app"]
    # Project A registers + (owner) approves a worker → record stamped project_id=pa.
    async with _client(app, mt["tok_a"]) as ca:
        reg = (await ca.post("/api/v1/fleet/register", json={"name": "wa", "models": ["gpt-4o"]})).json()
        wid, secret = reg["worker_id"], reg["worker_secret"]
    async with _client(app, mt["tok_owner"]) as co:
        await co.post(f"/api/v1/fleet/workers/{wid}/approve")
    # Worker routes are secret-gated; a foreign token is irrelevant without the secret.
    async with _worker_client(app) as cw:
        assert (
            await cw.post("/api/v1/fleet/claim", headers=_wh(wid, "wrong"), json={})
        ).status_code == 401
        assert (
            await cw.post(
                "/api/v1/fleet/report",
                headers=_wh(wid, "wrong"),
                json={"run_id": "x", "status": "completed"},
            )
        ).status_code == 401
        assert (
            await cw.post("/api/v1/fleet/heartbeat", headers=_wh(wid, "wrong"), json={})
        ).status_code == 401
    # A project A worker cannot claim project B's task; it can claim its own project task.
    async with _client(app, mt["tok_b"]) as cb:
        assert (await cb.post("/api/v1/fleet/tasks", json={"model": "gpt-4o"})).status_code == 201
    async with _worker_client(app) as cw:
        assert (
            await cw.post(
                "/api/v1/fleet/claim",
                headers=_wh(wid, secret),
                json={"poll_timeout": 0.01},
            )
        ).status_code == 204
    async with _client(app, mt["tok_a"]) as ca:
        task = await ca.post("/api/v1/fleet/tasks", json={"model": "gpt-4o"})
        assert task.status_code == 201, task.text
        run_id = task.json()["run_id"]
    async with _worker_client(app) as cw:
        claim = await cw.post("/api/v1/fleet/claim", headers=_wh(wid, secret), json={})
        assert claim.status_code == 200, claim.text
        assert claim.json()["run_id"] == run_id


@pytest.mark.asyncio
async def test_project_token_cannot_override_project_via_body_label(mt):
    app = mt["app"]
    # Project A's token registers a worker but tries to claim project B by
    # supplying a body project_id label. The token's scope (pa) must win.
    async with _client(app, mt["tok_a"]) as ca:
        reg = await ca.post(
            "/api/v1/fleet/register",
            json={"name": "wf", "models": ["gpt-4o"], "labels": {"project_id": mt["pb"], "gpu": "a100"}},
        )
        wid = reg.json()["worker_id"]
    async with _client(app, mt["tok_owner"]) as co:
        w = (await co.get(f"/api/v1/fleet/workers/{wid}")).json()
    assert w["labels"]["project_id"] == mt["pa"]   # token scope, not body's "pb"
    assert w["labels"].get("gpu") == "a100"          # other labels preserved
