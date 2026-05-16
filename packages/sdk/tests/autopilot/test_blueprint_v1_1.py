# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for Blueprint v1.1 compositional schema (superset of v1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.autopilot.blueprint import Blueprint


def _v1_minimal() -> dict:
    return {
        "id": "test-v1",
        "version": "1.0.0",
        "title": "v1 blueprint",
        "category": "support",
        "mode": "event_driven",
        "providers_required": [
            {"role": "classifier", "capability": "classification", "tier": "small"}
        ],
        "agent_graph": {
            "nodes": [
                {
                    "id": "n1",
                    "role": "triage",
                    "kind": "deterministic",
                }
            ],
            "edges": [],
            "entry": "n1",
        },
        "description": "test v1",
        "example_goals": ["triage tickets"],
        "required_slots": {},
        "optional_slots": {},
        "success_criteria": {"dataset_id": "test-eval", "metrics": []},
        "training_data_hooks": [],
    }


def _v1_1_minimal() -> dict:
    return {
        "id": "test-v1-1",
        "version": "1.1.0",
        "title": "v1.1 blueprint",
        "quality_tier": "curated",
        "composition": [
            {"pattern": "ticket_triage", "inputs": {}, "wraps": None}
        ],
        "example_goals": ["triage tickets cheaply"],
        "required_slots": {},
        "optional_slots": {},
        "success_criteria": {"metrics": []},
        "training_data_hooks": [],
    }


def test_v1_minimal_parses():
    bp = Blueprint.model_validate(_v1_minimal())
    assert bp.agent_graph is not None
    assert bp.composition is None


def test_v1_1_minimal_parses_without_agent_graph():
    bp = Blueprint.model_validate(_v1_1_minimal())
    assert bp.composition is not None
    assert bp.agent_graph is None
    assert bp.category == ""
    assert bp.mode is None
    assert bp.providers_required == ()


def test_both_composition_and_agent_graph_rejected():
    data = _v1_minimal()
    data["composition"] = [{"pattern": "ticket_triage", "inputs": {}}]
    with pytest.raises(ValidationError, match="exactly one"):
        Blueprint.model_validate(data)


def test_neither_composition_nor_agent_graph_rejected():
    data = _v1_minimal()
    data.pop("agent_graph")
    with pytest.raises(ValidationError, match="exactly one"):
        Blueprint.model_validate(data)


def test_empty_composition_with_no_agent_graph_rejected():
    data = _v1_1_minimal()
    data["composition"] = []
    with pytest.raises(ValidationError, match="exactly one"):
        Blueprint.model_validate(data)


def test_quality_tier_defaults_to_curated_for_v1():
    bp = Blueprint.model_validate(_v1_minimal())
    assert bp.quality_tier == "curated"


def test_metric_v1_shape_op_value():
    data = _v1_1_minimal()
    data["success_criteria"] = {
        "metrics": [{"name": "agreement", "op": ">=", "value": 0.85}]
    }
    bp = Blueprint.model_validate(data)
    m = bp.success_criteria.metrics[0]
    assert m.op == ">=" and m.value == 0.85 and m.name == "agreement"


def test_metric_v1_1_shape_name_target_normalised():
    data = _v1_1_minimal()
    data["success_criteria"] = {
        "metrics": [{"name": "agreement", "target": 0.85}]
    }
    bp = Blueprint.model_validate(data)
    m = bp.success_criteria.metrics[0]
    assert m.name == "agreement" and m.op == ">=" and m.value == 0.85


def test_hook_v1_shape_event_dataset():
    data = _v1_1_minimal()
    data["training_data_hooks"] = [
        {"event": "classifier.completed", "dataset": "support-triage", "format": "alpaca"}
    ]
    bp = Blueprint.model_validate(data)
    h = bp.training_data_hooks[0]
    assert h.event == "classifier.completed"
    assert h.dataset == "support-triage"


def test_hook_v1_1_shape_hook_target_normalised():
    data = _v1_1_minimal()
    data["training_data_hooks"] = [
        {
            "hook": "classifier.completed",
            "target": "support-triage",
            "filter": "user_rating >= 4",
        }
    ]
    bp = Blueprint.model_validate(data)
    h = bp.training_data_hooks[0]
    assert h.event == "classifier.completed"
    assert h.dataset == "support-triage"
    assert h.quality_filter == "user_rating >= 4"
