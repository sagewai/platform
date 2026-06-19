# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tenant-facing enqueue gate for the Sealed identity-execution preview.

POST /api/v1/workflows/enqueue must refuse identity/full/full_jit runs
(Modes 2/3/3b) in multi-tenant mode unless SAGEWAI_SEALED_PREVIEW is set,
because the Sealed runtime protections are experimental and unwired.
Single-org behaviour is unchanged (the gate allows identity modes there).

The multi-tenant cases drive the real auth path: a seeded IdentityStore
issues a project-scoped session token, exactly like the production
middleware expects.
"""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Single-org fixture (default tenancy) — light-weight admin app
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_fleet_factory_db(tmp_path, monkeypatch):
    """The admin app's fleet registry uses the process-cached factory engine
    (PostgresFleetRegistry, persistent SQLite). Isolate SAGEWAI_HOME and reset the
    cached engine per test so a worker registered by one test can't leak into
    another via the shared SQLite file."""
    from sagewai.db import factory

    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "_fleet_home"))
    factory.reset_engine()
    yield
    factory.reset_engine()


@pytest.fixture
def single_org_state(tmp_path, monkeypatch):
    from sagewai.admin.state_file import AdminStateFile

    path = tmp_path / "admin-state.json"
    sf = AdminStateFile(path=path)
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")

    import sagewai.admin.state_file as _sf_mod

    monkeypatch.setattr(_sf_mod, "default_admin_state_path", lambda: path)
    return sf


@pytest_asyncio.fixture
async def single_org_client(single_org_state, monkeypatch):
    monkeypatch.delenv("SAGEWAI_TENANCY_MODE", raising=False)
    from sagewai.admin.serve import create_admin_serve_app

    app = create_admin_serve_app(single_org_state)
    token = single_org_state.validate_login("a@b.com", "pw123456")["access_token"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as cl:
        yield cl


# ---------------------------------------------------------------------------
# Multi-tenant fixture — real IdentityStore + project-scoped session token
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def multi_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))

    from sagewai.admin.identity_store import IdentityStore
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.db.engine import create_engine

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'id.db'}")
    store = IdentityStore(engine=engine)
    await store.init()
    oid = (await store.bootstrap_org("Acme", "acme"))["id"]
    pa = (await store.create_project(oid, "pa", "PA"))["id"]
    member = await store.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, member["id"], "project:member", project_id=pa)

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")

    app = create_admin_serve_app(sf, identity_store=store)
    token = await store.issue_session(oid, member["id"])

    try:
        yield {"app": app, "token": token, "project": pa}
    finally:
        await engine.dispose()


async def _mt_enqueue(mt, **body_kwargs):
    body = {"workflow_name": "test-wf", **body_kwargs}
    headers = {
        "authorization": f"Bearer {mt['token']}",
        "x-project-id": mt["project"],
    }
    transport = httpx.ASGITransport(app=mt["app"])
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post("/api/v1/workflows/enqueue", headers=headers, json=body)


# ---------------------------------------------------------------------------
# Multi-tenant: identity modes are preview-gated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["identity", "full", "full_jit"])
async def test_identity_mode_refused_in_multi_without_optin(multi_tenant, monkeypatch, mode):
    monkeypatch.delenv("SAGEWAI_SEALED_PREVIEW", raising=False)

    res = await _mt_enqueue(multi_tenant, execution_mode=mode)
    assert res.status_code == 403, res.text
    detail = res.json().get("detail", "")
    assert "preview-only" in detail
    assert "SAGEWAI_SEALED_PREVIEW" in detail
    assert mode in detail


async def test_sandboxed_not_preview_gated_in_multi(multi_tenant, monkeypatch):
    """Mode 1 / SANDBOXED is not preview-gated — it fails the worker check, not the gate."""
    monkeypatch.delenv("SAGEWAI_SEALED_PREVIEW", raising=False)

    res = await _mt_enqueue(multi_tenant, execution_mode="sandboxed")
    # Not 403-preview; the no-worker capability check (400) is what fires.
    assert res.status_code == 400, res.text
    assert "preview-only" not in res.text


async def test_identity_mode_not_preview_gated_with_optin(multi_tenant, monkeypatch):
    """With the opt-in set, the preview gate passes; later checks (no worker) apply."""
    monkeypatch.setenv("SAGEWAI_SEALED_PREVIEW", "1")

    res = await _mt_enqueue(multi_tenant, execution_mode="identity")
    # Past the preview gate → fails on the no-approved-worker capability check.
    assert res.status_code == 400, res.text
    assert "preview-only" not in res.text
    assert "worker" in res.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# Single-org: gate never fires
# ---------------------------------------------------------------------------


async def test_identity_mode_not_preview_gated_single_org(single_org_client):
    """Single-org (default) runs identity modes at operator's own risk — not gated."""
    res = await single_org_client.post(
        "/api/v1/workflows/enqueue",
        json={"workflow_name": "test-wf", "execution_mode": "identity"},
    )
    assert res.status_code == 400, res.text
    assert "preview-only" not in res.text
    assert "worker" in res.json().get("detail", "").lower()
