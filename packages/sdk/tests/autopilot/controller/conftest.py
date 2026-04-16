# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Shared fixtures for the controller test suite."""

from __future__ import annotations

import pytest

from sagewai.autopilot._types import AgentKind, Mode
from sagewai.autopilot.agent_graph import Agent, AgentGraph
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.controller.driver import MissionDriver
from sagewai.autopilot.mission import Mission
from sagewai.autopilot.models import EvalRef, Metric, ProviderRequirement
from tests.autopilot.fixtures import (
    make_synthetic_batch_blueprint,
    make_synthetic_scheduled_blueprint,
)


def _make_mission_from_blueprint(bp: Blueprint, mission_id: str = "ms-test-abc123") -> Mission:
    """Create a DRAFT mission with blueprint JSON stored in slots."""
    slots: dict = {}
    for name, spec in bp.required_slots.items():
        if spec.default is not None:
            slots[name] = spec.default
        elif "list" in spec.type_:
            slots[name] = ["https://example.com"]
        elif spec.type_ == "cron":
            slots[name] = "0 9 * * 1-5"
        elif spec.type_ == "JsonSchema":
            slots[name] = '{"type": "object"}'
        else:
            slots[name] = "stub-value"

    # Inject the blueprint JSON so MissionDriver can load the graph
    slots["__blueprint_json__"] = bp.model_dump_json()

    return Mission(
        mission_id=mission_id,
        project_id="test-project",
        blueprint_id=bp.id,
        blueprint_version=bp.version,
        slots=slots,
    )


def _advance_to_scheduled(mission: Mission) -> Mission:
    """Transition a DRAFT mission through APPROVED to SCHEDULED."""
    from sagewai.autopilot._types import MissionState

    mission.transition_to(MissionState.APPROVED)
    mission.transition_to(MissionState.SCHEDULED)
    return mission


@pytest.fixture()
def stub_mission() -> Mission:
    """A DRAFT mission backed by the 2-node scheduled blueprint (scout → summarizer)."""
    bp = make_synthetic_scheduled_blueprint()
    return _make_mission_from_blueprint(bp)


@pytest.fixture()
def single_node_mission() -> Mission:
    """A DRAFT mission with a single-node agent graph (no edges)."""
    bp = Blueprint(
        id="SYNTHETIC_single_node",
        version="0.0.1",
        title="SYNTHETIC single node",
        description="Test fixture.",
        category="test",
        mode=Mode.SCHEDULED,
        example_goals=("SYNTHETIC single node test",),
        required_slots={},
        optional_slots={},
        tools_required=(),
        providers_required=(
            ProviderRequirement(role="worker", capability="reasoning", tier="low"),
        ),
        agent_graph=AgentGraph(
            nodes=(Agent(id="only", kind=AgentKind.DETERMINISTIC),),
            edges=(),
            entry="only",
        ),
        success_criteria=EvalRef(
            dataset_id="SYNTHETIC_single_eval",
            metrics=(Metric(name="quality", op=">=", value=3.0),),
        ),
    )
    return Mission(
        mission_id="ms-single-000",
        project_id="test-project",
        blueprint_id=bp.id,
        blueprint_version=bp.version,
        slots={"__blueprint_json__": bp.model_dump_json()},
    )


@pytest.fixture()
def branched_mission() -> Mission:
    """A DRAFT mission backed by the batch blueprint (which has branches)."""
    bp = make_synthetic_batch_blueprint()
    return _make_mission_from_blueprint(bp, mission_id="ms-branched-001")


@pytest.fixture()
def driver() -> MissionDriver:
    return MissionDriver()
