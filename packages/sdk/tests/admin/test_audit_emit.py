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

import pytest
from fastapi.testclient import TestClient

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
