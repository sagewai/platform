"""Tests for agent admin controls — pause, resume, cancel, config update."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from sagewai.admin.controller import (
    AgentCancelledError,
    RunController,
    RunControlRegistry,
)

# ---------------------------------------------------------------------------
# RunController unit tests
# ---------------------------------------------------------------------------


class TestRunController:
    def test_initial_state(self):
        """Controller starts unpaused and uncancelled."""
        ctrl = RunController()
        assert not ctrl.is_paused
        assert not ctrl.is_cancelled

    @pytest.mark.asyncio
    async def test_checkpoint_passes_normally(self):
        """Checkpoint returns immediately when not paused or cancelled."""
        ctrl = RunController()
        await ctrl.checkpoint()  # Should not block or raise

    @pytest.mark.asyncio
    async def test_cancel_raises_on_checkpoint(self):
        """Checkpoint raises AgentCancelledError after cancel()."""
        ctrl = RunController()
        ctrl.cancel()
        assert ctrl.is_cancelled
        with pytest.raises(AgentCancelledError):
            await ctrl.checkpoint()

    @pytest.mark.asyncio
    async def test_pause_blocks_checkpoint(self):
        """Checkpoint blocks when paused, resumes when unpaused."""
        ctrl = RunController()
        ctrl.pause()
        assert ctrl.is_paused

        resumed = False

        async def wait_then_resume():
            nonlocal resumed
            await asyncio.sleep(0.05)
            ctrl.resume()
            resumed = True

        # Start resume task and checkpoint concurrently
        await asyncio.gather(wait_then_resume(), ctrl.checkpoint())
        assert resumed
        assert not ctrl.is_paused

    @pytest.mark.asyncio
    async def test_cancel_while_paused(self):
        """Cancel unblocks a paused checkpoint and raises."""
        ctrl = RunController()
        ctrl.pause()

        async def cancel_after_delay():
            await asyncio.sleep(0.05)
            ctrl.cancel()

        with pytest.raises(AgentCancelledError):
            await asyncio.gather(cancel_after_delay(), ctrl.checkpoint())

    def test_pause_resume_cycle(self):
        """Can pause and resume multiple times."""
        ctrl = RunController()
        ctrl.pause()
        assert ctrl.is_paused
        ctrl.resume()
        assert not ctrl.is_paused
        ctrl.pause()
        assert ctrl.is_paused
        ctrl.resume()
        assert not ctrl.is_paused


# ---------------------------------------------------------------------------
# RunControlRegistry unit tests
# ---------------------------------------------------------------------------


class TestRunControlRegistry:
    def test_register_and_get(self):
        reg = RunControlRegistry()
        ctrl = RunController()
        reg.register("run-1", ctrl)
        assert reg.get("run-1") is ctrl

    def test_get_missing(self):
        reg = RunControlRegistry()
        assert reg.get("nonexistent") is None

    def test_unregister(self):
        reg = RunControlRegistry()
        ctrl = RunController()
        reg.register("run-1", ctrl)
        reg.unregister("run-1")
        assert reg.get("run-1") is None

    def test_list_active(self):
        reg = RunControlRegistry()
        reg.register("run-1", RunController())
        reg.register("run-2", RunController())
        assert sorted(reg.list_active()) == ["run-1", "run-2"]

    def test_unregister_missing(self):
        """Unregistering a missing run doesn't raise."""
        reg = RunControlRegistry()
        reg.unregister("nonexistent")  # Should not raise


# ---------------------------------------------------------------------------
# Admin API control endpoint tests
# ---------------------------------------------------------------------------


class TestAdminControlEndpoints:
    def _make_client(self, run_controls=None, registry=None):
        from fastapi import FastAPI

        from sagewai.admin import AdminState, create_admin_router

        state = AdminState()
        app = FastAPI()
        app.include_router(
            create_admin_router(state, registry=registry, run_controls=run_controls),
            prefix="/admin",
        )
        return TestClient(app, raise_server_exceptions=False)

    def test_pause_run(self):
        reg = RunControlRegistry()
        ctrl = RunController()
        reg.register("run-1", ctrl)

        client = self._make_client(run_controls=reg)
        resp = client.post("/admin/runs/run-1/pause")
        assert resp.status_code == 200
        assert resp.json()["action"] == "pause"
        assert resp.json()["status"] == "paused"
        assert ctrl.is_paused

    def test_resume_run(self):
        reg = RunControlRegistry()
        ctrl = RunController()
        ctrl.pause()
        reg.register("run-1", ctrl)

        client = self._make_client(run_controls=reg)
        resp = client.post("/admin/runs/run-1/resume")
        assert resp.status_code == 200
        assert resp.json()["action"] == "resume"
        assert not ctrl.is_paused

    def test_cancel_run(self):
        reg = RunControlRegistry()
        ctrl = RunController()
        reg.register("run-1", ctrl)

        client = self._make_client(run_controls=reg)
        resp = client.post("/admin/runs/run-1/cancel")
        assert resp.status_code == 200
        assert resp.json()["action"] == "cancel"
        assert ctrl.is_cancelled

    def test_pause_missing_run(self):
        reg = RunControlRegistry()
        client = self._make_client(run_controls=reg)
        resp = client.post("/admin/runs/nonexistent/pause")
        assert resp.status_code == 404

    def test_resume_cancelled_run(self):
        reg = RunControlRegistry()
        ctrl = RunController()
        ctrl.cancel()
        reg.register("run-1", ctrl)

        client = self._make_client(run_controls=reg)
        resp = client.post("/admin/runs/run-1/resume")
        assert resp.status_code == 409

    def test_controls_not_configured(self):
        client = self._make_client(run_controls=None)
        resp = client.post("/admin/runs/run-1/pause")
        assert resp.status_code == 501


# ---------------------------------------------------------------------------
# Config update endpoint tests
# ---------------------------------------------------------------------------


class TestConfigUpdateEndpoint:
    def _make_client_with_registry(self):
        from fastapi import FastAPI

        from sagewai.admin import AdminState, create_admin_router

        state = AdminState()
        mock_registry = MagicMock()
        mock_agent = MagicMock()
        mock_agent.config = MagicMock()
        mock_agent.config.model = "gpt-4o"
        mock_agent.config.inference = MagicMock()
        mock_agent.config.inference.temperature = 0.7
        mock_agent.config.inference.max_tokens = None
        mock_agent.config.max_iterations = 10
        mock_agent.config.system_prompt = "You are a helper"
        mock_registry.get.return_value = mock_agent
        mock_registry.list_agents.return_value = {"test-agent": ["chat"]}

        app = FastAPI()
        app.include_router(
            create_admin_router(state, registry=mock_registry),
            prefix="/admin",
        )
        return TestClient(app, raise_server_exceptions=False), mock_agent

    def test_update_model(self):
        client, agent = self._make_client_with_registry()
        resp = client.patch(
            "/admin/agents/test-agent/config",
            json={"model": "claude-sonnet-4-20250514"},
        )
        assert resp.status_code == 200
        assert resp.json()["updated"]["model"] == "claude-sonnet-4-20250514"
        assert agent.config.model == "claude-sonnet-4-20250514"

    def test_update_temperature(self):
        client, agent = self._make_client_with_registry()
        resp = client.patch(
            "/admin/agents/test-agent/config",
            json={"temperature": 0.3},
        )
        assert resp.status_code == 200
        assert resp.json()["updated"]["temperature"] == 0.3

    def test_update_max_iterations(self):
        client, agent = self._make_client_with_registry()
        resp = client.patch(
            "/admin/agents/test-agent/config",
            json={"max_iterations": 20},
        )
        assert resp.status_code == 200
        assert resp.json()["updated"]["max_iterations"] == 20

    def test_update_no_fields(self):
        client, _ = self._make_client_with_registry()
        resp = client.patch("/admin/agents/test-agent/config", json={})
        assert resp.status_code == 400

    def test_update_agent_not_found(self):
        from fastapi import FastAPI

        from sagewai.admin import AdminState, create_admin_router

        state = AdminState()
        mock_registry = MagicMock()
        mock_registry.get.return_value = None
        app = FastAPI()
        app.include_router(
            create_admin_router(state, registry=mock_registry),
            prefix="/admin",
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.patch(
            "/admin/agents/nonexistent/config",
            json={"model": "gpt-4o"},
        )
        assert resp.status_code == 404

    def test_update_no_registry(self):
        from fastapi import FastAPI

        from sagewai.admin import AdminState, create_admin_router

        state = AdminState()
        app = FastAPI()
        app.include_router(create_admin_router(state), prefix="/admin")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.patch(
            "/admin/agents/test/config",
            json={"model": "gpt-4o"},
        )
        assert resp.status_code == 404
