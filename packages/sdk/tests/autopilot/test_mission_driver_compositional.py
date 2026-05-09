# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for MissionDriver composition materialisation via injected resolver."""

from __future__ import annotations

import pytest

from sagewai.autopilot._types import AgentKind, Mode
from sagewai.autopilot.agent_graph import Agent, AgentGraph
from sagewai.autopilot.blueprint import Blueprint, CompositionStep
from sagewai.autopilot.controller.driver import MissionDriver
from sagewai.autopilot.models import EvalRef


def _make_resolved_graph() -> AgentGraph:
    return AgentGraph(
        nodes=(Agent(id="triage", kind=AgentKind.DETERMINISTIC),),
        edges=(),
        entry="triage",
    )


def _bp_compositional() -> Blueprint:
    return Blueprint.model_validate({
        "id": "test-compositional",
        "version": "1.1.0",
        "title": "compositional test",
        "composition": [{"pattern": "ticket_triage", "inputs": {}}],
        "example_goals": [],
        "required_slots": {},
        "optional_slots": {},
        "success_criteria": {"metrics": []},
        "training_data_hooks": [],
    })


def _bp_v1() -> Blueprint:
    return Blueprint(
        id="test-v1",
        version="1.0.0",
        title="v1 test",
        category="test",
        mode=Mode.EVENT_DRIVEN,
        providers_required=(),
        agent_graph=AgentGraph(
            nodes=(Agent(id="only", kind=AgentKind.DETERMINISTIC),),
            edges=(),
            entry="only",
        ),
        success_criteria=EvalRef(dataset_id="eval", metrics=()),
    )


def test_compositional_blueprint_resolved_before_run():
    bp = _bp_compositional()
    resolved_graph = _make_resolved_graph()
    calls = {"n": 0}

    def fake_resolver(composition):
        calls["n"] += 1
        return resolved_graph

    md = MissionDriver(blueprint=bp, resolver=fake_resolver)
    md._materialise_if_needed()
    assert calls["n"] == 1
    assert md.blueprint.agent_graph == resolved_graph


def test_compositional_blueprint_without_resolver_raises():
    bp = _bp_compositional()
    md = MissionDriver(blueprint=bp, resolver=None)
    with pytest.raises(RuntimeError, match="resolver"):
        md._materialise_if_needed()


def test_v1_blueprint_does_not_call_resolver():
    bp = _bp_v1()
    calls = {"n": 0}

    def fake_resolver(composition):
        calls["n"] += 1
        return _make_resolved_graph()

    md = MissionDriver(blueprint=bp, resolver=fake_resolver)
    md._materialise_if_needed()
    assert calls["n"] == 0


def test_materialise_is_idempotent():
    bp = _bp_compositional()
    resolved_graph = _make_resolved_graph()
    calls = {"n": 0}

    def fake_resolver(composition):
        calls["n"] += 1
        return resolved_graph

    md = MissionDriver(blueprint=bp, resolver=fake_resolver)
    md._materialise_if_needed()
    md._materialise_if_needed()
    assert calls["n"] == 1


def test_materialise_dict_resolver_accepted():
    """Resolver may return a dict (as sagewai_llm does); driver wraps it in AgentGraph."""
    bp = _bp_compositional()

    def dict_resolver(composition):
        return {
            "nodes": [{"id": "triage", "kind": "deterministic"}],
            "edges": [],
            "entry": "triage",
        }

    md = MissionDriver(blueprint=bp, resolver=dict_resolver)
    md._materialise_if_needed()
    assert md.blueprint.agent_graph is not None
    assert md.blueprint.agent_graph.nodes[0].id == "triage"
