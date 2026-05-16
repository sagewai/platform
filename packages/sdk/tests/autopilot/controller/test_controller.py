# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for AutopilotController — start_mission, approve_and_schedule, run_mission."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.autopilot._types import MissionState
from sagewai.autopilot.controller.controller import AutopilotController
from sagewai.autopilot.controller.types import ControllerConfig, MissionRunResult
from sagewai.autopilot.routing.types import (
    AutoRouted,
    PickerNeeded,
    RankedBlueprint,
    SynthesisNeeded,
)
from tests.autopilot.fixtures import make_synthetic_scheduled_blueprint

# ── helpers ─────────────────────────────────────────────────────────


def _make_ranked(score: float = 0.92) -> RankedBlueprint:
    bp = make_synthetic_scheduled_blueprint()
    return RankedBlueprint(blueprint_json=bp.model_dump_json(), score=score)


def _make_auto_routed() -> AutoRouted:
    ranked = _make_ranked(0.92)
    return AutoRouted(
        ranked=ranked,
        slots={
            "vendors": ["https://example.com"],
            "schedule": "0 9 * * 1-5",
        },
        preview="Plan: research vendors\n- slot vendors = ...",
    )


def _make_controller() -> AutopilotController:
    mock_router = MagicMock()
    mock_client = MagicMock()
    config = ControllerConfig(project_id="test-project")
    return AutopilotController(
        router=mock_router,
        client=mock_client,
        config=config,
    )


# ── construction ─────────────────────────────────────────────────────


def test_controller_stores_config():
    ctrl = _make_controller()
    assert ctrl.config.project_id == "test-project"


def test_controller_has_driver():
    from sagewai.autopilot.controller.driver import MissionDriver

    ctrl = _make_controller()
    assert isinstance(ctrl.driver, MissionDriver)


def test_controller_accepts_custom_driver():
    from sagewai.autopilot.controller.driver import MissionDriver

    custom_driver = MissionDriver()
    mock_router = MagicMock()
    mock_client = MagicMock()
    ctrl = AutopilotController(
        router=mock_router,
        client=mock_client,
        driver=custom_driver,
    )
    assert ctrl.driver is custom_driver


# ── start_mission ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_mission_calls_router():
    ctrl = _make_controller()
    auto_routed = _make_auto_routed()
    ctrl._router.route = AsyncMock(return_value=auto_routed)

    result = await ctrl.start_mission("research AI vendors daily")

    ctrl._router.route.assert_awaited_once_with("research AI vendors daily")
    assert result is auto_routed


@pytest.mark.asyncio
async def test_start_mission_returns_routing_result_auto_routed():
    ctrl = _make_controller()
    auto_routed = _make_auto_routed()
    ctrl._router.route = AsyncMock(return_value=auto_routed)

    result = await ctrl.start_mission("research AI vendors")
    assert result.kind == "auto_routed"


@pytest.mark.asyncio
async def test_start_mission_returns_picker_needed():
    ctrl = _make_controller()
    picker = PickerNeeded(candidates=(_make_ranked(0.75), _make_ranked(0.70)))
    ctrl._router.route = AsyncMock(return_value=picker)

    result = await ctrl.start_mission("do something ambiguous")
    assert result.kind == "picker_needed"


@pytest.mark.asyncio
async def test_start_mission_returns_synthesis_needed():
    ctrl = _make_controller()
    synthesis = SynthesisNeeded(goal="invent a new workflow")
    ctrl._router.route = AsyncMock(return_value=synthesis)

    result = await ctrl.start_mission("invent a new workflow")
    assert result.kind == "synthesis_needed"


@pytest.mark.asyncio
async def test_start_mission_creates_mission_and_approves_when_auto_routed():
    ctrl = _make_controller()
    auto_routed = _make_auto_routed()
    ctrl._router.route = AsyncMock(return_value=auto_routed)

    result = await ctrl.start_mission("research AI vendors")
    assert result.kind == "auto_routed"
    assert hasattr(result, "mission")
    assert result.mission.state == MissionState.APPROVED


# ── approve_and_schedule ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_and_schedule_transitions_to_scheduled(stub_mission):
    ctrl = _make_controller()
    stub_mission.transition_to(MissionState.APPROVED)

    returned = await ctrl.approve_and_schedule(stub_mission)

    assert returned.state == MissionState.SCHEDULED
    assert returned is stub_mission


@pytest.mark.asyncio
async def test_approve_and_schedule_requires_approved_state(stub_mission):
    ctrl = _make_controller()
    # Mission is DRAFT — should raise MissionLifecycleError
    from sagewai.autopilot.errors import MissionLifecycleError

    with pytest.raises(MissionLifecycleError):
        await ctrl.approve_and_schedule(stub_mission)


@pytest.mark.asyncio
async def test_approve_and_schedule_idempotent_for_scheduled_back_to_approved(stub_mission):
    ctrl = _make_controller()
    stub_mission.transition_to(MissionState.APPROVED)
    stub_mission.transition_to(MissionState.SCHEDULED)
    # SCHEDULED → APPROVED is allowed per transition table
    stub_mission.transition_to(MissionState.APPROVED)
    returned = await ctrl.approve_and_schedule(stub_mission)
    assert returned.state == MissionState.SCHEDULED


# ── run_mission ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_mission_delegates_to_driver(stub_mission):
    ctrl = _make_controller()
    stub_mission.transition_to(MissionState.APPROVED)
    stub_mission.transition_to(MissionState.SCHEDULED)

    result = await ctrl.run_mission(stub_mission)

    assert isinstance(result, MissionRunResult)
    assert result.mission_id == stub_mission.mission_id


@pytest.mark.asyncio
async def test_run_mission_result_is_completed(stub_mission):
    ctrl = _make_controller()
    stub_mission.transition_to(MissionState.APPROVED)
    stub_mission.transition_to(MissionState.SCHEDULED)

    result = await ctrl.run_mission(stub_mission)
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_run_mission_steps_match_graph(stub_mission):
    ctrl = _make_controller()
    stub_mission.transition_to(MissionState.APPROVED)
    stub_mission.transition_to(MissionState.SCHEDULED)

    result = await ctrl.run_mission(stub_mission)
    # scheduled blueprint has 2 nodes: scout, summarizer
    assert len(result.steps) == 2
    assert result.steps[0].node_id == "scout"
    assert result.steps[1].node_id == "summarizer"


@pytest.mark.asyncio
async def test_run_mission_requires_scheduled_state(stub_mission):
    ctrl = _make_controller()
    # stub_mission is DRAFT
    from sagewai.autopilot.errors import MissionLifecycleError

    with pytest.raises(MissionLifecycleError):
        await ctrl.run_mission(stub_mission)


@pytest.mark.asyncio
async def test_run_mission_end_to_end_no_router(stub_mission):
    """Full path: approve + schedule + run without touching router."""
    ctrl = _make_controller()
    stub_mission.transition_to(MissionState.APPROVED)
    await ctrl.approve_and_schedule(stub_mission)
    result = await ctrl.run_mission(stub_mission)
    assert result.status == "completed"
    assert stub_mission.state == MissionState.COMPLETED
