# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Durable task store wired into the app + project-scoped status reads."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate_factory_db(tmp_path, monkeypatch):
    from sagewai.db import factory

    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "_home"))
    factory.reset_engine()
    yield
    factory.reset_engine()


def _app(tmp_path):
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.fleet.task_store import PostgresTaskStore

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")
    app = create_admin_serve_app(sf)
    assert isinstance(app.state.fleet_task_store, PostgresTaskStore)  # durable, not in-memory
    token = sf.validate_login("a@b.com", "pw123456")["access_token"]
    return app, token


def test_enqueue_then_status_read_is_durable(tmp_path):
    app, token = _app(tmp_path)
    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {token}"})
        enq = c.post("/api/v1/fleet/tasks", json={"model": "gpt-4o", "payload": {"m": "hi"}})
        assert enq.status_code == 201
        run_id = enq.json()["run_id"]
        got = c.get(f"/api/v1/fleet/tasks/{run_id}")
        assert got.status_code == 200 and got.json()["status"] == "pending"
        listed = c.get("/api/v1/fleet/tasks?status=pending")
        assert any(t["run_id"] == run_id for t in listed.json()["tasks"])
        assert c.get("/api/v1/fleet/tasks/does-not-exist").status_code == 404
