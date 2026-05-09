# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for sealed-allocation and sealed-override admin routes — Plan K Tasks 3+4."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin.autopilot_routes import create_autopilot_router
from sagewai.admin.autopilot_state import save_mission
from sagewai.admin.state_file import AdminStateFile
from sagewai.autopilot.sealed_matcher import ProfileRecord


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


_NOW = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
_OLDER = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)

_BLUEPRINT_JSON = json.dumps({
    "id": "bp-sealed-test",
    "title": "Sealed Test Blueprint",
    "version": "1.0",
    "agent_graph": {
        "nodes": [
            {
                "id": "step-read",
                "kind": "llm",
                "role": "reader",
                "prompt_ref": "test/read",
                "tools": ["read_file"],
            },
            {
                "id": "step-search",
                "kind": "llm",
                "role": "researcher",
                "prompt_ref": "test/search",
                "tools": ["web_search"],
            },
            {
                "id": "step-exec",
                "kind": "llm",
                "role": "executor",
                "prompt_ref": "test/exec",
                "tools": ["shell_exec"],
            },
        ],
        "edges": [],
        "entry": "step-read",
    },
    "slots": [],
    "providers_required": [],
    "success_criteria": {"metrics": []},
    "training_data_hooks": [],
})


def _seed_mission(sf, mission_id="m-sealed-1", blueprint_json=_BLUEPRINT_JSON):
    return save_mission(sf, {
        "mission_id": mission_id,
        "project_id": "proj-a",
        "status": "pending",
        "created_at": "2026-05-09T10:00:00+00:00",
        "goal_preview": "Sealed allocation test.",
        "slots": {},
        "blueprint_json": blueprint_json,
        "score": 0.9,
    })


def _make_profiles() -> list[ProfileRecord]:
    return [
        ProfileRecord(
            id="p-fs",
            name="fs-profile",
            granted_scopes=frozenset({"fs.read"}),
            last_used_at=_NOW,
        ),
        ProfileRecord(
            id="p-net",
            name="net-profile",
            granted_scopes=frozenset({"network.outbound.fetch"}),
            last_used_at=_NOW,
        ),
        # shell_exec requires "exec.shell" — no profile covers it
    ]


# ── GET /autopilot/missions/{id}/sealed-allocation ────────────────────────


class TestSealedAllocation:
    def test_returns_matched_profile_per_step(self, auth_client):
        tc, sf = auth_client
        _seed_mission(sf)

        with patch(
            "sagewai.admin.autopilot_routes._get_sealed_profiles_snapshot",
            return_value=_make_profiles(),
        ):
            resp = tc.get("/api/v1/autopilot/missions/m-sealed-1/sealed-allocation")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

        by_id = {row["step_id"]: row for row in data}

        # step-read needs fs.read — p-fs matches
        assert by_id["step-read"]["matched_profile_id"] == "p-fs"
        # step-search needs network.outbound.fetch — p-net matches
        assert by_id["step-search"]["matched_profile_id"] == "p-net"
        # step-exec needs exec.shell — no profile → None + jit_hitl flag
        assert by_id["step-exec"]["matched_profile_id"] is None
        assert by_id["step-exec"]["jit_hitl"] is True

    def test_includes_required_scopes(self, auth_client):
        tc, sf = auth_client
        _seed_mission(sf)

        with patch(
            "sagewai.admin.autopilot_routes._get_sealed_profiles_snapshot",
            return_value=_make_profiles(),
        ):
            resp = tc.get("/api/v1/autopilot/missions/m-sealed-1/sealed-allocation")

        data = resp.json()
        by_id = {row["step_id"]: row for row in data}
        assert "fs.read" in by_id["step-read"]["required_scopes"]

    def test_unknown_mission_returns_404(self, auth_client):
        tc, _ = auth_client
        with patch(
            "sagewai.admin.autopilot_routes._get_sealed_profiles_snapshot",
            return_value=[],
        ):
            resp = tc.get("/api/v1/autopilot/missions/no-such-mission/sealed-allocation")
        assert resp.status_code == 404

    def test_unauthenticated_returns_401(self, sf):
        app = FastAPI()
        app.include_router(create_autopilot_router(sf), prefix="/api/v1")
        tc = TestClient(app, raise_server_exceptions=False)
        _seed_mission(sf)
        with patch(
            "sagewai.admin.autopilot_routes._get_sealed_profiles_snapshot",
            return_value=[],
        ):
            resp = tc.get("/api/v1/autopilot/missions/m-sealed-1/sealed-allocation")
        assert resp.status_code == 401

    def test_override_reflected_in_allocation(self, auth_client):
        """Profile override stored in mission record is shown in allocation."""
        tc, sf = auth_client
        _seed_mission(sf)

        with patch(
            "sagewai.admin.autopilot_routes._get_sealed_profiles_snapshot",
            return_value=_make_profiles(),
        ):
            tc.post(
                "/api/v1/autopilot/missions/m-sealed-1/sealed-override",
                json={"step_id": "step-read", "profile_id": "p-net"},
            )
            resp = tc.get("/api/v1/autopilot/missions/m-sealed-1/sealed-allocation")

        data = resp.json()
        by_id = {row["step_id"]: row for row in data}
        assert by_id["step-read"]["matched_profile_id"] == "p-net"
        assert by_id["step-read"]["overridden"] is True


# ── POST /autopilot/missions/{id}/sealed-override ─────────────────────────


class TestSealedOverride:
    def test_override_accepted_and_persisted(self, auth_client):
        tc, sf = auth_client
        _seed_mission(sf)

        with patch(
            "sagewai.admin.autopilot_routes._get_sealed_profiles_snapshot",
            return_value=_make_profiles(),
        ):
            resp = tc.post(
                "/api/v1/autopilot/missions/m-sealed-1/sealed-override",
                json={"step_id": "step-read", "profile_id": "p-net"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["step_id"] == "step-read"
        assert data["profile_id"] == "p-net"

    def test_unknown_mission_returns_404(self, auth_client):
        tc, _ = auth_client
        with patch(
            "sagewai.admin.autopilot_routes._get_sealed_profiles_snapshot",
            return_value=_make_profiles(),
        ):
            resp = tc.post(
                "/api/v1/autopilot/missions/no-such-mission/sealed-override",
                json={"step_id": "step-read", "profile_id": "p-fs"},
            )
        assert resp.status_code == 404

    def test_unauthenticated_returns_401(self, sf):
        app = FastAPI()
        app.include_router(create_autopilot_router(sf), prefix="/api/v1")
        tc = TestClient(app, raise_server_exceptions=False)
        _seed_mission(sf)
        with patch(
            "sagewai.admin.autopilot_routes._get_sealed_profiles_snapshot",
            return_value=[],
        ):
            resp = tc.post(
                "/api/v1/autopilot/missions/m-sealed-1/sealed-override",
                json={"step_id": "step-read", "profile_id": "p-fs"},
            )
        assert resp.status_code == 401
