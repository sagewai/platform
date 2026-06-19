# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Single-org real-route tests for fleet task enqueueing."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate_fleet_factory_db(tmp_path, monkeypatch):
    """Isolate SAGEWAI_HOME + reset the process-cached factory engine per test so
    each gets a fresh current-schema SQLite (never the developer's real
    ~/.sagewai, which the B2 fail-closed init() probe rejects if stale)."""
    from sagewai.db import factory

    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "_fleet_home"))
    factory.reset_engine()
    yield
    factory.reset_engine()


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


def _wh(worker_id, secret):
    return {"X-Worker-Id": worker_id, "X-Worker-Secret": secret}


def test_enqueue_task_can_be_claimed_by_approved_worker(client):
    reg = client.post(
        "/api/v1/fleet/register",
        json={"name": "w", "models": ["gpt-4o"]},
    )
    assert reg.status_code == 201, reg.text
    worker_id = reg.json()["worker_id"]
    worker_secret = reg.json()["worker_secret"]

    approved = client.post(f"/api/v1/fleet/workers/{worker_id}/approve")
    assert approved.status_code == 200, approved.text

    enq = client.post(
        "/api/v1/fleet/tasks",
        json={"model": "gpt-4o", "payload": {"agent": "a", "message": "hi"}},
    )
    assert enq.status_code == 201, enq.text
    run_id = enq.json()["run_id"]

    worker = TestClient(client.app)
    claim = worker.post("/api/v1/fleet/claim", headers=_wh(worker_id, worker_secret), json={})
    assert claim.status_code == 200, claim.text
    task = claim.json()
    assert task["run_id"] == run_id
    assert task["payload"]["message"] == "hi"


def test_enqueue_task_scrubs_body_project_id_label(client):
    reg = client.post(
        "/api/v1/fleet/register",
        json={"name": "w", "models": ["gpt-4o"]},
    )
    assert reg.status_code == 201, reg.text
    worker_id = reg.json()["worker_id"]
    worker_secret = reg.json()["worker_secret"]
    approved = client.post(f"/api/v1/fleet/workers/{worker_id}/approve")
    assert approved.status_code == 200, approved.text

    enq = client.post(
        "/api/v1/fleet/tasks",
        json={
            "model": "gpt-4o",
            "labels": {"project_id": "x"},
            "payload": {"agent": "a", "message": "hi"},
        },
    )
    assert enq.status_code == 201, enq.text

    worker = TestClient(client.app)
    claim = worker.post("/api/v1/fleet/claim", headers=_wh(worker_id, worker_secret), json={})
    assert claim.status_code == 200, claim.text
    task = claim.json()
    assert task.get("labels", {}) == {}
    assert "project_id" not in task.get("labels", {})


def test_enqueue_stamps_org_id_for_isolation(app_token):
    app, sf, token = app_token
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    run_id = c.post("/api/v1/fleet/tasks", json={"payload": {"message": "hi"}}).json()["run_id"]
    # The durable row is persisted under the requester's org: the org-scoped status
    # read finds it (a missing/foreign org_id would 404), proving org stamping for
    # cross-org isolation — without reaching into store internals.
    assert c.get(f"/api/v1/fleet/tasks/{run_id}").status_code == 200


def test_register_and_enqueue_use_first_class_project(client):
    import asyncio

    reg = client.post("/api/v1/fleet/register", json={"name": "w", "models": ["gpt-4o"]})
    assert reg.status_code == 201
    wid = reg.json()["worker_id"]
    # Worker row got the first-class field (None = org-global in single-org), NOT a label.
    detail = client.get(f"/api/v1/fleet/workers/{wid}").json()
    assert detail["project_id"] is None
    assert "project_id" not in detail.get("labels", {})
    w = asyncio.run(client.app.state.fleet_registry.get_worker(wid))
    assert w.project_id is None
    # Producer: the persisted task carries project_id as a first-class field (read it
    # back through the durable status API, not store internals).
    enq = client.post("/api/v1/fleet/tasks", json={"model": "gpt-4o", "payload": {"message": "hi"}})
    assert enq.status_code == 201
    detail = client.get(f"/api/v1/fleet/tasks/{enq.json()['run_id']}").json()
    assert detail["project_id"] is None  # None = org-global in single-org
