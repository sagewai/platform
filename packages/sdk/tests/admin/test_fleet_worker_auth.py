# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Worker-secret + enrollment-key authentication on the fleet routes (single-org)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_token(tmp_path):
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")
    app = create_admin_serve_app(sf)
    token = sf.validate_login("a@b.com", "pw123456")["access_token"]
    return app, sf, token


@pytest.fixture
def client(app_token):
    app, sf, token = app_token
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


def _register(client, **body):
    body.setdefault("name", "w")
    body.setdefault("models", ["gpt-4o"])
    return client.post("/api/v1/fleet/register", json=body)


def test_register_returns_worker_secret(client):
    r = _register(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["worker_id"]
    assert isinstance(body.get("worker_secret"), str) and len(body["worker_secret"]) > 16


def _wh(worker_id, secret):  # worker auth headers (no org token)
    return {"X-Worker-Id": worker_id, "X-Worker-Secret": secret}


def test_claim_requires_worker_secret_not_org_token(app_token):
    app, sf, token = app_token
    admin = TestClient(app)
    admin.headers.update({"Authorization": f"Bearer {token}"})
    reg = _register(admin).json()
    wid, secret = reg["worker_id"], reg["worker_secret"]
    admin.post(f"/api/v1/fleet/workers/{wid}/approve")

    # A fresh client with NO org token, authenticating purely by worker secret.
    worker = TestClient(app)
    # No task queued -> 204; the point is auth succeeds (not 401).
    ok = worker.post("/api/v1/fleet/claim", headers=_wh(wid, secret), json={})
    assert ok.status_code in (200, 204), ok.text
    # Wrong secret -> 401.
    bad = worker.post("/api/v1/fleet/claim", headers=_wh(wid, "wrong"), json={})
    assert bad.status_code == 401
    # Missing credential -> 401.
    none = worker.post("/api/v1/fleet/claim", json={})
    assert none.status_code == 401


def test_pending_worker_claim_403_heartbeat_200(app_token):
    app, sf, token = app_token
    admin = TestClient(app)
    admin.headers.update({"Authorization": f"Bearer {token}"})
    reg = _register(admin).json()
    wid, secret = reg["worker_id"], reg["worker_secret"]
    # NOT approved.
    worker = TestClient(app)
    claim = worker.post("/api/v1/fleet/claim", headers=_wh(wid, secret), json={})
    assert claim.status_code == 403
    assert claim.json().get("status") == "pending"
    hb = worker.post("/api/v1/fleet/heartbeat", headers=_wh(wid, secret), json={})
    assert hb.status_code == 200  # heartbeat has no approval gate


def test_secret_cannot_act_as_another_worker(app_token):
    app, sf, token = app_token
    admin = TestClient(app)
    admin.headers.update({"Authorization": f"Bearer {token}"})
    a = _register(admin, name="a").json()
    b = _register(admin, name="b").json()
    worker = TestClient(app)
    # a's secret with b's id -> 401.
    r = worker.post("/api/v1/fleet/claim", headers=_wh(b["worker_id"], a["worker_secret"]), json={})
    assert r.status_code == 401


def test_report_rejects_non_terminal_status(app_token):
    app, sf, token = app_token
    admin = TestClient(app)
    admin.headers.update({"Authorization": f"Bearer {token}"})
    reg = _register(admin).json()
    wid, secret = reg["worker_id"], reg["worker_secret"]
    admin.post(f"/api/v1/fleet/workers/{wid}/approve")
    worker = TestClient(app)
    r = worker.post("/api/v1/fleet/report", headers=_wh(wid, secret),
                    json={"run_id": "anything", "status": "pending"})
    assert r.status_code == 400, r.text  # not 500, not 403


def test_enrollment_key_register_is_token_less(app_token):
    app, sf, token = app_token
    admin = TestClient(app)
    admin.headers.update({"Authorization": f"Bearer {token}"})
    key = admin.post("/api/v1/fleet/enrollment-keys", json={"name": "k", "models": ["gpt-4o"]}).json()
    raw = key["key"]
    # Register with NO org token, only the enrollment key header.
    anon = TestClient(app)
    r = anon.post(
        "/api/v1/fleet/register",
        headers={"X-Enrollment-Key": raw},
        json={"name": "ek", "models": ["gpt-4o"]},
    )
    assert r.status_code == 201, r.text
    assert r.json().get("worker_secret")


def test_workers_list_does_not_leak_secret_hash(client):
    reg = _register(client)
    assert reg.status_code == 201
    listed = client.get("/api/v1/fleet/workers")
    assert listed.status_code == 200
    workers = listed.json()["workers"]
    assert workers and all("secret_hash" not in w for w in workers)
