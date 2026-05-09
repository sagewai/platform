# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for ``GET /api/v1/autopilot/missions/{id}/trace`` — Plan H Task 6.

Returns the persisted run trace + run summary so the frontend can
replay events on page reload without re-opening an SSE connection.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from sagewai.admin.autopilot_routes import create_autopilot_router
from sagewai.admin.autopilot_state import save_mission, update_mission
from sagewai.admin.state_file import AdminStateFile


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


def _seed_pending_mission(sf: AdminStateFile, mission_id: str = "trace-mission-001") -> dict:
    return save_mission(
        sf,
        {
            "mission_id": mission_id,
            "project_id": "proj-trace",
            "status": "pending",
            "created_at": "2026-05-09T10:00:00+00:00",
            "goal_preview": "Plan H trace endpoint test goal.",
            "slots": {},
            "blueprint_json": "",
            "score": 0.75,
        },
    )


# ── tests ─────────────────────────────────────────────────────────────


async def test_trace_404_unknown_mission(app_and_sf, auth_headers):
    """GET /trace for an unknown mission id returns 404."""
    app, _sf = app_and_sf
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.get(
            "/api/v1/autopilot/missions/does-not-exist/trace",
            headers=auth_headers,
        )
    assert resp.status_code == 404
    body = resp.json()
    assert "does-not-exist" in body.get("detail", "")


async def test_trace_unauth_401(app_and_sf):
    """Without cookie/token, GET /trace returns 401."""
    app, sf = app_and_sf
    _seed_pending_mission(sf)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.get("/api/v1/autopilot/missions/trace-mission-001/trace")
    assert resp.status_code == 401


async def test_trace_returns_empty_for_pending_mission(app_and_sf, auth_headers):
    """Pending mission (no run yet) returns 200 with zeroed/null fields."""
    app, sf = app_and_sf
    mid = "trace-mission-001"
    _seed_pending_mission(sf, mid)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.get(
            f"/api/v1/autopilot/missions/{mid}/trace",
            headers=auth_headers,
        )
    assert resp.status_code == 200
    body = resp.json()

    assert body["mission_id"] == mid
    assert body["run_id"] is None
    assert body["status"] == "pending"
    assert body["events"] == []
    assert body["total_cost_usd"] == 0.0
    assert body["step_count"] == 0
    assert body["output"] is None
    assert body["error"] is None


async def test_trace_returns_persisted_events_after_run(app_and_sf, auth_headers):
    """Trace fields round-trip exactly when set via update_mission."""
    app, sf = app_and_sf
    mid = "trace-mission-002"
    _seed_pending_mission(sf, mid)

    trace_events = [
        {"kind": "mission.started", "ts": "2026-05-09T10:01:00+00:00"},
        {"kind": "agent.started", "ts": "2026-05-09T10:01:01+00:00", "agent_id": "planner"},
        {
            "kind": "agent.tool_call",
            "ts": "2026-05-09T10:01:02+00:00",
            "agent_id": "planner",
            "tool": "search",
        },
        {"kind": "mission.finished", "ts": "2026-05-09T10:01:03+00:00", "status": "completed"},
    ]

    def _populate(rec: dict) -> None:
        rec["run_id"] = "run_abc123"
        rec["started_at"] = "2026-05-09T10:01:00+00:00"
        rec["finished_at"] = "2026-05-09T10:01:03+00:00"
        rec["last_event_at"] = "2026-05-09T10:01:03+00:00"
        rec["total_cost_usd"] = 0.0123
        rec["step_count"] = 2
        rec["output"] = "final"
        rec["status"] = "completed"
        rec["trace"] = trace_events
        rec["error"] = None

    update_mission(sf, mid, _populate)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.get(
            f"/api/v1/autopilot/missions/{mid}/trace",
            headers=auth_headers,
        )
    assert resp.status_code == 200
    body = resp.json()

    assert body["mission_id"] == mid
    assert body["run_id"] == "run_abc123"
    assert body["status"] == "completed"
    assert body["started_at"] == "2026-05-09T10:01:00+00:00"
    assert body["finished_at"] == "2026-05-09T10:01:03+00:00"
    assert body["last_event_at"] == "2026-05-09T10:01:03+00:00"
    assert body["total_cost_usd"] == 0.0123
    assert body["step_count"] == 2
    assert body["output"] == "final"
    assert body["error"] is None
    assert len(body["events"]) == 4
    assert body["events"][0]["kind"] == "mission.started"
    assert body["events"][1]["kind"] == "agent.started"
    assert body["events"][2]["kind"] == "agent.tool_call"
    assert body["events"][2]["tool"] == "search"
    assert body["events"][3]["kind"] == "mission.finished"


async def test_trace_payload_shape_matches_design(app_and_sf, auth_headers):
    """Response contains exactly the keys defined in the Plan H spec."""
    app, sf = app_and_sf
    mid = "trace-mission-003"
    _seed_pending_mission(sf, mid)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.get(
            f"/api/v1/autopilot/missions/{mid}/trace",
            headers=auth_headers,
        )
    assert resp.status_code == 200
    body = resp.json()

    expected_keys = {
        "mission_id",
        "run_id",
        "status",
        "started_at",
        "finished_at",
        "last_event_at",
        "total_cost_usd",
        "step_count",
        "events",
        "output",
        "error",
    }
    assert set(body.keys()) == expected_keys
