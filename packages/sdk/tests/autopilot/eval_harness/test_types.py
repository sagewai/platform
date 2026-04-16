"""Tests for GoldenGoal, GoldenGoalSet, EvalReport, EvalConfig types."""

from __future__ import annotations

import pytest

from sagewai.autopilot.eval_harness.types import (
    EvalConfig,
    EvalReport,
    GoldenGoal,
    GoldenGoalSet,
)

# ── GoldenGoal ─────────────────────────────────────────────────────


def test_golden_goal_auto_route_band():
    gg = GoldenGoal(
        goal="run daily research on 5 vendors",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="auto_route",
    )
    assert gg.expected_band == "auto_route"
    assert gg.expected_blueprint_id == "SYNTHETIC_scheduled_research"


def test_golden_goal_synthesis_band_has_no_blueprint_id():
    gg = GoldenGoal(
        goal="build a spaceship",
        expected_blueprint_id=None,
        expected_band="synthesis",
    )
    assert gg.expected_blueprint_id is None


def test_golden_goal_picker_band():
    gg = GoldenGoal(
        goal="classify incoming events maybe",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="picker",
    )
    assert gg.expected_band == "picker"


def test_golden_goal_rejects_empty_goal_string():
    with pytest.raises(Exception):
        GoldenGoal(goal="", expected_blueprint_id=None, expected_band="synthesis")


def test_golden_goal_rejects_invalid_band():
    with pytest.raises(Exception):
        GoldenGoal(
            goal="do something",
            expected_blueprint_id=None,
            expected_band="invalid_band",  # type: ignore[arg-type]
        )


def test_golden_goal_is_immutable():
    gg = GoldenGoal(goal="some goal", expected_blueprint_id=None, expected_band="synthesis")
    with pytest.raises(Exception):
        gg.goal = "mutated"  # type: ignore[misc]


def test_golden_goal_json_round_trip():
    gg = GoldenGoal(
        goal="process batch overnight",
        expected_blueprint_id="SYNTHETIC_batch_etl",
        expected_band="auto_route",
    )
    restored = GoldenGoal.model_validate_json(gg.model_dump_json())
    assert restored == gg


# ── GoldenGoalSet ──────────────────────────────────────────────────


def _make_goal(
    band: str = "auto_route", bp_id: str | None = "SYNTHETIC_scheduled_research"
) -> GoldenGoal:
    return GoldenGoal(goal=f"some goal for {band}", expected_blueprint_id=bp_id, expected_band=band)  # type: ignore[arg-type]


def test_golden_goal_set_stores_goals():
    goals = (_make_goal("auto_route"), _make_goal("synthesis", None))
    gs = GoldenGoalSet(version="1.0.0", goals=goals)
    assert len(gs.goals) == 2
    assert gs.version == "1.0.0"


def test_golden_goal_set_created_at_defaults_to_now():
    gs = GoldenGoalSet(version="1.0.0", goals=(_make_goal(),))
    assert gs.created_at is not None


def test_golden_goal_set_rejects_empty_goals():
    with pytest.raises(Exception):
        GoldenGoalSet(version="1.0.0", goals=())


def test_golden_goal_set_is_immutable():
    gs = GoldenGoalSet(version="1.0.0", goals=(_make_goal(),))
    with pytest.raises(Exception):
        gs.version = "mutated"  # type: ignore[misc]


def test_golden_goal_set_json_round_trip():
    gs = GoldenGoalSet(
        version="2.0.0",
        goals=(_make_goal("auto_route"), _make_goal("picker", "SYNTHETIC_event_triage")),
    )
    restored = GoldenGoalSet.model_validate_json(gs.model_dump_json())
    assert restored.version == gs.version
    assert len(restored.goals) == len(gs.goals)


# ── EvalReport ─────────────────────────────────────────────────────


def _make_report(**overrides) -> EvalReport:
    defaults = dict(
        total_goals=50,
        top1_accuracy=0.92,
        band_accuracy=0.96,
        false_positive_rate=0.04,
        duration_seconds=1.23,
    )
    defaults.update(overrides)
    return EvalReport(**defaults)


def test_eval_report_stores_fields():
    r = _make_report()
    assert r.total_goals == 50
    assert r.top1_accuracy == pytest.approx(0.92)
    assert r.band_accuracy == pytest.approx(0.96)
    assert r.false_positive_rate == pytest.approx(0.04)
    assert r.duration_seconds == pytest.approx(1.23)


def test_eval_report_accuracy_bounds():
    with pytest.raises(Exception):
        _make_report(top1_accuracy=1.01)
    with pytest.raises(Exception):
        _make_report(top1_accuracy=-0.01)
    with pytest.raises(Exception):
        _make_report(band_accuracy=1.01)
    with pytest.raises(Exception):
        _make_report(false_positive_rate=-0.01)


def test_eval_report_rejects_zero_total_goals():
    with pytest.raises(Exception):
        _make_report(total_goals=0)


def test_eval_report_is_immutable():
    r = _make_report()
    with pytest.raises(Exception):
        r.total_goals = 999  # type: ignore[misc]


def test_eval_report_json_round_trip():
    r = _make_report(total_goals=10, top1_accuracy=1.0, band_accuracy=1.0, false_positive_rate=0.0)
    restored = EvalReport.model_validate_json(r.model_dump_json())
    assert restored == r


# ── EvalConfig ─────────────────────────────────────────────────────


def test_eval_config_defaults_match_production():
    cfg = EvalConfig()
    assert cfg.auto_route_threshold == pytest.approx(0.85)
    assert cfg.picker_threshold == pytest.approx(0.65)
    assert cfg.retrieve_k == 5


def test_eval_config_custom_thresholds():
    cfg = EvalConfig(auto_route_threshold=0.90, picker_threshold=0.70, retrieve_k=10)
    assert cfg.auto_route_threshold == pytest.approx(0.90)
    assert cfg.picker_threshold == pytest.approx(0.70)
    assert cfg.retrieve_k == 10


def test_eval_config_rejects_inverted_thresholds():
    with pytest.raises(Exception):
        EvalConfig(auto_route_threshold=0.60, picker_threshold=0.70)


def test_eval_config_is_immutable():
    cfg = EvalConfig()
    with pytest.raises(Exception):
        cfg.auto_route_threshold = 0.99  # type: ignore[misc]


def test_eval_config_json_round_trip():
    cfg = EvalConfig(auto_route_threshold=0.88, picker_threshold=0.68, retrieve_k=3)
    restored = EvalConfig.model_validate_json(cfg.model_dump_json())
    assert restored == cfg
