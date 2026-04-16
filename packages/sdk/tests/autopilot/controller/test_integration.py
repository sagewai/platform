# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Integration tests — full lifecycle using real Mission + MissionDriver.

These tests avoid mocking the Mission or MissionDriver and instead wire
up the full path from an AutoRouted result through to a completed
MissionRunResult.  The router itself is still mocked because it requires
a live SagewaiLLMClient.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.autopilot._types import MissionState
from sagewai.autopilot.controller.controller import AutopilotController
from sagewai.autopilot.controller.types import ControllerConfig, MissionRunResult
from sagewai.autopilot.routing.types import AutoRouted, RankedBlueprint
from tests.autopilot.fixtures import (
    make_synthetic_batch_blueprint,
    make_synthetic_scheduled_blueprint,
)


def _make_auto_routed_from(bp, extra_slots: dict | None = None) -> AutoRouted:
    bp_json = bp.model_dump_json()
    ranked = RankedBlueprint(blueprint_json=bp_json, score=0.94)
    slots: dict = {
        "vendors": ["https://example.com"],
        "schedule": "0 9 * * 1-5",
    }
    if extra_slots:
        slots.update(extra_slots)
    return AutoRouted(
        ranked=ranked,
        slots=slots,
        preview="Plan: scheduled research",
    )


def _make_ctrl(bp, extra_slots: dict | None = None) -> tuple[AutopilotController, AutoRouted]:
    auto_routed = _make_auto_routed_from(bp, extra_slots)
    mock_router = MagicMock()
    mock_router.route = AsyncMock(return_value=auto_routed)
    config = ControllerConfig(project_id="integration-test")
    ctrl = AutopilotController(
        router=mock_router,
        client=MagicMock(),
        config=config,
    )
    return ctrl, auto_routed


# ── scheduled blueprint (linear graph) ─────────────────────────────


@pytest.mark.asyncio
async def test_full_lifecycle_scheduled_blueprint():
    bp = make_synthetic_scheduled_blueprint()
    ctrl, _ = _make_ctrl(bp)

    routing_result = await ctrl.start_mission("research AI vendors daily")
    assert routing_result.kind == "auto_routed"
    mission = routing_result.mission
    assert mission.state == MissionState.APPROVED

    await ctrl.approve_and_schedule(mission)
    assert mission.state == MissionState.SCHEDULED

    run_result = await ctrl.run_mission(mission)
    assert isinstance(run_result, MissionRunResult)
    assert run_result.status == "completed"
    assert mission.state == MissionState.COMPLETED


@pytest.mark.asyncio
async def test_full_lifecycle_produces_correct_step_count():
    bp = make_synthetic_scheduled_blueprint()
    ctrl, _ = _make_ctrl(bp)

    result = await ctrl.start_mission("research AI vendors daily")
    mission = result.mission
    await ctrl.approve_and_schedule(mission)
    run_result = await ctrl.run_mission(mission)

    # scheduled blueprint: scout → summarizer = 2 nodes
    assert len(run_result.steps) == 2


@pytest.mark.asyncio
async def test_full_lifecycle_mission_id_stable():
    bp = make_synthetic_scheduled_blueprint()
    ctrl, _ = _make_ctrl(bp)

    result = await ctrl.start_mission("research AI vendors daily")
    mission = result.mission
    mid = mission.mission_id
    await ctrl.approve_and_schedule(mission)
    run_result = await ctrl.run_mission(mission)

    assert run_result.mission_id == mid


@pytest.mark.asyncio
async def test_full_lifecycle_duration_positive():
    bp = make_synthetic_scheduled_blueprint()
    ctrl, _ = _make_ctrl(bp)

    result = await ctrl.start_mission("research AI vendors daily")
    mission = result.mission
    await ctrl.approve_and_schedule(mission)
    run_result = await ctrl.run_mission(mission)

    assert run_result.duration_seconds >= 0.0


# ── batch blueprint (branched graph — stub path) ────────────────────


@pytest.mark.asyncio
async def test_full_lifecycle_branched_blueprint_completes(caplog):
    import logging

    bp = make_synthetic_batch_blueprint()
    auto_routed = AutoRouted(
        ranked=RankedBlueprint(blueprint_json=bp.model_dump_json(), score=0.91),
        slots={
            "extraction_schema": {"type": "object"},
        },
        preview="Batch plan",
    )
    mock_router = MagicMock()
    mock_router.route = AsyncMock(return_value=auto_routed)
    config = ControllerConfig(project_id="integration-test")
    ctrl = AutopilotController(
        router=mock_router,
        client=MagicMock(),
        config=config,
    )

    result = await ctrl.start_mission("process documents from S3")
    mission = result.mission
    await ctrl.approve_and_schedule(mission)

    with caplog.at_level(logging.WARNING, logger="sagewai.autopilot.controller.driver"):
        run_result = await ctrl.run_mission(mission)

    assert run_result.status == "completed"
    assert "branch" in caplog.text.lower()


# ── cannot re-run a completed mission ──────────────────────────────


@pytest.mark.asyncio
async def test_cannot_run_completed_mission_again():
    from sagewai.autopilot.errors import MissionLifecycleError

    bp = make_synthetic_scheduled_blueprint()
    ctrl, _ = _make_ctrl(bp)

    result = await ctrl.start_mission("research AI vendors daily")
    mission = result.mission
    await ctrl.approve_and_schedule(mission)
    await ctrl.run_mission(mission)
    assert mission.state == MissionState.COMPLETED

    # Attempt to run again — driver requires SCHEDULED state
    with pytest.raises(MissionLifecycleError):
        await ctrl.run_mission(mission)


# ── sequential missions are independent ────────────────────────────


@pytest.mark.asyncio
async def test_two_sequential_missions_are_independent():
    bp = make_synthetic_scheduled_blueprint()
    ctrl, _ = _make_ctrl(bp)

    # Mission 1
    r1 = await ctrl.start_mission("goal one")
    m1 = r1.mission
    await ctrl.approve_and_schedule(m1)
    res1 = await ctrl.run_mission(m1)

    # Mock router returns fresh AutoRouted for second call
    auto_routed2 = _make_auto_routed_from(bp)
    ctrl._router.route = AsyncMock(return_value=auto_routed2)

    # Mission 2
    r2 = await ctrl.start_mission("goal two")
    m2 = r2.mission
    await ctrl.approve_and_schedule(m2)
    res2 = await ctrl.run_mission(m2)

    assert m1.mission_id != m2.mission_id
    assert res1.status == "completed"
    assert res2.status == "completed"
    assert m1.state == MissionState.COMPLETED
    assert m2.state == MissionState.COMPLETED
