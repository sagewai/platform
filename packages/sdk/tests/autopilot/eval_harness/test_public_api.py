"""Tests that the public API surface of sagewai.autopilot.eval_harness is complete."""

from __future__ import annotations

import importlib


def _public_names(mod):
    return (
        set(mod.__all__)
        if hasattr(mod, "__all__")
        else {n for n in dir(mod) if not n.startswith("_")}
    )


def test_eval_harness_package_exports_all_public_symbols():
    mod = importlib.import_module("sagewai.autopilot.eval_harness")
    required = {
        "EvalHarness",
        "EvalConfig",
        "EvalReport",
        "GoldenGoal",
        "GoldenGoalSet",
        "SYNTHETIC_GOLDEN_GOALS",
        "run_eval",
    }
    missing = required - _public_names(mod)
    assert not missing, f"Missing from sagewai.autopilot.eval_harness.__all__: {missing}"


def test_autopilot_top_level_re_exports_eval_harness_symbols():
    mod = importlib.import_module("sagewai.autopilot")
    required = {
        "EvalHarness",
        "EvalConfig",
        "EvalReport",
        "GoldenGoal",
        "GoldenGoalSet",
        "run_eval",
    }
    missing = required - _public_names(mod)
    assert not missing, f"Missing from sagewai.autopilot top-level: {missing}"


def test_golden_goal_importable_from_top_level():
    from sagewai.autopilot import GoldenGoal  # noqa: F401

    assert GoldenGoal is not None


def test_eval_harness_importable_from_top_level():
    from sagewai.autopilot import EvalHarness  # noqa: F401

    assert EvalHarness is not None


def test_run_eval_importable_from_top_level():
    from sagewai.autopilot import run_eval  # noqa: F401

    assert run_eval is not None


def test_eval_config_importable_from_top_level():
    from sagewai.autopilot import EvalConfig  # noqa: F401

    assert EvalConfig is not None


def test_eval_report_importable_from_top_level():
    from sagewai.autopilot import EvalReport  # noqa: F401

    assert EvalReport is not None


def test_golden_goal_set_importable_from_top_level():
    from sagewai.autopilot import GoldenGoalSet  # noqa: F401

    assert GoldenGoalSet is not None


def test_synthetic_golden_goals_importable_directly():
    from sagewai.autopilot.eval_harness import SYNTHETIC_GOLDEN_GOALS
    from sagewai.autopilot.eval_harness.types import GoldenGoalSet

    assert isinstance(SYNTHETIC_GOLDEN_GOALS, GoldenGoalSet)
