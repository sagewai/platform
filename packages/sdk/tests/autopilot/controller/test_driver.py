# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for MissionDriver — state transitions and AgentGraph walking."""

from __future__ import annotations

import logging

import pytest

from sagewai.autopilot._types import MissionState


def _schedule(mission):
    """Advance a DRAFT mission to SCHEDULED via APPROVED."""
    mission.transition_to(MissionState.APPROVED)
    mission.transition_to(MissionState.SCHEDULED)


# ── state transitions ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_transitions_to_running(driver, stub_mission):
    _schedule(stub_mission)
    result = await driver.execute(stub_mission)
    assert result.status in ("completed", "failed")


@pytest.mark.asyncio
async def test_execute_transitions_to_completed_on_success(driver, stub_mission):
    _schedule(stub_mission)
    result = await driver.execute(stub_mission)
    assert result.status == "completed"
    assert stub_mission.state == MissionState.COMPLETED


@pytest.mark.asyncio
async def test_execute_mission_id_in_result(driver, stub_mission):
    _schedule(stub_mission)
    result = await driver.execute(stub_mission)
    assert result.mission_id == stub_mission.mission_id


@pytest.mark.asyncio
async def test_execute_requires_scheduled_state(driver, stub_mission):
    # Mission is still DRAFT — should raise MissionLifecycleError
    from sagewai.autopilot.errors import MissionLifecycleError

    with pytest.raises(MissionLifecycleError):
        await driver.execute(stub_mission)


@pytest.mark.asyncio
async def test_execute_result_is_frozen(driver, stub_mission):
    _schedule(stub_mission)
    result = await driver.execute(stub_mission)
    with pytest.raises(Exception):
        result.status = "mutated"  # type: ignore[misc]


# ── linear graph walk ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_produces_step_per_node(driver, stub_mission):
    _schedule(stub_mission)
    result = await driver.execute(stub_mission)
    # stub_mission uses the 2-node scheduled blueprint (scout + summarizer)
    assert len(result.steps) == 2


@pytest.mark.asyncio
async def test_execute_step_order_matches_graph(driver, stub_mission):
    _schedule(stub_mission)
    result = await driver.execute(stub_mission)
    assert result.steps[0].node_id == "scout"
    assert result.steps[1].node_id == "summarizer"


@pytest.mark.asyncio
async def test_execute_step_statuses_valid(driver, stub_mission):
    _schedule(stub_mission)
    result = await driver.execute(stub_mission)
    valid_statuses = {"completed", "skipped", "failed"}
    for step in result.steps:
        assert step.status in valid_statuses


@pytest.mark.asyncio
async def test_execute_step_output_preview_is_set(driver, stub_mission):
    _schedule(stub_mission)
    result = await driver.execute(stub_mission)
    for step in result.steps:
        assert step.output_preview is not None


@pytest.mark.asyncio
async def test_execute_duration_is_non_negative(driver, stub_mission):
    _schedule(stub_mission)
    result = await driver.execute(stub_mission)
    assert result.duration_seconds >= 0.0


# ── single-node graph ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_single_node_graph(driver, single_node_mission):
    _schedule(single_node_mission)
    result = await driver.execute(single_node_mission)
    assert result.status == "completed"
    assert len(result.steps) == 1


# ── branched graph ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_branched_graph_logs_warning(driver, branched_mission, caplog):
    _schedule(branched_mission)
    with caplog.at_level(logging.WARNING, logger="sagewai.autopilot.controller.driver"):
        result = await driver.execute(branched_mission)
    assert "branch" in caplog.text.lower()
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_execute_branched_graph_returns_entry_step(driver, branched_mission):
    _schedule(branched_mission)
    result = await driver.execute(branched_mission)
    # Only the entry node is executed as a stub for branched graphs
    assert len(result.steps) == 1
    assert result.steps[0].node_id == "ingestor"


# ── FAILED transition ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_sets_failed_state_on_error(driver, stub_mission, monkeypatch):
    _schedule(stub_mission)

    def _bad_traverse(self_inner):
        raise RuntimeError("simulated traversal failure")

    import sagewai.autopilot.agent_graph as ag_mod

    monkeypatch.setattr(ag_mod.AgentGraph, "traverse_linear", _bad_traverse)

    result = await driver.execute(stub_mission)
    assert result.status == "failed"
    assert stub_mission.state == MissionState.FAILED
    assert result.error is not None
    assert "simulated traversal failure" in result.error


@pytest.mark.asyncio
async def test_execute_no_error_field_on_success(driver, stub_mission):
    _schedule(stub_mission)
    result = await driver.execute(stub_mission)
    assert result.error is None
