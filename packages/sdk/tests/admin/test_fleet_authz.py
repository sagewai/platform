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
def app_token(tmp_path, monkeypatch):
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile

    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    from sagewai.db import factory as _factory
    _factory.reset_engine()
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


async def _worker_org_id(app, worker_id):
    worker = await app.state.fleet_registry.get_worker(worker_id)
    assert worker is not None
    return worker.org_id


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


def _wh(worker_id, secret):
    return {"X-Worker-Id": worker_id, "X-Worker-Secret": secret}


def test_pending_worker_cannot_claim_then_can_after_approve(client):
    reg = _register(client, name="w-pending").json()
    wid, secret = reg["worker_id"], reg["worker_secret"]
    worker = TestClient(client.app)
    r = worker.post("/api/v1/fleet/claim", headers=_wh(wid, secret), json={})
    assert r.status_code == 403
    assert r.json().get("status") == "pending"
    _approve(client, wid)
    r2 = worker.post("/api/v1/fleet/claim", headers=_wh(wid, secret), json={})
    assert r2.status_code in (200, 204)  # approved: claims (204 = nothing queued)


def test_revoked_worker_cannot_claim(client):
    reg = _register(client, name="w-rev").json()
    wid, secret = reg["worker_id"], reg["worker_secret"]
    _approve(client, wid)
    client.post(f"/api/v1/fleet/workers/{wid}/revoke")
    worker = TestClient(client.app)
    r = worker.post("/api/v1/fleet/claim", headers=_wh(wid, secret), json={})
    assert r.status_code == 403
    assert r.json().get("status") == "revoked"


def test_unknown_worker_claim_401(client):
    worker = TestClient(client.app)
    r = worker.post("/api/v1/fleet/claim", headers=_wh("does-not-exist", "wrong"), json={})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_report_wrong_worker_denied_and_idempotent(app_token):
    app, sf, token = app_token
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    reg = _register(c, name="w-owner").json()
    wid, secret = reg["worker_id"], reg["worker_secret"]
    _approve(c, wid)
    worker = TestClient(app)
    # Enqueue a task and claim it as this worker.
    org_id = await _worker_org_id(app, wid)
    await app.state.fleet_task_store.enqueue({"run_id": "r1", "org_id": org_id, "model": "gpt-4o", "pool": "default"})
    claim = worker.post("/api/v1/fleet/claim", headers=_wh(wid, secret), json={})
    assert claim.status_code == 200, claim.text
    # A different (approved) worker cannot report it.
    other_reg = _register(c, name="w-other").json()
    other, other_secret = other_reg["worker_id"], other_reg["worker_secret"]
    _approve(c, other)
    bad = worker.post(
        "/api/v1/fleet/report",
        headers=_wh(other, other_secret),
        json={"run_id": "r1", "status": "completed"},
    )
    assert bad.status_code == 403
    # The owner reports — twice (idempotent).
    ok = worker.post(
        "/api/v1/fleet/report",
        headers=_wh(wid, secret),
        json={"run_id": "r1", "status": "completed", "output": "ok"},
    )
    assert ok.status_code == 200
    dup = worker.post(
        "/api/v1/fleet/report",
        headers=_wh(wid, secret),
        json={"run_id": "r1", "status": "completed", "output": "ok"},
    )
    assert dup.status_code == 200


def test_pending_worker_can_heartbeat(client):
    reg = _register(client, name="w-hb").json()
    wid, secret = reg["worker_id"], reg["worker_secret"]
    worker = TestClient(client.app)
    hb = worker.post("/api/v1/fleet/heartbeat", headers=_wh(wid, secret), json={})
    assert hb.status_code == 200  # heartbeat has no approval gate


def test_claim_forwards_poll_timeout_to_dispatcher(app_token):
    app, sf, token = app_token
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    reg = _register(c, name="w-pt").json()
    wid, secret = reg["worker_id"], reg["worker_secret"]
    _approve(c, wid)
    worker = TestClient(app)

    captured: dict = {}
    orig = app.state.fleet_dispatcher.claim

    async def spy(*args, **kwargs):
        captured.update(kwargs)
        return None

    app.state.fleet_dispatcher.claim = spy  # same object the route calls
    try:
        r = worker.post("/api/v1/fleet/claim", headers=_wh(wid, secret), json={"poll_timeout": 3.5})
    finally:
        app.state.fleet_dispatcher.claim = orig
    assert r.status_code == 204
    assert captured.get("poll_timeout") == 3.5
