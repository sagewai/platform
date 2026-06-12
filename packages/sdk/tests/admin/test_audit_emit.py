# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport

from sagewai.admin.audit import emit_audit
from sagewai.admin.state_file import AdminStateFile


def test_emit_audit_persists_event(tmp_path):
    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.complete_setup(org_name="A", admin_email="a@b.com", admin_password="pw123456")
    emit_audit(sf, event_type="sealed.reveal", actor_label="a@b.com",
               target="profile/p1#key", details={"ok": True})
    events = sf._read()["audit_events"]
    assert events[-1]["event_type"] == "sealed.reveal"
    assert events[-1]["actor_label"] == "a@b.com"
    assert events[-1]["target"] == "profile/p1#key"
    assert "ts" in events[-1] and "id" in events[-1]


def test_emit_audit_is_bounded(tmp_path):
    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.complete_setup(org_name="A", admin_email="a@b.com", admin_password="pw123456")
    for i in range(1100):
        emit_audit(sf, event_type="x", actor_label="a@b.com", target=str(i))
    events = sf._read()["audit_events"]
    assert len(events) == 1000           # bounded to most-recent 1000
    assert events[-1]["target"] == "1099"


def test_failed_login_and_logout_are_audited(tmp_path):
    from sagewai.admin.serve import create_admin_serve_app

    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.complete_setup(org_name="A", admin_email="a@b.com", admin_password="pw123456")
    c = TestClient(create_admin_serve_app(sf))

    # failed login
    c.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "wrong"})

    # successful login then logout via Bearer
    raw = sf.validate_login("a@b.com", "pw123456")["access_token"]
    c.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {raw}"})

    types = [e["event_type"] for e in sf._read().get("audit_events", [])]
    assert "auth.login.failed" in types, f"expected auth.login.failed in {types}"
    assert "auth.logout" in types, f"expected auth.logout in {types}"


def test_fleet_worker_approve_is_audited(tmp_path):
    """Approving a fleet worker emits fleet.worker.approved with the real actor."""
    from sagewai.admin.serve import create_admin_serve_app

    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.complete_setup(org_name="A", admin_email="a@b.com", admin_password="pw123456")
    app = create_admin_serve_app(sf)
    c = TestClient(app)

    # log in to get a session token (admin scope)
    raw = sf.validate_login("a@b.com", "pw123456")["access_token"]
    auth = {"Authorization": f"Bearer {raw}"}

    # register a worker via the fleet self-registration route
    reg_r = c.post("/api/v1/fleet/register", json={
        "name": "test-worker",
        "pool": "default",
        "org_id": "default",
    }, headers=auth)
    assert reg_r.status_code == 201, reg_r.text

    worker_id = reg_r.json()["worker_id"]
    approve_r = c.post(f"/api/v1/fleet/workers/{worker_id}/approve", headers=auth)
    assert approve_r.status_code == 200, approve_r.text

    events = sf._read().get("audit_events", [])
    assert any(e["event_type"] == "fleet.worker.approved" for e in events), \
        "approve must emit fleet.worker.approved"


def test_enrollment_key_create_is_audited(tmp_path):
    """Creating an enrollment key emits fleet.enrollment_key.created."""
    from sagewai.admin.serve import create_admin_serve_app

    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.complete_setup(org_name="A", admin_email="a@b.com", admin_password="pw123456")
    c = TestClient(create_admin_serve_app(sf))

    raw = sf.validate_login("a@b.com", "pw123456")["access_token"]
    auth = {"Authorization": f"Bearer {raw}"}

    r = c.post("/api/v1/fleet/enrollment-keys",
               json={"name": "ci-key", "pool": "default"},
               headers=auth)
    assert r.status_code == 201, r.text

    events = sf._read().get("audit_events", [])
    assert any(e["event_type"] == "fleet.enrollment_key.created" for e in events), \
        "create enrollment key must emit fleet.enrollment_key.created"


# ───────────── multi-tenant durable per-tenant audit tail ─────────────


@pytest_asyncio.fixture
async def mt_env(tmp_path, monkeypatch):
    """Real admin app wired multi-tenant on one engine, with a TenantAuditStore
    so the durable per-tenant audit (``_emit_audit``) can be asserted on. Yields
    an org-admin session so the audited admin routes are reachable."""
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))

    from sagewai.admin.admin_resource_store import AdminResourceStore
    from sagewai.admin.identity_store import IdentityStore
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.tenant_audit import TenantAuditStore
    from sagewai.db.engine import create_engine

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'id.db'}")
    ident = IdentityStore(engine=engine)
    await ident.init()
    oid = (await ident.bootstrap_org("Acme", "acme"))["id"]
    pa = (await ident.create_project(oid, "pa", "PA"))["id"]
    admin = await ident.create_user(oid, "a@acme.io", password="pw0000", role="org:admin")

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")
    res = AdminResourceStore(engine=engine)
    await res.init()

    app = create_admin_serve_app(
        sf,
        identity_store=ident,
        admin_resource_store=res,
    )
    audit = TenantAuditStore(engine=engine)
    await audit.init()
    app.state.tenant_audit = audit

    session = await ident.issue_session(oid, admin["id"])

    yield {
        "app": app,
        "ident": ident,
        "audit": audit,
        "oid": oid,
        "pa": pa,
        "admin": admin,
        "session": session,
    }
    await engine.dispose()


def _bearer(token):
    return {"authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_durable_audit_tail_covers_org_fleet_connector(mt_env):
    """In multi mode, update_org + a fleet worker approve + a connector save each
    append a durable per-tenant audit entry to the org's audit chain."""
    app = mt_env["app"]
    headers = _bearer(mt_env["session"])
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # 1) org settings update → org.updated
        r = await c.patch("/api/v1/organization", headers=headers,
                          json={"org_name": "Acme Renamed"})
        assert r.status_code == 200, r.text

        # 2) register + approve a fleet worker → fleet.worker.approved
        reg = await c.post("/api/v1/fleet/register", headers=headers,
                           json={"name": "w1", "pool": "default"})
        assert reg.status_code == 201, reg.text
        worker_id = reg.json()["worker_id"]
        appr = await c.post(f"/api/v1/fleet/workers/{worker_id}/approve", headers=headers)
        assert appr.status_code == 200, appr.text

        # 3) save a connector → connector.saved
        sc = await c.post("/api/v1/connectors/my-conn", headers=headers,
                          json={"type": "http", "config": {}})
        assert sc.status_code == 200, sc.text

    # The org-admin reads the org-shared chain (project_id=None) — org.updated,
    # fleet.worker.approved and connector.saved all live there (no project ctx).
    ctx_admin = await mt_env["ident"].build_context(
        mt_env["oid"], mt_env["admin"]["id"], project_id=None
    )
    chain = await mt_env["audit"].read_chain(ctx_admin)
    actions = {e["action"] for e in chain}
    assert "org.updated" in actions, actions
    assert "fleet.worker.approved" in actions, actions
    assert "connector.saved" in actions, actions


@pytest.mark.asyncio
async def test_durable_audit_is_noop_in_single_org(tmp_path, monkeypatch):
    """The durable tail is a no-op single-org: the same routes succeed with no
    TenantAuditStore wired (single-org uses the file audit only)."""
    monkeypatch.delenv("SAGEWAI_TENANCY_MODE", raising=False)
    from sagewai.admin.serve import create_admin_serve_app

    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.complete_setup(org_name="A", admin_email="a@b.com", admin_password="pw123456")
    c = TestClient(create_admin_serve_app(sf))
    raw = sf.validate_login("a@b.com", "pw123456")["access_token"]
    auth = {"Authorization": f"Bearer {raw}"}

    # connector save succeeds and does NOT require a tenant-audit store.
    r = c.post("/api/v1/connectors/c1", json={"type": "http"}, headers=auth)
    assert r.status_code == 200, r.text
    d = c.request("DELETE", "/api/v1/connectors/c1", headers=auth)
    assert d.status_code == 200, d.text
