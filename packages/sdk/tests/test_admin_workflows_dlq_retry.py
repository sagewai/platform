# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the workflow DLQ: GET /workflows/dlq and POST /workflows/dlq/{id}/retry.

Failed workflow runs are ``workflow_runs`` rows with ``status == "failed"``. The
DLQ list surfaces them; retry re-enqueues the run under a fresh run_id (threading
``replay_of_run_id`` for lineage) and drops the failed entry.
"""
from __future__ import annotations

import json

import httpx
import pytest


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    """Isolated admin-state.json for each test (single-org)."""
    from sagewai.admin.state_file import AdminStateFile

    path = tmp_path / "admin-state.json"
    sf = AdminStateFile(path=path)
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")

    import sagewai.admin.state_file as _sf_mod

    monkeypatch.setattr(_sf_mod, "default_admin_state_path", lambda: path)
    return path


@pytest.fixture
async def client(state_path):
    """AsyncClient backed by the full admin app, authenticated as admin."""
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=state_path)
    app = create_admin_serve_app(sf)
    token = sf.validate_login("a@b.com", "pw123456")["access_token"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as cl:
        yield cl


def _seed_failed_run(state_path, *, run_id="wf-failed", status="failed", **extra):
    """Seed a workflow_runs row directly into the state file."""
    from sagewai.admin.state_file import AdminStateFile

    record = {
        "run_id": run_id,
        "workflow_name": "broken-wf",
        "status": status,
        "execution_mode": "bare",
        "requires_sandbox_mode": "none",
        "input_data": {"goal": "x"},
        "error": "boom",
        "enqueued_at": "2026-06-13T00:00:00+00:00",
        "project_id": None,
        **extra,
    }
    AdminStateFile(path=state_path).mutate(
        lambda d: d.setdefault("workflow_runs", []).insert(0, record)
    )
    return run_id


async def test_dlq_lists_failed_runs(client, state_path):
    """GET /workflows/dlq returns failed runs in DLQEntry shape (was a [] stub)."""
    _seed_failed_run(state_path, run_id="wf-fail-a")
    res = await client.get("/workflows/dlq")
    assert res.status_code == 200, res.text
    entries = res.json()
    assert [e["run_id"] for e in entries] == ["wf-fail-a"]
    assert entries[0]["workflow_name"] == "broken-wf"
    assert entries[0]["error"] == "boom"
    assert "created_at" in entries[0]


async def test_dlq_excludes_non_failed_runs(client, state_path):
    """Only status=='failed' rows are DLQ entries."""
    _seed_failed_run(state_path, run_id="wf-done", status="completed")
    res = await client.get("/workflows/dlq")
    assert res.status_code == 200
    assert res.json() == []


async def test_dlq_retry_reenqueues_and_clears(client, state_path):
    """Retry re-enqueues under a fresh id, links lineage, and drops the failed row."""
    _seed_failed_run(state_path, run_id="wf-fail-b")
    res = await client.post("/workflows/dlq/wf-fail-b/retry")
    assert res.status_code == 202, res.text
    new_id = res.json()["new_run_id"]
    assert new_id.startswith("wf-")
    assert new_id != "wf-fail-b"

    state = json.loads(state_path.read_text())
    runs = {r["run_id"]: r for r in state.get("workflow_runs", [])}
    assert "wf-fail-b" not in runs  # original cleared
    assert new_id in runs  # replay enqueued
    assert runs[new_id]["replay_of_run_id"] == "wf-fail-b"
    assert runs[new_id]["status"] != "failed"
    assert runs[new_id]["workflow_name"] == "broken-wf"

    # The DLQ list no longer shows the retried run.
    listing = (await client.get("/workflows/dlq")).json()
    assert "wf-fail-b" not in {e["run_id"] for e in listing}


async def test_dlq_retry_missing_returns_404(client):
    res = await client.post("/workflows/dlq/does-not-exist/retry")
    assert res.status_code == 404


async def test_dlq_retry_non_failed_returns_409(client, state_path):
    """A run that isn't failed can't be retried via the DLQ."""
    _seed_failed_run(state_path, run_id="wf-running", status="running")
    res = await client.post("/workflows/dlq/wf-running/retry")
    assert res.status_code == 409
    # And it stays put.
    state = json.loads(state_path.read_text())
    assert "wf-running" in {r["run_id"] for r in state.get("workflow_runs", [])}
