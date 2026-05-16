# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sandbox-allocation and sandbox-override admin routes — Plan J Tasks 2+3."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin.autopilot_routes import create_autopilot_router
from sagewai.admin.autopilot_state import save_mission
from sagewai.admin.state_file import AdminStateFile


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def sf(tmp_path):
    return AdminStateFile(tmp_path / "state.json")


@pytest.fixture()
def auth_client(sf):
    sf.complete_setup(
        org_name="Test Org",
        admin_email="admin@example.com",
        admin_password="hunter2",
    )
    result = sf.validate_login("admin@example.com", "hunter2")
    assert result is not None
    token = result["access_token"]

    app = FastAPI()
    app.include_router(create_autopilot_router(sf), prefix="/api/v1")
    tc = TestClient(app, raise_server_exceptions=True)
    tc.cookies.set("sagewai_auth", token)
    return tc, sf


_BLUEPRINT_JSON = json.dumps({
    "id": "bp-sandbox-test",
    "title": "Sandbox Test Blueprint",
    "version": "1.0",
    "agent_graph": {
        "nodes": [
            {
                "id": "step-trusted",
                "kind": "llm",
                "role": "reader",
                "prompt_ref": "test/read",
                "tools": ["read_file"],
            },
            {
                "id": "step-sandboxed",
                "kind": "llm",
                "role": "researcher",
                "prompt_ref": "test/search",
                "tools": ["web_search"],
            },
            {
                "id": "step-untrusted",
                "kind": "llm",
                "role": "executor",
                "prompt_ref": "test/exec",
                "tools": ["shell_exec"],
            },
        ],
        "edges": [],
        "entry": "step-trusted",
    },
    "slots": [],
    "providers_required": [],
    "success_criteria": {"metrics": []},
    "training_data_hooks": [],
})


def _seed_mission(sf, mission_id="m-sandbox-1", blueprint_json=_BLUEPRINT_JSON):
    return save_mission(sf, {
        "mission_id": mission_id,
        "project_id": "proj-a",
        "status": "pending",
        "created_at": "2026-05-09T10:00:00+00:00",
        "goal_preview": "Sandbox tier allocation test.",
        "slots": {},
        "blueprint_json": blueprint_json,
        "score": 0.9,
    })


# ── GET /autopilot/missions/{id}/sandbox-allocation ───────────────────────


class TestSandboxAllocation:
    def test_returns_tier_for_each_step(self, auth_client):
        tc, sf = auth_client
        _seed_mission(sf)

        resp = tc.get("/api/v1/autopilot/missions/m-sandbox-1/sandbox-allocation")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data) == 3
        by_id = {row["step_id"]: row for row in data}

        assert by_id["step-trusted"]["tier"] == "TRUSTED"
        assert by_id["step-sandboxed"]["tier"] == "SANDBOXED"
        assert by_id["step-untrusted"]["tier"] == "UNTRUSTED"

    def test_includes_tools_and_role(self, auth_client):
        tc, sf = auth_client
        _seed_mission(sf)

        resp = tc.get("/api/v1/autopilot/missions/m-sandbox-1/sandbox-allocation")
        data = resp.json()
        by_id = {row["step_id"]: row for row in data}

        assert "read_file" in by_id["step-trusted"]["tools"]
        assert by_id["step-trusted"]["role"] == "reader"

    def test_unknown_mission_returns_404(self, auth_client):
        tc, _ = auth_client
        resp = tc.get("/api/v1/autopilot/missions/no-such-mission/sandbox-allocation")
        assert resp.status_code == 404

    def test_unauthenticated_returns_401(self, sf):
        app = FastAPI()
        app.include_router(create_autopilot_router(sf), prefix="/api/v1")
        tc = TestClient(app, raise_server_exceptions=False)
        _seed_mission(sf)
        resp = tc.get("/api/v1/autopilot/missions/m-sandbox-1/sandbox-allocation")
        assert resp.status_code == 401

    def test_step_without_tools_is_trusted(self, auth_client):
        tc, sf = auth_client
        bp_json = json.dumps({
            "id": "bp-notool",
            "title": "No Tool",
            "version": "1.0",
            "agent_graph": {
                "nodes": [{"id": "step-notool", "kind": "llm", "role": "planner",
                            "prompt_ref": "test/plan", "tools": []}],
                "edges": [],
                "entry": "step-notool",
            },
            "slots": [],
            "providers_required": [],
            "success_criteria": {"metrics": []},
            "training_data_hooks": [],
        })
        _seed_mission(sf, mission_id="m-notool", blueprint_json=bp_json)

        resp = tc.get("/api/v1/autopilot/missions/m-notool/sandbox-allocation")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["tier"] == "TRUSTED"


# ── POST /autopilot/missions/{id}/sandbox-override ────────────────────────


class TestSandboxOverride:
    def test_downgrade_accepted(self, auth_client):
        """Override SANDBOXED → UNTRUSTED (more restrictive) is a downgrade — accepted."""
        tc, sf = auth_client
        _seed_mission(sf)

        resp = tc.post(
            "/api/v1/autopilot/missions/m-sandbox-1/sandbox-override",
            json={"step_id": "step-sandboxed", "tier": "UNTRUSTED"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["step_id"] == "step-sandboxed"
        assert data["tier"] == "UNTRUSTED"
        assert data["previous_tier"] == "SANDBOXED"

    def test_upgrade_rejected(self, auth_client):
        """Override UNTRUSTED → SANDBOXED (less restrictive) is NOT a downgrade — rejected."""
        tc, sf = auth_client
        _seed_mission(sf)

        resp = tc.post(
            "/api/v1/autopilot/missions/m-sandbox-1/sandbox-override",
            json={"step_id": "step-untrusted", "tier": "SANDBOXED"},
        )
        assert resp.status_code == 422

    def test_same_tier_rejected(self, auth_client):
        """Override to same tier is not a downgrade — rejected."""
        tc, sf = auth_client
        _seed_mission(sf)

        resp = tc.post(
            "/api/v1/autopilot/missions/m-sandbox-1/sandbox-override",
            json={"step_id": "step-sandboxed", "tier": "SANDBOXED"},
        )
        assert resp.status_code == 422

    def test_unknown_step_returns_404(self, auth_client):
        tc, sf = auth_client
        _seed_mission(sf)

        resp = tc.post(
            "/api/v1/autopilot/missions/m-sandbox-1/sandbox-override",
            json={"step_id": "no-such-step", "tier": "UNTRUSTED"},
        )
        assert resp.status_code == 404

    def test_unknown_mission_returns_404(self, auth_client):
        tc, _ = auth_client
        resp = tc.post(
            "/api/v1/autopilot/missions/no-such-mission/sandbox-override",
            json={"step_id": "step-trusted", "tier": "UNTRUSTED"},
        )
        assert resp.status_code == 404

    def test_unauthenticated_returns_401(self, sf):
        app = FastAPI()
        app.include_router(create_autopilot_router(sf), prefix="/api/v1")
        tc = TestClient(app, raise_server_exceptions=False)
        _seed_mission(sf)
        resp = tc.post(
            "/api/v1/autopilot/missions/m-sandbox-1/sandbox-override",
            json={"step_id": "step-trusted", "tier": "UNTRUSTED"},
        )
        assert resp.status_code == 401

    def test_override_persisted_in_subsequent_allocation(self, auth_client):
        """Override stored in mission; subsequent allocation reflects it."""
        tc, sf = auth_client
        _seed_mission(sf)

        tc.post(
            "/api/v1/autopilot/missions/m-sandbox-1/sandbox-override",
            json={"step_id": "step-trusted", "tier": "SANDBOXED"},
        )

        resp = tc.get("/api/v1/autopilot/missions/m-sandbox-1/sandbox-allocation")
        data = resp.json()
        by_id = {row["step_id"]: row for row in data}
        assert by_id["step-trusted"]["tier"] == "SANDBOXED"
        assert by_id["step-trusted"]["overridden"] is True
