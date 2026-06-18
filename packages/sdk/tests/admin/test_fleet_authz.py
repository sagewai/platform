# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Single-org real-route tests for the hardened fleet routes."""
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


def _register(client, name="w", **body):
    body.setdefault("models", ["gpt-4o"])
    return client.post("/api/v1/fleet/register", json={"name": name, **body})


def _worker(client, worker_id):
    return client.get(f"/api/v1/fleet/workers/{worker_id}").json()


def test_register_scrubs_body_project_id(client):
    # Single-org context has no project scope, so a body-supplied project_id
    # must NOT survive into the stored worker record.
    reg = _register(client, name="w-forge", labels={"project_id": "victim", "gpu": "a100"})
    assert reg.status_code in (200, 201), reg.text
    wid = reg.json()["worker_id"]
    labels = _worker(client, wid)["labels"]
    assert "project_id" not in labels         # scrubbed
    assert labels.get("gpu") == "a100"          # other labels preserved


def _approve(client, worker_id):
    return client.post(f"/api/v1/fleet/workers/{worker_id}/approve")


def test_pending_worker_cannot_claim_then_can_after_approve(client):
    wid = _register(client, name="w-pending").json()["worker_id"]
    r = client.post("/api/v1/fleet/claim", json={"worker_id": wid})
    assert r.status_code == 403
    assert r.json().get("status") == "pending"
    _approve(client, wid)
    r2 = client.post("/api/v1/fleet/claim", json={"worker_id": wid})
    assert r2.status_code in (200, 204)  # approved: claims (204 = nothing queued)


def test_revoked_worker_cannot_claim(client):
    wid = _register(client, name="w-rev").json()["worker_id"]
    _approve(client, wid)
    client.post(f"/api/v1/fleet/workers/{wid}/revoke")
    r = client.post("/api/v1/fleet/claim", json={"worker_id": wid})
    assert r.status_code == 403
    assert r.json().get("status") == "revoked"


def test_unknown_worker_claim_404(client):
    r = client.post("/api/v1/fleet/claim", json={"worker_id": "does-not-exist"})
    assert r.status_code == 404


def test_report_wrong_worker_denied_and_idempotent(app_token):
    app, sf, token = app_token
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    wid = _register(c, name="w-owner").json()["worker_id"]
    _approve(c, wid)
    # Enqueue a task and claim it as this worker.
    app.state.fleet_task_store.enqueue({"run_id": "r1", "model": "gpt-4o", "pool": "default"})
    claim = c.post("/api/v1/fleet/claim", json={"worker_id": wid})
    assert claim.status_code == 200, claim.text
    # A different (approved) worker cannot report it.
    other = _register(c, name="w-other").json()["worker_id"]
    _approve(c, other)
    bad = c.post("/api/v1/fleet/report", json={"worker_id": other, "run_id": "r1", "status": "completed"})
    assert bad.status_code == 403
    # The owner reports — twice (idempotent).
    ok = c.post("/api/v1/fleet/report", json={"worker_id": wid, "run_id": "r1", "status": "completed", "output": "ok"})
    assert ok.status_code == 200
    dup = c.post("/api/v1/fleet/report", json={"worker_id": wid, "run_id": "r1", "status": "completed", "output": "ok"})
    assert dup.status_code == 200


def test_pending_worker_can_heartbeat(client):
    wid = _register(client, name="w-hb").json()["worker_id"]
    hb = client.post("/api/v1/fleet/heartbeat", json={"worker_id": wid})
    assert hb.status_code == 200  # heartbeat has no approval gate


def test_claim_forwards_poll_timeout_to_dispatcher(app_token):
    app, sf, token = app_token
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    wid = _register(c, name="w-pt").json()["worker_id"]
    _approve(c, wid)

    captured: dict = {}
    orig = app.state.fleet_dispatcher.claim

    async def spy(*args, **kwargs):
        captured.update(kwargs)
        return None

    app.state.fleet_dispatcher.claim = spy  # same object the route calls
    try:
        r = c.post("/api/v1/fleet/claim", json={"worker_id": wid, "poll_timeout": 3.5})
    finally:
        app.state.fleet_dispatcher.claim = orig
    assert r.status_code == 204
    assert captured.get("poll_timeout") == 3.5
