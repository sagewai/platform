# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for admin API — agents, runs, sessions endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin.api import create_admin_router
from sagewai.admin.models import (
    AgentDetail,
    AgentSummary,
    RunDetail,
    RunSummary,
    SessionInfo,
    StepInfo,
    ToolCallRecord,
)
from sagewai.admin.state import AdminState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def state():
    return AdminState()


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    config = MagicMock()
    config.name = "test-agent"
    config.model = "gpt-4o"
    config.system_prompt = "You are a test agent"
    config.max_iterations = 10
    config.tools = []

    agent = MagicMock()
    agent.config = config

    registry.list_agents.return_value = {"test-agent": ["research", "search"]}
    registry.get.return_value = agent
    return registry


@pytest.fixture
def client(state, mock_registry):
    app = FastAPI()
    app.include_router(create_admin_router(state, mock_registry), prefix="/admin")
    return TestClient(app)


@pytest.fixture
def client_no_registry(state):
    app = FastAPI()
    app.include_router(create_admin_router(state), prefix="/admin")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TestModels:
    def test_agent_summary(self):
        s = AgentSummary(name="agent", capabilities=["a", "b"])
        assert s.name == "agent"
        assert len(s.capabilities) == 2

    def test_agent_detail(self):
        d = AgentDetail(name="agent", model="gpt-4o", total_runs=5)
        assert d.total_runs == 5

    def test_run_summary(self):
        r = RunSummary(run_id="abc", agent_name="agent")
        assert r.run_id == "abc"

    def test_run_detail(self):
        r = RunDetail(
            run_id="abc",
            agent_name="agent",
            tool_calls=[ToolCallRecord(tool_name="search")],
            steps=[StepInfo(step_type="llm_call")],
        )
        assert len(r.tool_calls) == 1
        assert len(r.steps) == 1

    def test_session_info(self):
        s = SessionInfo(session_id="s1", agent_name="agent", started_at=1000.0)
        assert s.status == "active"


# ---------------------------------------------------------------------------
# AdminState
# ---------------------------------------------------------------------------


class TestAdminState:
    def test_record_run(self, state):
        run_id = state.record_run(
            agent_name="agent",
            input_text="hello",
            output_text="world",
            total_tokens=100,
        )
        assert len(run_id) == 12
        assert state.total_runs == 1

    def test_get_run(self, state):
        run_id = state.record_run(agent_name="agent", input_text="test")
        run = state.get_run(run_id)
        assert run is not None
        assert run.agent_name == "agent"

    def test_get_run_not_found(self, state):
        assert state.get_run("nonexistent") is None

    def test_list_runs_with_filter(self, state):
        state.record_run(agent_name="agent1", status="completed")
        state.record_run(agent_name="agent2", status="failed")
        state.record_run(agent_name="agent1", status="failed")

        all_runs = state.list_runs()
        assert len(all_runs) == 3

        agent1_runs = state.list_runs(agent_name="agent1")
        assert len(agent1_runs) == 2

        failed_runs = state.list_runs(status="failed")
        assert len(failed_runs) == 2

    def test_list_runs_pagination(self, state):
        for i in range(10):
            state.record_run(agent_name="agent", input_text=str(i))

        page1 = state.list_runs(limit=3, offset=0)
        page2 = state.list_runs(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3

    def test_run_eviction(self):
        state = AdminState(max_runs=3)
        for i in range(5):
            state.record_run(agent_name="agent", input_text=str(i))
        assert state.total_runs == 3

    def test_agent_run_count(self, state):
        state.record_run(agent_name="agent1")
        state.record_run(agent_name="agent1")
        state.record_run(agent_name="agent2")
        assert state.get_agent_run_count("agent1") == 2
        assert state.get_agent_run_count("agent2") == 1
        assert state.get_agent_run_count("nonexistent") == 0

    def test_record_run_with_tool_calls(self, state):
        run_id = state.record_run(
            agent_name="agent",
            tool_calls=[{"tool_name": "search", "arguments": '{"q": "test"}', "duration_ms": 50}],
        )
        run = state.get_run(run_id)
        assert len(run.tool_calls) == 1
        assert run.tool_calls[0].tool_name == "search"

    def test_record_run_with_steps(self, state):
        run_id = state.record_run(
            agent_name="agent",
            steps=[{"step_type": "llm_call", "detail": "gpt-4o", "duration_ms": 200}],
        )
        run = state.get_run(run_id)
        assert len(run.steps) == 1

    def test_start_session(self, state):
        sid = state.start_session("agent")
        assert len(sid) == 12
        assert state.active_sessions == 1

    def test_update_session(self, state):
        sid = state.start_session("agent")
        state.update_session(sid, message_count=5)
        session = state.get_session(sid)
        assert session.message_count == 5

    def test_end_session(self, state):
        sid = state.start_session("agent")
        state.end_session(sid)
        assert state.active_sessions == 0

    def test_list_sessions(self, state):
        state.start_session("agent1")
        state.start_session("agent2")
        assert len(state.list_sessions()) == 2

    def test_clear(self, state):
        state.record_run(agent_name="agent")
        state.start_session("agent")
        state.clear()
        assert state.total_runs == 0
        assert state.active_sessions == 0


# ---------------------------------------------------------------------------
# API — Agents
# ---------------------------------------------------------------------------


class TestAgentEndpoints:
    def test_list_agents(self, client):
        resp = client.get("/admin/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "test-agent"
        assert "research" in data[0]["capabilities"]

    def test_list_agents_no_registry(self, client_no_registry):
        resp = client_no_registry.get("/admin/agents")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_agent(self, client):
        resp = client.get("/admin/agents/test-agent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-agent"
        assert data["model"] == "gpt-4o"

    def test_get_agent_not_found(self, client, mock_registry):
        mock_registry.get.return_value = None
        resp = client.get("/admin/agents/nonexistent")
        assert resp.status_code == 404

    def test_get_agent_no_registry(self, client_no_registry):
        resp = client_no_registry.get("/admin/agents/any")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API — Runs
# ---------------------------------------------------------------------------


class TestRunEndpoints:
    def test_list_runs_empty(self, client):
        resp = client.get("/admin/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["has_more"] is False

    def test_list_runs(self, client, state):
        state.record_run(agent_name="agent", input_text="hello")
        resp = client.get("/admin/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["agent_name"] == "agent"

    def test_list_runs_filtered(self, client, state):
        state.record_run(agent_name="agent1")
        state.record_run(agent_name="agent2")
        resp = client.get("/admin/runs?agent_name=agent1")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    def test_get_run(self, client, state):
        run_id = state.record_run(agent_name="agent", input_text="test")
        resp = client.get(f"/admin/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["input_text"] == "test"

    def test_get_run_not_found(self, client):
        resp = client.get("/admin/runs/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API — Sessions
# ---------------------------------------------------------------------------


class TestSessionEndpoints:
    def test_list_sessions_empty(self, client):
        resp = client.get("/admin/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["has_more"] is False

    def test_list_sessions(self, client, state):
        state.start_session("agent")
        resp = client.get("/admin/sessions")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    def test_get_session(self, client, state):
        sid = state.start_session("agent")
        resp = client.get(f"/admin/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["agent_name"] == "agent"

    def test_get_session_not_found(self, client):
        resp = client.get("/admin/sessions/nonexistent")
        assert resp.status_code == 404
