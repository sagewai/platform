"""Tests for the SYNTHETIC_GOLDEN_GOALS fixture set integrity."""

from __future__ import annotations

import pytest

from sagewai.autopilot.eval_harness.fixtures import SYNTHETIC_GOLDEN_GOALS
from sagewai.autopilot.eval_harness.types import GoldenGoalSet

#: The three synthetic blueprint IDs defined in tests/autopilot/fixtures.py
_KNOWN_BP_IDS = frozenset(
    {
        "SYNTHETIC_scheduled_research",
        "SYNTHETIC_event_triage",
        "SYNTHETIC_batch_extract",
    }
)


def test_synthetic_golden_goals_is_golden_goal_set():
    assert isinstance(SYNTHETIC_GOLDEN_GOALS, GoldenGoalSet)


def test_synthetic_golden_goals_has_at_least_50_goals():
    assert len(SYNTHETIC_GOLDEN_GOALS.goals) >= 50


def test_synthetic_golden_goals_version_is_set():
    assert SYNTHETIC_GOLDEN_GOALS.version


def test_all_bands_represented():
    bands = {g.expected_band for g in SYNTHETIC_GOLDEN_GOALS.goals}
    assert "auto_route" in bands
    assert "picker" in bands
    assert "synthesis" in bands


def test_auto_route_goals_have_blueprint_id():
    for g in SYNTHETIC_GOLDEN_GOALS.goals:
        if g.expected_band == "auto_route":
            assert (
                g.expected_blueprint_id is not None
            ), f"auto_route goal missing expected_blueprint_id: {g.goal!r}"


def test_synthesis_goals_have_no_blueprint_id():
    for g in SYNTHETIC_GOLDEN_GOALS.goals:
        if g.expected_band == "synthesis":
            assert (
                g.expected_blueprint_id is None
            ), f"synthesis goal should have no expected_blueprint_id: {g.goal!r}"


def test_picker_goals_have_blueprint_id():
    for g in SYNTHETIC_GOLDEN_GOALS.goals:
        if g.expected_band == "picker":
            assert (
                g.expected_blueprint_id is not None
            ), f"picker goal missing expected_blueprint_id: {g.goal!r}"


def test_all_blueprint_ids_reference_known_synthetic_ids():
    for g in SYNTHETIC_GOLDEN_GOALS.goals:
        if g.expected_blueprint_id is not None:
            assert g.expected_blueprint_id in _KNOWN_BP_IDS, (
                f"Unknown blueprint ID {g.expected_blueprint_id!r} in goal {g.goal!r}. "
                "Only SYNTHETIC_* IDs are permitted in the open-source fixture set."
            )


def test_no_duplicate_goal_strings():
    goals = [g.goal for g in SYNTHETIC_GOLDEN_GOALS.goals]
    assert len(goals) == len(set(goals)), "Duplicate goal strings found in fixture set"


def test_all_goal_strings_are_non_empty():
    for g in SYNTHETIC_GOLDEN_GOALS.goals:
        assert g.goal.strip(), f"Empty or whitespace-only goal string found: {g.goal!r}"


@pytest.mark.parametrize("band", ["auto_route", "picker", "synthesis"])
def test_each_band_has_at_least_ten_goals(band: str):
    count = sum(1 for g in SYNTHETIC_GOLDEN_GOALS.goals if g.expected_band == band)
    assert count >= 10, f"Band {band!r} has only {count} goals; expected >= 10"


def test_each_known_blueprint_id_has_auto_route_goals():
    for bp_id in _KNOWN_BP_IDS:
        count = sum(
            1
            for g in SYNTHETIC_GOLDEN_GOALS.goals
            if g.expected_blueprint_id == bp_id and g.expected_band == "auto_route"
        )
        assert count >= 5, f"Blueprint {bp_id!r} has only {count} auto_route goals; expected >= 5"
