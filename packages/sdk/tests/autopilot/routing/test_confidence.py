"""Tests for the confidence gating module."""

from __future__ import annotations

import pytest

from sagewai.autopilot.routing.confidence import (
    ConfidenceConfig,
    RoutingDecision,
    gate,
)
from sagewai.autopilot.routing.types import RankedBlueprint


def _rb(score: float, idx: int = 0) -> RankedBlueprint:
    return RankedBlueprint(blueprint_json=f'{{"id":"bp-{idx}"}}', score=score)


# ── ConfidenceConfig ───────────────────────────────────────────────


def test_default_thresholds():
    cfg = ConfidenceConfig()
    assert cfg.auto_route_threshold == pytest.approx(0.85)
    assert cfg.picker_threshold == pytest.approx(0.65)
    assert cfg.picker_top_k == 3


def test_custom_thresholds_roundtrip():
    cfg = ConfidenceConfig(auto_route_threshold=0.90, picker_threshold=0.70, picker_top_k=5)
    assert cfg.auto_route_threshold == pytest.approx(0.90)
    assert cfg.picker_threshold == pytest.approx(0.70)
    assert cfg.picker_top_k == 5


def test_auto_threshold_must_be_above_picker_threshold():
    with pytest.raises(Exception):
        ConfidenceConfig(auto_route_threshold=0.60, picker_threshold=0.70)


def test_thresholds_must_be_in_unit_interval():
    with pytest.raises(Exception):
        ConfidenceConfig(auto_route_threshold=1.1, picker_threshold=0.65)
    with pytest.raises(Exception):
        ConfidenceConfig(auto_route_threshold=0.85, picker_threshold=-0.01)


def test_picker_top_k_must_be_positive():
    with pytest.raises(Exception):
        ConfidenceConfig(picker_top_k=0)


# ── gate() — AUTO_ROUTE band ───────────────────────────────────────


def test_gate_auto_route_at_exact_threshold():
    cfg = ConfidenceConfig(auto_route_threshold=0.85, picker_threshold=0.65)
    candidates = (_rb(0.85, 0), _rb(0.70, 1), _rb(0.60, 2))
    decision = gate(candidates, cfg)
    assert decision == RoutingDecision.AUTO_ROUTE


def test_gate_auto_route_above_threshold():
    cfg = ConfidenceConfig()
    candidates = (_rb(0.95, 0), _rb(0.80, 1))
    assert gate(candidates, cfg) == RoutingDecision.AUTO_ROUTE


def test_gate_auto_route_uses_only_top_candidate():
    cfg = ConfidenceConfig(auto_route_threshold=0.85, picker_threshold=0.65)
    # Second candidate is high, but first is below threshold
    candidates = (_rb(0.50, 0), _rb(0.95, 1))
    # gate() checks candidates[0] only
    assert gate(candidates, cfg) != RoutingDecision.AUTO_ROUTE


# ── gate() — PICKER band ───────────────────────────────────────────


def test_gate_picker_in_middle_band():
    cfg = ConfidenceConfig(auto_route_threshold=0.85, picker_threshold=0.65)
    candidates = (_rb(0.75, 0), _rb(0.70, 1), _rb(0.60, 2))
    assert gate(candidates, cfg) == RoutingDecision.PICKER


def test_gate_picker_at_picker_threshold():
    cfg = ConfidenceConfig(auto_route_threshold=0.85, picker_threshold=0.65)
    candidates = (_rb(0.65, 0), _rb(0.60, 1))
    assert gate(candidates, cfg) == RoutingDecision.PICKER


def test_gate_picker_just_below_auto_route():
    cfg = ConfidenceConfig(auto_route_threshold=0.85, picker_threshold=0.65)
    candidates = (_rb(0.849, 0), _rb(0.80, 1))
    assert gate(candidates, cfg) == RoutingDecision.PICKER


# ── gate() — SYNTHESIZE band ──────────────────────────────────────


def test_gate_synthesize_below_picker_threshold():
    cfg = ConfidenceConfig(auto_route_threshold=0.85, picker_threshold=0.65)
    candidates = (_rb(0.30, 0), _rb(0.20, 1))
    assert gate(candidates, cfg) == RoutingDecision.SYNTHESIZE


def test_gate_synthesize_empty_candidates():
    cfg = ConfidenceConfig()
    assert gate((), cfg) == RoutingDecision.SYNTHESIZE


def test_gate_synthesize_just_below_picker_threshold():
    cfg = ConfidenceConfig(auto_route_threshold=0.85, picker_threshold=0.65)
    candidates = (_rb(0.649, 0),)
    assert gate(candidates, cfg) == RoutingDecision.SYNTHESIZE


# ── Boundary exhaustiveness ────────────────────────────────────────


@pytest.mark.parametrize(
    "score,expected",
    [
        (1.00, RoutingDecision.AUTO_ROUTE),
        (0.85, RoutingDecision.AUTO_ROUTE),
        (0.84, RoutingDecision.PICKER),
        (0.65, RoutingDecision.PICKER),
        (0.64, RoutingDecision.SYNTHESIZE),
        (0.00, RoutingDecision.SYNTHESIZE),
    ],
)
def test_gate_boundary_exhaustive(score: float, expected: RoutingDecision):
    cfg = ConfidenceConfig(auto_route_threshold=0.85, picker_threshold=0.65)
    candidates = (_rb(score, 0), _rb(0.10, 1))
    assert gate(candidates, cfg) == expected
