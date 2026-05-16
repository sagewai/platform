# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the autopilot cancel endpoint and SSE route."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin.autopilot_lifecycle import MissionStatus, transition_mission
from sagewai.admin.autopilot_lifecycle_bus import get_lifecycle_bus
from sagewai.admin.autopilot_routes import create_autopilot_router
from sagewai.admin.autopilot_state import save_mission
from sagewai.admin.state_file import AdminStateFile


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
def client(authenticated_sf):
    sf, token = authenticated_sf
    app = FastAPI()
    app.include_router(create_autopilot_router(sf), prefix="/api/v1")
    c = TestClient(app, raise_server_exceptions=True)
    c.cookies.set("sagewai_auth", token)
    return c, sf, token


def _make_pending_mission(sf: AdminStateFile, mission_id: str = "test-001") -> dict:
    return save_mission(
        sf,
        {
            "mission_id": mission_id,
            "project_id": None,
            "status": "pending",
            "created_at": "2026-05-09T00:00:00+00:00",
            "goal_preview": "test goal",
            "slots": {},
            "blueprint_json": "{}",
            "score": None,
        },
    )


# ── Cancel endpoint tests ─────────────────────────────────────────────


def test_cancel_pending_returns_409(client):
    c, sf, _ = client
    _make_pending_mission(sf)
    resp = c.post(
        "/api/v1/autopilot/missions/test-001/cancel",
        json={"reason": "changed my mind"},
    )
    assert resp.status_code == 409


def test_cancel_running_returns_202(client):
    c, sf, _ = client
    _make_pending_mission(sf)
    transition_mission(sf, "test-001", MissionStatus.RUNNING)
    resp = c.post(
        "/api/v1/autopilot/missions/test-001/cancel",
        json={"reason": "operator request"},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "cancelled"


def test_cancel_completed_returns_409(client):
    c, sf, _ = client
    _make_pending_mission(sf)
    transition_mission(sf, "test-001", MissionStatus.RUNNING)
    transition_mission(sf, "test-001", MissionStatus.COMPLETED)
    resp = c.post(
        "/api/v1/autopilot/missions/test-001/cancel",
        json={"reason": "too late"},
    )
    assert resp.status_code == 409


def test_cancel_sets_cancel_reason(client):
    c, sf, _ = client
    _make_pending_mission(sf)
    transition_mission(sf, "test-001", MissionStatus.RUNNING)
    c.post(
        "/api/v1/autopilot/missions/test-001/cancel",
        json={"reason": "specific reason"},
    )
    from sagewai.admin.autopilot_state import get_mission
    record = get_mission(sf, "test-001")
    assert record["cancel_reason"] == "specific reason"
    assert record["status"] == "cancelled"


def test_cancel_missing_reason_returns_422(client):
    c, sf, _ = client
    _make_pending_mission(sf)
    transition_mission(sf, "test-001", MissionStatus.RUNNING)
    resp = c.post("/api/v1/autopilot/missions/test-001/cancel", json={})
    assert resp.status_code == 422


def test_cancel_notfound_returns_404(client):
    c, sf, _ = client
    resp = c.post(
        "/api/v1/autopilot/missions/no-such-id/cancel",
        json={"reason": "does not exist"},
    )
    assert resp.status_code == 404
