# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the POST /api/v1/autopilot/missions/{mission_id}/explain endpoint.

Fixtures and helpers are self-contained — no imports from
``test_admin_autopilot`` to avoid brittle cross-file coupling.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin.autopilot_routes import create_autopilot_router
from sagewai.admin.autopilot_state import save_mission
from sagewai.admin.state_file import AdminStateFile
from sagewai.autopilot._types import AgentKind
from sagewai.autopilot.agent_graph import Agent, AgentGraph
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.models import EvalRef, Metric, ProviderRequirement, TrainingHook

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
# Local helpers (private to this test file)
# ---------------------------------------------------------------------------


def _sample_blueprint() -> Blueprint:
    """Minimal but valid Blueprint for test seeding."""
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
    sf: AdminStateFile,
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
        "slots": {"topic": "sales"},
        "blueprint_json": blueprint.model_dump_json() if blueprint is not None else "",
        "score": 0.93,
    }
    if extra:
        mission.update(extra)
    return save_mission(sf, mission)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_REQUIRED_H2S = [
    "## What this will do",
    "## Resources allocated",
    "## How to run",
    "## How to debug",
]
_REQUIRED_SECTION_KEYS = {"what_it_does", "resources", "how_to_run", "how_to_debug"}


class TestExplainEndpoint:
    def test_explain_returns_markdown_with_required_sections(self, auth_client):
        """Happy-path: all 4 H2 headings appear in markdown; sections dict has 4 keys."""
        tc, sf = auth_client
        _seed_mission(sf, blueprint=_sample_blueprint())

        resp = tc.post("/api/v1/autopilot/missions/test-mission-001/explain")
        assert resp.status_code == 200
        data = resp.json()

        assert "markdown" in data, "Response must have a 'markdown' key"
        assert "sections" in data, "Response must have a 'sections' key"

        markdown = data["markdown"]
        for heading in _REQUIRED_H2S:
            assert heading in markdown, f"Markdown must contain '{heading}'"

        assert set(data["sections"].keys()) == _REQUIRED_SECTION_KEYS

    def test_explain_describes_each_agent_node(self, auth_client):
        """Each agent's role string must appear in the brief."""
        tc, sf = auth_client

        # Build a multi-agent blueprint with distinct roles.
        graph = AgentGraph(
            nodes=(
                Agent(
                    id="researcher",
                    kind=AgentKind.LLM,
                    role="researcher",
                    prompt_ref="prompts/researcher.md",
                    tools=("web_search", "pdf_reader"),
                ),
                Agent(
                    id="summariser",
                    kind=AgentKind.LLM,
                    role="summariser",
                    prompt_ref="prompts/summariser.md",
                ),
                Agent(
                    id="critic",
                    kind=AgentKind.LLM,
                    role="critic",
                    prompt_ref="prompts/critic.md",
                    tools=("fact_checker",),
                ),
            ),
            edges=(("researcher", "summariser"), ("summariser", "critic")),
            entry="researcher",
        )
        bp = Blueprint(
            id="multi-agent-bp",
            version="1.0",
            title="Multi-agent",
            description="Research, summarise, and critique.",
            agent_graph=graph,
            tools_required=("web_search", "pdf_reader", "fact_checker"),
            providers_required=(
                ProviderRequirement(role="backend", capability="reasoning", tier="high"),
            ),
            success_criteria=EvalRef(
                dataset_id="ds-multi",
                metrics=(Metric(name="f1", value=0.8),),
            ),
        )
        _seed_mission(sf, blueprint=bp, mission_id="multi-agent-mission")

        resp = tc.post("/api/v1/autopilot/missions/multi-agent-mission/explain")
        assert resp.status_code == 200
        markdown = resp.json()["markdown"]

        for role in ("researcher", "summariser", "critic"):
            assert role in markdown, f"Brief must mention agent role '{role}'"

    def test_explain_includes_tools_and_providers(self, auth_client):
        """Tool names and provider names/tiers must appear in 'resources' section."""
        tc, sf = auth_client
        _seed_mission(sf, blueprint=_sample_blueprint())

        resp = tc.post("/api/v1/autopilot/missions/test-mission-001/explain")
        assert resp.status_code == 200
        data = resp.json()

        resources = data["sections"]["resources"]
        # Tool name from the sample blueprint.
        assert "web_search" in resources, "resources section must mention 'web_search'"
        # Provider role and tier from the sample blueprint.
        assert "primary" in resources, "resources section must mention provider role 'primary'"
        assert "medium" in resources, "resources section must mention provider tier 'medium'"

    def test_explain_404_for_unknown_mission(self, auth_client):
        """Non-existent mission ID returns 404."""
        tc, _ = auth_client
        resp = tc.post("/api/v1/autopilot/missions/does-not-exist/explain")
        assert resp.status_code == 404

    def test_explain_unauthenticated_returns_401(self, client):
        """Requests without auth are rejected with 401."""
        resp = client.post("/api/v1/autopilot/missions/any-id/explain")
        assert resp.status_code == 401
