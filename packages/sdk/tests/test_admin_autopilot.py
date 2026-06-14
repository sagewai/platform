# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
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
from sagewai.autopilot._types import AgentKind
from sagewai.autopilot.agent_graph import Agent, AgentGraph
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.models import EvalRef, Metric, ProviderRequirement, TrainingHook
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
        # First enable mints the deterministic, org-derived identity.
        tc.post("/api/v1/autopilot/enable", json={"tier": "anonymous"})
        first = get_autopilot_identity(sf)
        assert first is not None
        # Simulate a completed enrollment (the server-derived secret adopted).
        set_autopilot_identity(
            sf,
            InstanceIdentity(
                instance_id=first.instance_id,
                instance_secret="ab" * 32,
                registered=True,
            ),
        )
        # Re-enabling must NOT clobber the enrolled identity (idempotent): same
        # org-derived id, and the adopted secret + registered flag are kept.
        tc.post("/api/v1/autopilot/enable", json={"tier": "free"})
        identity = get_autopilot_identity(sf)
        assert identity is not None
        assert identity.instance_id == first.instance_id
        assert identity.instance_secret == "ab" * 32
        assert identity.registered is True

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
        # GoalRouter with no real service returns SynthesisNeeded.
        # Field name is `routing_result` (renamed from `kind` in PR #263).
        assert data["routing_result"] == "synthesis_needed"
        assert "goal" in data

    def test_goal_response_has_routing_result_field(self, auth_client):
        tc, _ = auth_client
        resp = tc.post("/api/v1/autopilot/goal", json={"goal": "build a dashboard"})
        assert resp.status_code == 200
        assert "routing_result" in resp.json()


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


# ---------------------------------------------------------------------------
# Helpers for blueprint-enriched tests
# ---------------------------------------------------------------------------


def _sample_blueprint() -> Blueprint:
    """Build a minimal but structurally valid Blueprint for test seeding."""
    graph = AgentGraph(
        nodes=(
            Agent(
                id="planner",
                kind=AgentKind.LLM,
                role="planner",
                prompt_ref="prompts/planner.md",
                tools=("web_search",),
            ),
            Agent(
                id="writer",
                kind=AgentKind.LLM,
                role="writer",
                prompt_ref="prompts/writer.md",
            ),
        ),
        edges=(("planner", "writer"),),
        entry="planner",
    )
    return Blueprint(
        id="sample-bp",
        version="1.0",
        title="Sample",
        description="A sample blueprint for tests.",
        agent_graph=graph,
        tools_required=("web_search",),
        providers_required=(
            ProviderRequirement(role="primary", capability="reasoning", tier="medium"),
        ),
        training_data_hooks=(
            TrainingHook(event="writer.completed", dataset="train/sample"),
        ),
        success_criteria=EvalRef(
            dataset_id="ds-1",
            metrics=(Metric(name="accuracy", value=0.9),),
        ),
    )


def _seed_mission(
    sf,
    *,
    mission_id: str = "test-mission-001",
    blueprint: Blueprint | None = None,
    extra: dict | None = None,
) -> dict:
    """Persist a mission dict into the state file and return it."""
    mission: dict = {
        "mission_id": mission_id,
        "project_id": "proj-a",
        "status": "pending",
        "created_at": "2026-05-09T10:00:00+00:00",
        "goal_preview": "Summarise the weekly sales data.",
        "slots": {"topic": "sales", "__blueprint_json__": "should-be-filtered"},
        "blueprint_json": blueprint.model_dump_json() if blueprint is not None else "",
        "score": 0.93,
    }
    if extra:
        mission.update(extra)
    return save_mission(sf, mission)


# ---------------------------------------------------------------------------
# GET /api/v1/autopilot/missions/{mission_id}
# ---------------------------------------------------------------------------


class TestGetMissionDetail:
    def test_get_mission_detail_returns_full_blueprint_metadata(self, auth_client):
        """Happy-path: detail endpoint exposes all blueprint fields."""
        tc, sf = auth_client
        bp = _sample_blueprint()
        _seed_mission(sf, blueprint=bp)

        resp = tc.get("/api/v1/autopilot/missions/test-mission-001")
        assert resp.status_code == 200
        data = resp.json()

        # Core identity fields.
        assert data["id"] == "test-mission-001"
        assert data["status"] == "pending"
        assert data["goal_text"] == "Summarise the weekly sales data."
        assert data["project_id"] == "proj-a"
        assert data["created_at"] == "2026-05-09T10:00:00+00:00"
        # updated_at falls back to created_at when absent.
        assert data["updated_at"] == "2026-05-09T10:00:00+00:00"

        # Blueprint metadata.
        assert data["blueprint_id"] == "sample-bp"
        assert data["description"] == "A sample blueprint for tests."

        # Agent graph — 2 nodes, 1 edge.
        graph = data["agent_graph_json"]
        assert len(graph["nodes"]) == 2
        node_ids = {n["id"] for n in graph["nodes"]}
        assert "planner" in node_ids
        assert "writer" in node_ids
        planner = next(n for n in graph["nodes"] if n["id"] == "planner")
        assert planner["kind"] == "llm"
        assert planner["role"] == "planner"
        assert "web_search" in planner["tools"]

        assert len(graph["edges"]) == 1
        assert graph["edges"][0] == {"from": "planner", "to": "writer"}

        # Tools required.
        assert data["tools_required"] == [{"name": "web_search"}]

        # Providers required.
        assert len(data["providers_required"]) == 1
        prov = data["providers_required"][0]
        assert prov["role"] == "primary"
        assert prov["capability"] == "reasoning"
        assert prov["tier"] == "medium"
        assert prov["name"] == "primary"

        # Success criteria.
        assert len(data["success_criteria"]) == 1
        crit = data["success_criteria"][0]
        assert crit["metric"] == "accuracy"
        assert crit["op"] == ">="
        assert crit["target"] == pytest.approx(0.9)

        # Training hooks.
        assert len(data["training_data_hooks"]) == 1
        hook = data["training_data_hooks"][0]
        assert hook["event"] == "writer.completed"
        assert hook["dataset"] == "train/sample"
        assert hook["format"] == "alpaca"

        # Slots — internal __ keys must be filtered out.
        assert "topic" in data["slots"]
        assert data["slots"]["topic"] == "sales"
        assert "__blueprint_json__" not in data["slots"]

        # estimated_cost is None for v1.0.
        assert data["estimated_cost"] is None

    def test_get_mission_detail_404_for_unknown_id(self, auth_client):
        """Non-existent mission IDs return 404."""
        tc, _ = auth_client
        resp = tc.get("/api/v1/autopilot/missions/does-not-exist")
        assert resp.status_code == 404

    def test_get_mission_detail_unauthenticated_returns_401(self, client):
        """Unauthenticated requests are rejected with 401."""
        resp = client.get("/api/v1/autopilot/missions/any-id")
        assert resp.status_code == 401

    def test_get_mission_detail_empty_blueprint_returns_safe_defaults(self, auth_client):
        """A mission with no blueprint JSON returns empty defaults, not an error."""
        tc, sf = auth_client
        _seed_mission(sf, blueprint=None)

        resp = tc.get("/api/v1/autopilot/missions/test-mission-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["blueprint_id"] is None
        assert data["description"] == ""
        assert data["agent_graph_json"] == {"nodes": [], "edges": []}
        assert data["tools_required"] == []
        assert data["providers_required"] == []
        assert data["success_criteria"] == []
        assert data["training_data_hooks"] == []
        assert data["estimated_cost"] is None


# ---------------------------------------------------------------------------
# GET /api/v1/autopilot/missions — blueprint metadata enrichment
# ---------------------------------------------------------------------------


class TestListMissionsBlueprintMetadata:
    def test_list_missions_includes_blueprint_metadata_summary(self, auth_client):
        """Each item in the list response carries blueprint metadata fields."""
        tc, sf = auth_client
        bp = _sample_blueprint()
        _seed_mission(sf, mission_id="m-enriched", blueprint=bp)

        resp = tc.get("/api/v1/autopilot/missions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

        item = data["missions"][0]
        # Must have all enriched fields, not just the raw mission keys.
        assert item["id"] == "m-enriched"
        assert "tools_required" in item
        assert item["tools_required"] == [{"name": "web_search"}]
        assert "agent_graph_json" in item
        assert len(item["agent_graph_json"]["nodes"]) == 2
        assert "providers_required" in item
        assert len(item["providers_required"]) == 1
        assert "success_criteria" in item
        assert len(item["success_criteria"]) == 1
        assert "training_data_hooks" in item
        assert len(item["training_data_hooks"]) == 1

    def test_list_missions_outer_shape_preserved(self, auth_client):
        """Enriching items must not break the outer {missions, count} shape."""
        tc, sf = auth_client
        _seed_mission(sf, mission_id="m1")
        _seed_mission(sf, mission_id="m2", extra={"mission_id": "m2", "project_id": "proj-a"})

        resp = tc.get("/api/v1/autopilot/missions")
        assert resp.status_code == 200
        data = resp.json()
        assert "missions" in data
        assert "count" in data
        assert data["count"] == 2
        assert len(data["missions"]) == 2
