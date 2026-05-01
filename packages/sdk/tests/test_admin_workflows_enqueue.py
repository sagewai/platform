# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for POST /api/v1/workflows/enqueue — canonical WorkflowRun enqueue endpoint.

Covers:
  1. Mode 0 / BARE — no profile_ref, no artifact_destination → 202, correct metadata
  2. Mode 3 / FULL — profile_ref + artifact_destination → 202, fields populated (DEFERRED
     for full cascade — tested with system-default check only; cascade needs a
     master key & backend which adds significant fixture complexity)
  3. 400 — execution_mode=full without profile_ref and no system default
  4. 400 — unknown execution_mode value
  5. Persisted run matches returned run_id (BARE mode)
  6. Mode 1 / SANDBOXED — requires approved worker; 400 if none registered
"""
from __future__ import annotations

import json

import httpx
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    """Isolated admin-state.json for each test."""
    path = tmp_path / "admin-state.json"
    path.write_text(json.dumps({"setup_complete": True}))

    import sagewai.admin.state_file as _sf_mod

    monkeypatch.setattr(_sf_mod, "_DEFAULT_STATE_FILE", path)
    return path


@pytest.fixture
async def client(state_path):
    """AsyncClient backed by the full admin app."""
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=state_path)
    app = create_admin_serve_app(sf)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as cl:
        yield cl


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _enqueue(client, **body_kwargs):
    """POST /api/v1/workflows/enqueue with the given body fields."""
    body = {"workflow_name": "test-wf", **body_kwargs}
    return client.post("/api/v1/workflows/enqueue", json=body)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


async def test_bare_mode_returns_202_with_correct_metadata(client):
    """Mode 0 / BARE — no sandbox, no profile_ref. Must return 202 with correct metadata."""
    res = await _enqueue(
        client,
        execution_mode="bare",
        input_data={"goal": "hello world"},
    )
    assert res.status_code == 202, res.text
    data = res.json()
    assert data["status"] == "pending"
    assert data["execution_mode"] == "bare"
    assert data["requires_sandbox_mode"] == "none"
    assert data["security_profile_ref"] is None
    assert data["effective_env_keys"] == []
    assert data["effective_secret_keys"] == []
    assert data["run_id"].startswith("wf-")


async def test_bare_mode_persists_run_to_state_file(client, state_path):
    """Persisted WorkflowRun row must use the run_id returned in the response."""
    res = await _enqueue(client, execution_mode="bare")
    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]

    state = json.loads(state_path.read_text())
    run_ids = [r["run_id"] for r in state.get("workflow_runs", [])]
    assert run_id in run_ids, f"{run_id!r} not found in persisted run_ids: {run_ids}"


async def test_bare_mode_persisted_run_has_correct_execution_mode(client, state_path):
    """The persisted WorkflowRun dict must carry execution_mode=bare."""
    res = await _enqueue(client, execution_mode="bare", input_data={"x": 1})
    assert res.status_code == 202
    run_id = res.json()["run_id"]

    state = json.loads(state_path.read_text())
    runs = {r["run_id"]: r for r in state.get("workflow_runs", [])}
    assert run_id in runs
    assert runs[run_id]["execution_mode"] == "bare"
    assert runs[run_id]["requires_sandbox_mode"] == "none"
    assert runs[run_id]["workflow_name"] == "test-wf"


async def test_unknown_execution_mode_returns_400(client):
    """Unrecognised execution_mode must return 400 with an explanatory detail."""
    res = await _enqueue(client, execution_mode="turbo-quantum")
    assert res.status_code == 400, res.text
    detail = res.json().get("detail", "")
    assert "turbo-quantum" in detail, f"Expected mode name in error: {detail!r}"


async def test_missing_workflow_name_returns_400(client):
    """Omitting workflow_name must return 400."""
    res = await client.post(
        "/api/v1/workflows/enqueue",
        json={"execution_mode": "bare"},
    )
    assert res.status_code == 400, res.text


async def test_full_mode_without_profile_ref_and_no_system_default_returns_400(client):
    """Mode 3 / FULL without a security_profile_ref and no system default must return 400.

    Two checks gate a FULL enqueue: the worker-capability check and the
    profile_ref check. Both fire in order; which fires first depends on
    the fleet state. Here we just assert the request is rejected (400) —
    the 'no workers' error is also correct because FULL requires PER_RUN
    sandbox mode and no workers are registered in a fresh registry.
    """
    res = await _enqueue(client, execution_mode="full")
    assert res.status_code == 400, res.text
    # Either the capability error or the profile_ref error is acceptable
    detail = res.json().get("detail", "")
    assert detail, "Expected a non-empty error detail"


async def test_full_jit_mode_without_profile_ref_and_no_system_default_returns_400(client):
    """Mode 3b / FULL_JIT mirrors the same check as FULL."""
    res = await _enqueue(client, execution_mode="full_jit")
    assert res.status_code == 400, res.text


async def test_sandboxed_mode_without_approved_worker_returns_400(client):
    """Mode 1 / SANDBOXED requires a sandbox (PER_RUN) — 400 if no approved worker.

    No workers are registered in a fresh InMemoryFleetRegistry, so the
    capability check must fail with an explanatory error.
    """
    res = await _enqueue(client, execution_mode="sandboxed")
    assert res.status_code == 400, res.text
    detail = res.json().get("detail", "")
    assert "worker" in detail.lower(), (
        f"Expected worker-related error; got: {detail!r}"
    )


async def test_identity_mode_without_approved_worker_returns_400(client):
    """Mode 2 / IDENTITY also requires PER_RUN sandbox — same worker check."""
    res = await _enqueue(client, execution_mode="identity")
    assert res.status_code == 400, res.text
    detail = res.json().get("detail", "")
    assert "worker" in detail.lower(), f"Unexpected error: {detail!r}"


async def test_full_mode_with_system_default_and_no_workers_returns_400(
    state_path, monkeypatch
):
    """FULL mode passes the profile_ref check when system_profile_ref is set,
    but still requires an approved worker → 400.
    """
    import sagewai.admin.state_file as _sf_mod

    # Write a state file that has a system_profile_ref configured
    state_path.write_text(json.dumps({
        "setup_complete": True,
        "sealed": {"system_profile_ref": "builtin:system-default"},
    }))
    monkeypatch.setattr(_sf_mod, "_DEFAULT_STATE_FILE", state_path)

    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=state_path)
    app = create_admin_serve_app(sf)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as cl:
        res = await cl.post(
            "/api/v1/workflows/enqueue",
            json={"workflow_name": "test-wf", "execution_mode": "full"},
        )
    # No workers → capability check fires before cascade resolution for non-BARE
    assert res.status_code == 400, res.text
    detail = res.json().get("detail", "")
    assert "worker" in detail.lower(), f"Unexpected error: {detail!r}"


async def test_bare_mode_default_execution_mode_is_full(client):
    """When execution_mode is omitted the server defaults to 'full'.

    With no system_profile_ref configured this must return 400, not 202.
    This verifies the default-to-full behaviour described in the spec.
    """
    res = await client.post(
        "/api/v1/workflows/enqueue",
        json={"workflow_name": "test-wf"},
    )
    # Default is 'full' → no profile_ref + no system default → 400
    assert res.status_code == 400, res.text


async def test_artifact_destination_invalid_structure_returns_400(client):
    """Malformed artifact_destination payload must return 400."""
    res = await _enqueue(
        client,
        execution_mode="bare",
        artifact_destination={"kind": "unknown-backend"},  # not a valid ArtifactDestination
    )
    assert res.status_code == 400, res.text


# ---------------------------------------------------------------------------
# DEFERRED tests (require Sealed master key + backend fixture)
# ---------------------------------------------------------------------------
# The following scenarios are deferred because they require:
#   - A live BuiltinSecretsBackend with an unlocked master key
#   - At least one created Profile with env/secrets
#
# Deferred:
#   Mode 2 / IDENTITY with a real profile_ref → effective_env_keys populated
#   Mode 3 / FULL + profile_ref + artifact_destination → all fields populated
#   Revoked secret_key raises 400 (SecretRevokedError path)
#
# These are tracked in issue #167.
