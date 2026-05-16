# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for GET /api/v1/autopilot/missions/{id}/fleet-allocation — Plan I Task 3."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from sagewai.admin.autopilot_routes import create_autopilot_router
from sagewai.admin.autopilot_state import save_mission, update_mission
from sagewai.admin.state_file import AdminStateFile
from sagewai.fleet.models import WorkerApprovalStatus, WorkerCapabilities, WorkerRecord


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def sf(tmp_path):
    return AdminStateFile(tmp_path / "state.json")


@pytest.fixture()
def authenticated_sf(sf):
    sf.complete_setup(
        org_name="Test Org",
        admin_email="admin@example.com",
        admin_password="hunter2",
    )
    result = sf.validate_login("admin@example.com", "hunter2")
    assert result is not None
    return sf, result["access_token"]


@pytest.fixture()
def app_and_sf(authenticated_sf):
    sf, _token = authenticated_sf
    app = FastAPI()
    app.include_router(create_autopilot_router(sf), prefix="/api/v1")
    return app, sf


@pytest.fixture()
def auth_headers(authenticated_sf):
    _sf, token = authenticated_sf
    return {"Authorization": f"Bearer {token}"}


# ── helpers ───────────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)

_BLUEPRINT_JSON = json.dumps({
    "id": "bp-fleet-test",
    "title": "Fleet Test Blueprint",
    "version": "1.0",
    "agent_graph": {
        "nodes": [
            {
                "id": "step-1",
                "kind": "llm",
                "role": "researcher",
                "prompt_ref": "test/research",
                "tools": ["web_search"],
            }
        ],
        "edges": [],
        "entry": "step-1",
    },
    "slots": [],
    "providers_required": [],
    "success_criteria": {"metrics": []},
    "training_data_hooks": [],
})


def _seed_mission(sf: AdminStateFile, mid: str, blueprint_json: str = _BLUEPRINT_JSON) -> dict:
    return save_mission(sf, {
        "mission_id": mid,
        "project_id": "proj-fleet",
        "status": "pending",
        "created_at": "2026-05-09T10:00:00+00:00",
        "goal_preview": "Fleet allocation test",
        "slots": {},
        "blueprint_json": blueprint_json,
        "score": 0.8,
    })


def _worker(wid: str, labels: list[str]) -> WorkerRecord:
    return WorkerRecord(
        id=wid,
        name=f"worker-{wid}",
        org_id="org1",
        capabilities=WorkerCapabilities(
            models_canonical=[],
            labels={t: "true" for t in labels},
        ),
        approval_status=WorkerApprovalStatus.APPROVED,
        registered_at=_NOW,
    )


# ── tests ─────────────────────────────────────────────────────────────


async def test_fleet_allocation_pending_mission(app_and_sf, auth_headers):
    """Pending mission returns matched workers per step, no claimed_worker_id."""
    app, sf = app_and_sf
    _seed_mission(sf, "fa-001")

    workers = [_worker("w1", ["web_search"])]
    with patch("sagewai.admin.autopilot_routes._get_fleet_registry_snapshot", return_value=workers):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.get(
                "/api/v1/autopilot/missions/fa-001/fleet-allocation",
                headers=auth_headers,
            )

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["step_id"] == "step-1"
    assert len(row["matched_workers"]) == 1
    assert row["matched_workers"][0]["worker_id"] == "w1"
    assert row["claimed_worker_id"] is None


async def test_fleet_allocation_no_compatible_worker(app_and_sf, auth_headers):
    """When no worker matches the tools, matched_workers is empty."""
    app, sf = app_and_sf
    _seed_mission(sf, "fa-002")

    workers = [_worker("w-no-match", ["fetch_url"])]  # doesn't have web_search
    with patch("sagewai.admin.autopilot_routes._get_fleet_registry_snapshot", return_value=workers):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.get(
                "/api/v1/autopilot/missions/fa-002/fleet-allocation",
                headers=auth_headers,
            )

    assert resp.status_code == 200
    rows = resp.json()
    assert rows[0]["matched_workers"] == []


async def test_fleet_allocation_with_claimed_worker(app_and_sf, auth_headers):
    """Running mission with a claimed step shows claimed_worker_id."""
    app, sf = app_and_sf
    _seed_mission(sf, "fa-003")
    update_mission(sf, "fa-003", lambda m: m.update({
        "trace": [
            {
                "kind": "agent.worker_claimed",
                "step_id": "step-1",
                "worker_id": "w1",
                "worker_name": "worker-w1",
            }
        ]
    }))

    workers = [_worker("w1", ["web_search"])]
    with patch("sagewai.admin.autopilot_routes._get_fleet_registry_snapshot", return_value=workers):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.get(
                "/api/v1/autopilot/missions/fa-003/fleet-allocation",
                headers=auth_headers,
            )

    rows = resp.json()
    assert rows[0]["claimed_worker_id"] == "w1"


async def test_fleet_allocation_unknown_mission(app_and_sf, auth_headers):
    app, sf = app_and_sf
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.get(
            "/api/v1/autopilot/missions/does-not-exist/fleet-allocation",
            headers=auth_headers,
        )
    assert resp.status_code == 404


async def test_fleet_allocation_unauthenticated(app_and_sf):
    app, _sf = app_and_sf
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.get("/api/v1/autopilot/missions/x/fleet-allocation")
    assert resp.status_code == 401
