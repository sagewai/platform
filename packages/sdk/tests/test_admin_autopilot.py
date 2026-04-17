# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for autopilot admin routes and state helpers.

All tests use ``tmp_path`` for the state file so they never touch
``~/.sagewai/``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin.autopilot_routes import create_autopilot_router
from sagewai.admin.autopilot_state import (
    AdminStateIdentityStore,
    get_autopilot_config,
    get_autopilot_identity,
    list_missions,
    save_mission,
    set_autopilot_config,
    set_autopilot_identity,
)
from sagewai.admin.state_file import AdminStateFile
from sagewai.autopilot.sagewai_llm.identity import InstanceIdentity

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sf(tmp_path):
    """AdminStateFile backed by a temp directory."""
    return AdminStateFile(tmp_path / "state.json")


@pytest.fixture()
def authenticated_sf(sf):
    """AdminStateFile with setup complete and a valid auth token."""
    sf.complete_setup(
        org_name="Test Org",
        admin_email="admin@example.com",
        admin_password="hunter2",
    )
    result = sf.validate_login("admin@example.com", "hunter2")
    assert result is not None, "Login failed in fixture"
    return sf, result["access_token"]


@pytest.fixture()
def client(sf):
    """Unauthenticated TestClient for the autopilot router."""
    app = FastAPI()
    app.include_router(create_autopilot_router(sf), prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def auth_client(authenticated_sf):
    """Authenticated TestClient — cookie set with valid token."""
    sf, token = authenticated_sf
    app = FastAPI()
    app.include_router(create_autopilot_router(sf), prefix="/api/v1")
    tc = TestClient(app, raise_server_exceptions=True)
    tc.cookies.set("sagewai_auth", token)
    return tc, sf


# ---------------------------------------------------------------------------
# AdminStateIdentityStore
# ---------------------------------------------------------------------------


class TestAdminStateIdentityStore:
    def test_load_returns_none_when_empty(self, sf):
        store = AdminStateIdentityStore(sf)
        assert store.load() is None

    def test_save_and_load_round_trip(self, sf):
        store = AdminStateIdentityStore(sf)
        identity = InstanceIdentity.generate()
        store.save(identity)
        loaded = store.load()
        assert loaded is not None
        assert loaded.instance_id == identity.instance_id
        assert loaded.instance_secret == identity.instance_secret

    def test_overwrite_identity(self, sf):
        store = AdminStateIdentityStore(sf)
        store.save(InstanceIdentity.generate())
        fresh = InstanceIdentity.generate()
        store.save(fresh)
        loaded = store.load()
        assert loaded is not None
        assert loaded.instance_id == fresh.instance_id


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


class TestAutopilotConfigHelpers:
    def test_get_config_defaults(self, sf):
        config = get_autopilot_config(sf)
        assert config["enabled"] is False
        assert config["tier"] == "anonymous"
        assert "base_url" in config
        assert isinstance(config["confidence_high"], float)
        assert isinstance(config["confidence_low"], float)

    def test_set_config_merges_patch(self, sf):
        set_autopilot_config(sf, {"enabled": True, "tier": "free"})
        config = get_autopilot_config(sf)
        assert config["enabled"] is True
        assert config["tier"] == "free"
        # defaults preserved
        assert "base_url" in config

    def test_set_config_ignores_unknown_keys(self, sf):
        set_autopilot_config(sf, {"unknown_key": "boom"})
        config = get_autopilot_config(sf)
        assert "unknown_key" not in config

    def test_set_identity_and_get(self, sf):
        identity = InstanceIdentity.generate()
        set_autopilot_identity(sf, identity)
        loaded = get_autopilot_identity(sf)
        assert loaded is not None
        assert loaded.instance_id == identity.instance_id


# ---------------------------------------------------------------------------
# Mission helpers
# ---------------------------------------------------------------------------


class TestMissionHelpers:
    def test_list_missions_empty(self, sf):
        assert list_missions(sf) == []

    def test_save_and_list_mission(self, sf):
        mission = {
            "mission_id": "abc123",
            "project_id": "proj-a",
            "status": "pending",
            "goal_preview": "Do something",
        }
        save_mission(sf, mission)
        all_missions = list_missions(sf)
        assert len(all_missions) == 1
        assert all_missions[0]["mission_id"] == "abc123"

    def test_list_missions_project_filter(self, sf):
        save_mission(sf, {"mission_id": "m1", "project_id": "proj-a", "status": "pending"})
        save_mission(sf, {"mission_id": "m2", "project_id": "proj-b", "status": "pending"})
        assert len(list_missions(sf, project_id="proj-a")) == 1
        assert list_missions(sf, project_id="proj-a")[0]["mission_id"] == "m1"

    def test_list_missions_no_filter_returns_all(self, sf):
        save_mission(sf, {"mission_id": "m1", "project_id": "proj-a", "status": "done"})
        save_mission(sf, {"mission_id": "m2", "project_id": "proj-b", "status": "done"})
        assert len(list_missions(sf)) == 2


# ---------------------------------------------------------------------------
# GET /api/v1/autopilot/status
# ---------------------------------------------------------------------------


class TestAutopilotStatus:
    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/api/v1/autopilot/status")
        assert resp.status_code == 401

    def test_authenticated_returns_200(self, auth_client):
        tc, _ = auth_client
        resp = tc.get("/api/v1/autopilot/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert "tier" in data
        assert data["enabled"] is False  # default

    def test_status_reflects_config(self, auth_client):
        tc, sf = auth_client
        set_autopilot_config(sf, {"enabled": True, "tier": "premium"})
        resp = tc.get("/api/v1/autopilot/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["tier"] == "premium"


# ---------------------------------------------------------------------------
# POST /api/v1/autopilot/enable
# ---------------------------------------------------------------------------


class TestAutopilotEnable:
    def test_unauthenticated_returns_401(self, client):
        resp = client.post("/api/v1/autopilot/enable", json={"tier": "anonymous"})
        assert resp.status_code == 401

    def test_enable_with_valid_tier(self, auth_client):
        tc, sf = auth_client
        resp = tc.post("/api/v1/autopilot/enable", json={"tier": "free"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["enabled"] is True
        assert data["tier"] == "free"
        assert "instance_id" in data

    def test_enable_persists_config(self, auth_client):
        tc, sf = auth_client
        tc.post("/api/v1/autopilot/enable", json={"tier": "premium"})
        config = get_autopilot_config(sf)
        assert config["enabled"] is True
        assert config["tier"] == "premium"

    def test_enable_creates_identity(self, auth_client):
        tc, sf = auth_client
        assert get_autopilot_identity(sf) is None
        tc.post("/api/v1/autopilot/enable", json={"tier": "anonymous"})
        identity = get_autopilot_identity(sf)
        assert identity is not None

    def test_enable_preserves_existing_identity(self, auth_client):
        tc, sf = auth_client
        existing = InstanceIdentity.generate()
        set_autopilot_identity(sf, existing)
        tc.post("/api/v1/autopilot/enable", json={"tier": "anonymous"})
        identity = get_autopilot_identity(sf)
        assert identity is not None
        assert identity.instance_id == existing.instance_id

    def test_invalid_tier_returns_422(self, auth_client):
        tc, _ = auth_client
        resp = tc.post("/api/v1/autopilot/enable", json={"tier": "ultra"})
        assert resp.status_code == 422

    def test_missing_tier_defaults_to_anonymous(self, auth_client):
        tc, _ = auth_client
        resp = tc.post("/api/v1/autopilot/enable", json={})
        assert resp.status_code == 200
        assert resp.json()["tier"] == "anonymous"


# ---------------------------------------------------------------------------
# POST /api/v1/autopilot/disable
# ---------------------------------------------------------------------------


class TestAutopilotDisable:
    def test_unauthenticated_returns_401(self, client):
        resp = client.post("/api/v1/autopilot/disable")
        assert resp.status_code == 401

    def test_disable_sets_enabled_false(self, auth_client):
        tc, sf = auth_client
        # Enable first
        tc.post("/api/v1/autopilot/enable", json={"tier": "free"})
        assert get_autopilot_config(sf)["enabled"] is True

        resp = tc.post("/api/v1/autopilot/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert get_autopilot_config(sf)["enabled"] is False

    def test_disable_preserves_identity(self, auth_client):
        tc, sf = auth_client
        tc.post("/api/v1/autopilot/enable", json={"tier": "free"})
        identity_before = get_autopilot_identity(sf)
        tc.post("/api/v1/autopilot/disable")
        identity_after = get_autopilot_identity(sf)
        assert identity_before is not None
        assert identity_after is not None
        assert identity_before.instance_id == identity_after.instance_id


# ---------------------------------------------------------------------------
# POST /api/v1/autopilot/goal
# ---------------------------------------------------------------------------


class TestAutopilotGoal:
    def test_unauthenticated_returns_401(self, client):
        resp = client.post("/api/v1/autopilot/goal", json={"goal": "do something"})
        assert resp.status_code == 401

    def test_empty_goal_returns_422(self, auth_client):
        tc, _ = auth_client
        resp = tc.post("/api/v1/autopilot/goal", json={"goal": ""})
        assert resp.status_code == 422

    def test_missing_goal_returns_422(self, auth_client):
        tc, _ = auth_client
        resp = tc.post("/api/v1/autopilot/goal", json={})
        assert resp.status_code == 422

    def test_valid_goal_returns_synthesis_needed(self, auth_client):
        """No real service → graceful degradation to synthesis_needed."""
        tc, _ = auth_client
        resp = tc.post("/api/v1/autopilot/goal", json={"goal": "summarise the quarterly report"})
        assert resp.status_code == 200
        data = resp.json()
        # GoalRouter with no real service returns SynthesisNeeded
        assert data["kind"] == "synthesis_needed"
        assert "goal" in data

    def test_goal_response_has_kind_field(self, auth_client):
        tc, _ = auth_client
        resp = tc.post("/api/v1/autopilot/goal", json={"goal": "build a dashboard"})
        assert resp.status_code == 200
        assert "kind" in resp.json()


# ---------------------------------------------------------------------------
# POST /api/v1/autopilot/approve
# ---------------------------------------------------------------------------


class TestAutopilotApprove:
    def _auto_routed_payload(self) -> dict:
        return {
            "result": {
                "kind": "auto_routed",
                "ranked": {"blueprint_json": '{"id":"bp1"}', "score": 0.92},
                "slots": {"topic": "sales"},
                "preview": "Summarise the sales data for Q1.",
            },
            "project_id": "proj-a",
        }

    def test_unauthenticated_returns_401(self, client):
        resp = client.post("/api/v1/autopilot/approve", json=self._auto_routed_payload())
        assert resp.status_code == 401

    def test_wrong_kind_returns_422(self, auth_client):
        tc, _ = auth_client
        resp = tc.post(
            "/api/v1/autopilot/approve",
            json={
                "result": {"kind": "synthesis_needed", "goal": "..."},
                "project_id": "proj-a",
            },
        )
        assert resp.status_code == 422

    def test_picker_needed_returns_422(self, auth_client):
        tc, _ = auth_client
        resp = tc.post(
            "/api/v1/autopilot/approve",
            json={
                "result": {
                    "kind": "picker_needed",
                    "candidates": [{"blueprint_json": "{}", "score": 0.7}],
                },
                "project_id": "proj-a",
            },
        )
        assert resp.status_code == 422

    def test_valid_approve_creates_mission(self, auth_client):
        tc, sf = auth_client
        payload = self._auto_routed_payload()
        resp = tc.post("/api/v1/autopilot/approve", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["ok"] is True
        assert "mission" in data
        mission = data["mission"]
        assert mission["status"] == "pending"
        assert "mission_id" in mission
        assert mission["project_id"] == "proj-a"

    def test_approve_persists_mission(self, auth_client):
        tc, sf = auth_client
        tc.post("/api/v1/autopilot/approve", json=self._auto_routed_payload())
        missions = list_missions(sf, project_id="proj-a")
        assert len(missions) == 1
        assert missions[0]["goal_preview"] == "Summarise the sales data for Q1."

    def test_approve_multiple_missions(self, auth_client):
        tc, sf = auth_client
        tc.post("/api/v1/autopilot/approve", json=self._auto_routed_payload())
        tc.post("/api/v1/autopilot/approve", json=self._auto_routed_payload())
        missions = list_missions(sf)
        assert len(missions) == 2


# ---------------------------------------------------------------------------
# GET /api/v1/autopilot/missions
# ---------------------------------------------------------------------------


class TestAutopilotMissions:
    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/api/v1/autopilot/missions")
        assert resp.status_code == 401

    def test_empty_missions_list(self, auth_client):
        tc, _ = auth_client
        resp = tc.get("/api/v1/autopilot/missions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["missions"] == []
        assert data["count"] == 0

    def test_lists_saved_missions(self, auth_client):
        tc, sf = auth_client
        save_mission(sf, {"mission_id": "m1", "project_id": None, "status": "done"})
        resp = tc.get("/api/v1/autopilot/missions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    def test_project_scoped_listing(self, auth_client):
        tc, sf = auth_client
        save_mission(sf, {"mission_id": "m1", "project_id": "proj-x", "status": "done"})
        save_mission(sf, {"mission_id": "m2", "project_id": "proj-y", "status": "done"})

        # Request with X-Project-ID header
        resp = tc.get("/api/v1/autopilot/missions", headers={"X-Project-ID": "proj-x"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["missions"][0]["mission_id"] == "m1"

    def test_no_project_returns_all(self, auth_client):
        tc, sf = auth_client
        save_mission(sf, {"mission_id": "m1", "project_id": "proj-x", "status": "done"})
        save_mission(sf, {"mission_id": "m2", "project_id": "proj-y", "status": "done"})
        resp = tc.get("/api/v1/autopilot/missions")
        assert resp.status_code == 200
        assert resp.json()["count"] == 2
