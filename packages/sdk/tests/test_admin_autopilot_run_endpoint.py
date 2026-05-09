# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for ``POST /api/v1/autopilot/missions/{id}/run`` — Plan H Task 4.

The run endpoint is the load-bearing background-task spawn point: it
must return 202 within milliseconds and never block the request thread
on driver execution.  These tests cover the happy path, the lifecycle
guards (404 / 409 / auth), and the background pipeline (driver dispatch
→ persist sink → terminal transition).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from sagewai.admin import autopilot_routes
from sagewai.admin import autopilot_run_bus as run_bus_mod
from sagewai.admin.autopilot_routes import create_autopilot_router
from sagewai.admin.autopilot_state import get_mission, save_mission
from sagewai.admin.state_file import AdminStateFile
from sagewai.autopilot._types import AgentKind
from sagewai.autopilot.agent_graph import Agent, AgentGraph
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.controller.types import (
    MissionRunResult,
    StepResult,
    StepTelemetry,
)
from sagewai.autopilot.models import (
    EvalRef,
    Metric,
    ProviderRequirement,
    TrainingHook,
)


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_run_bus_singleton():
    """Reset the process-global :class:`MissionRunBus` between tests.

    Tests reuse mission ids (``run-mission-001``) so a leaked ring
    buffer would cause a fresh persist sink to immediately replay a
    prior test's ``mission.finished`` event and exit early.
    """
    run_bus_mod._BUS_SINGLETON = None
    yield
    run_bus_mod._BUS_SINGLETON = None


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


def _sample_blueprint() -> Blueprint:
    """Minimal but structurally valid blueprint for run-endpoint tests."""
    graph = AgentGraph(
        nodes=(
            Agent(
                id="planner",
                kind=AgentKind.LLM,
                role="planner",
                prompt_ref="prompts/planner.md",
            ),
        ),
        edges=(),
        entry="planner",
    )
    return Blueprint(
        id="run-bp",
        version="1.0",
        title="Run Sample",
        description="Blueprint used by run-endpoint tests.",
        agent_graph=graph,
        tools_required=(),
        providers_required=(
            ProviderRequirement(role="primary", capability="reasoning", tier="medium"),
        ),
        training_data_hooks=(
            TrainingHook(event="planner.completed", dataset="train/run-sample"),
        ),
        success_criteria=EvalRef(
            dataset_id="ds-run",
            metrics=(Metric(name="accuracy", value=0.9),),
        ),
    )


def _seed_pending_mission(
    sf: AdminStateFile,
    *,
    mission_id: str = "run-mission-001",
    blueprint: Blueprint | None = None,
    status: str = "pending",
) -> dict:
    bp = blueprint if blueprint is not None else _sample_blueprint()
    return save_mission(
        sf,
        {
            "mission_id": mission_id,
            "project_id": "proj-a",
            "status": status,
            "created_at": "2026-05-09T10:00:00+00:00",
            "goal_preview": "Plan H run-endpoint test goal.",
            "slots": {"topic": "demo"},
            "blueprint_json": bp.model_dump_json(),
            "score": 0.93,
        },
    )


class FakeDriver:
    """Test stand-in for :class:`MissionDriver`.

    Either returns a canned :class:`MissionRunResult`, raises an
    exception, or sleeps for ``sleep`` seconds before returning the
    canned result.
    """

    def __init__(
        self,
        *,
        result: MissionRunResult | None = None,
        exc: BaseException | None = None,
        sleep: float = 0.0,
    ) -> None:
        self.result = result
        self.exc = exc
        self.sleep = sleep

    async def execute(self, mission: Any) -> MissionRunResult:
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.exc is not None:
            raise self.exc
        assert self.result is not None
        return self.result


def _completed_result(
    *,
    mission_id: str = "run-mission-001",
    cost_usd: float = 0.005,
    output: str = "the answer",
) -> MissionRunResult:
    tel = StepTelemetry(
        cost_usd=cost_usd,
        input_tokens=10,
        output_tokens=5,
        model_used="haiku",
        latency_ms=12.0,
    )
    step = StepResult(
        node_id="planner",
        status="completed",
        output_preview=output,
        output=output,
        telemetry=tel,
    )
    return MissionRunResult(
        mission_id=mission_id,
        status="completed",
        steps=(step,),
        duration_seconds=0.01,
        error=None,
    )


async def _wait_for_status(
    sf: AdminStateFile,
    mission_id: str,
    targets: tuple[str, ...],
    *,
    timeout: float = 2.0,
) -> dict:
    """Poll ``get_mission`` until ``status`` is one of *targets*."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = get_mission(sf, mission_id)
        if rec is not None and rec.get("status") in targets:
            return rec
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"mission '{mission_id}' did not reach {targets} within {timeout}s; "
        f"last record: {get_mission(sf, mission_id)}"
    )


# ── tests ─────────────────────────────────────────────────────────────


async def test_run_returns_202_with_run_id(
    monkeypatch, app_and_sf, auth_headers
):
    """Happy path: 202 + body has run_id (str, prefix 'run_') and started_at."""
    app, sf = app_and_sf
    _seed_pending_mission(sf)

    # Use a slow fake driver so the mission stays "running" long enough
    # for us to inspect the freshly-stamped record without racing the
    # background task to completion.
    monkeypatch.setattr(
        autopilot_routes,
        "_build_mission_driver",
        lambda r, b: FakeDriver(result=_completed_result(), sleep=0.5),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.post(
            "/api/v1/autopilot/missions/run-mission-001/run",
            headers=auth_headers,
        )
    assert resp.status_code == 202
    body = resp.json()
    assert isinstance(body["run_id"], str) and body["run_id"].startswith("run_")
    assert isinstance(body["started_at"], str) and body["started_at"]

    # Record reflects fresh-run state.
    rec = get_mission(sf, "run-mission-001")
    assert rec["status"] == "running"
    assert rec["run_id"] == body["run_id"]
    assert rec["started_at"] == body["started_at"]
    assert rec["trace"] == [] or isinstance(rec["trace"], list)
    assert rec["total_cost_usd"] == 0.0 or isinstance(rec["total_cost_usd"], float)

    # Let the background task drain so it doesn't bleed into the next test.
    await _wait_for_status(sf, "run-mission-001", ("completed", "failed"))


async def test_run_kicks_background_task_that_drives_mission(
    monkeypatch, app_and_sf, auth_headers
):
    """Background task should drive the mission to completion and persist telemetry."""
    app, sf = app_and_sf
    _seed_pending_mission(sf)

    monkeypatch.setattr(
        autopilot_routes,
        "_build_mission_driver",
        lambda r, b: FakeDriver(result=_completed_result(cost_usd=0.005)),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.post(
            "/api/v1/autopilot/missions/run-mission-001/run",
            headers=auth_headers,
        )
    assert resp.status_code == 202

    rec = await _wait_for_status(sf, "run-mission-001", ("completed",))
    assert rec["status"] == "completed"
    assert rec["error"] is None
    assert rec["finished_at"] is not None
    assert rec["step_count"] == 1
    # Cost is summed from agent.llm_call events — 1 step × $0.005.
    assert rec["total_cost_usd"] == pytest.approx(0.005, abs=1e-6)
    assert rec["output"] == "the answer"

    kinds = [e.get("kind") for e in rec.get("trace", [])]
    assert "mission.started" in kinds
    assert "mission.finished" in kinds


async def test_run_404_unknown_mission(app_and_sf, auth_headers):
    app, _sf = app_and_sf
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.post(
            "/api/v1/autopilot/missions/does-not-exist/run",
            headers=auth_headers,
        )
    assert resp.status_code == 404


async def test_run_409_when_already_running(app_and_sf, auth_headers):
    """Seed a mission with status='running' directly — POST returns 409."""
    app, sf = app_and_sf
    _seed_pending_mission(sf, status="running")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.post(
            "/api/v1/autopilot/missions/run-mission-001/run",
            headers=auth_headers,
        )
    assert resp.status_code == 409
    assert "already running" in resp.json()["detail"].lower()


async def test_run_409_when_completed(app_and_sf, auth_headers):
    """Terminal-state missions cannot be re-run."""
    app, sf = app_and_sf
    _seed_pending_mission(sf, status="completed")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.post(
            "/api/v1/autopilot/missions/run-mission-001/run",
            headers=auth_headers,
        )
    assert resp.status_code == 409


async def test_run_handles_driver_exception(
    monkeypatch, app_and_sf, auth_headers
):
    """A driver that raises lands the mission in 'failed' with the error string."""
    app, sf = app_and_sf
    _seed_pending_mission(sf)

    monkeypatch.setattr(
        autopilot_routes,
        "_build_mission_driver",
        lambda r, b: FakeDriver(exc=RuntimeError("boom")),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.post(
            "/api/v1/autopilot/missions/run-mission-001/run",
            headers=auth_headers,
        )
    assert resp.status_code == 202

    rec = await _wait_for_status(sf, "run-mission-001", ("failed",))
    assert rec["status"] == "failed"
    assert rec["error"] is not None and "boom" in rec["error"]
    assert rec["finished_at"] is not None


async def test_run_requires_auth(app_and_sf):
    """Unauthenticated client gets 401."""
    app, sf = app_and_sf
    _seed_pending_mission(sf)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.post("/api/v1/autopilot/missions/run-mission-001/run")
    assert resp.status_code == 401


async def test_run_persists_trace_to_state_file(
    monkeypatch, app_and_sf, auth_headers
):
    """After a successful run, every observer event lands in the trace."""
    app, sf = app_and_sf
    _seed_pending_mission(sf)

    monkeypatch.setattr(
        autopilot_routes,
        "_build_mission_driver",
        lambda r, b: FakeDriver(result=_completed_result()),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.post(
            "/api/v1/autopilot/missions/run-mission-001/run",
            headers=auth_headers,
        )
    assert resp.status_code == 202

    rec = await _wait_for_status(sf, "run-mission-001", ("completed",))
    trace = rec.get("trace") or []
    kinds = [e.get("kind") for e in trace]
    assert kinds[0] == "mission.started"
    assert "mission.finished" in kinds
    assert any(k.startswith("agent.") for k in kinds)
    assert "agent.llm_call" in kinds
    assert rec["last_event_at"] is not None


async def test_run_unblocks_immediately_does_not_await_execution(
    monkeypatch, app_and_sf, auth_headers
):
    """The /run handler must return within ~200ms even when the driver is slow."""
    app, sf = app_and_sf
    _seed_pending_mission(sf)

    # Driver that sleeps 0.5s — the handler must NOT wait for it.
    monkeypatch.setattr(
        autopilot_routes,
        "_build_mission_driver",
        lambda r, b: FakeDriver(result=_completed_result(), sleep=0.5),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        t0 = time.monotonic()
        resp = await ac.post(
            "/api/v1/autopilot/missions/run-mission-001/run",
            headers=auth_headers,
        )
        elapsed = time.monotonic() - t0

    assert resp.status_code == 202
    assert elapsed < 0.2, f"/run blocked for {elapsed:.3f}s — must not await execute()"

    # Drain the background task before exit so async cleanup doesn't warn.
    await _wait_for_status(sf, "run-mission-001", ("completed", "failed"))
