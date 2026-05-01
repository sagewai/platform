# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for EvalHarness and run_eval."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from sagewai.autopilot.eval_harness.harness import EvalHarness, run_eval
from sagewai.autopilot.eval_harness.types import EvalConfig, EvalReport, GoldenGoal, GoldenGoalSet

# ── Helpers ────────────────────────────────────────────────────────


def _make_goal_set(*goals: GoldenGoal) -> GoldenGoalSet:
    return GoldenGoalSet(version="0.0.1", goals=tuple(goals))


def _goal(band: str, bp_id: str | None = None) -> GoldenGoal:
    return GoldenGoal(
        goal=f"test goal for {band}",
        expected_blueprint_id=bp_id,
        expected_band=band,  # type: ignore[arg-type]
    )


def _make_auto_route_result(bp_id: str = "bp-1"):
    """Return a mock AutoRouted result."""
    from sagewai.autopilot.routing import AutoRouted
    from sagewai.autopilot.routing.types import RankedBlueprint

    return AutoRouted(
        ranked=RankedBlueprint(blueprint_json=json.dumps({"id": bp_id}), score=0.92),
        slots={},
        preview="preview text",
    )


def _make_picker_result(bp_id: str = "bp-1"):
    """Return a mock PickerNeeded result."""
    from sagewai.autopilot.routing import PickerNeeded
    from sagewai.autopilot.routing.types import RankedBlueprint

    return PickerNeeded(
        candidates=(RankedBlueprint(blueprint_json=json.dumps({"id": bp_id}), score=0.75),),
    )


def _make_synthesis_result(goal: str = "test goal for synthesis"):
    """Return a mock SynthesisNeeded result."""
    from sagewai.autopilot.routing import SynthesisNeeded

    return SynthesisNeeded(goal=goal)


# ── EvalHarness construction ───────────────────────────────────────


def test_eval_harness_accepts_goal_set_and_client():
    client = AsyncMock()
    goals = _make_goal_set(_goal("synthesis"))
    harness = EvalHarness(goal_set=goals, client=client)
    assert harness is not None


def test_eval_harness_uses_default_eval_config():
    client = AsyncMock()
    harness = EvalHarness(goal_set=_make_goal_set(_goal("synthesis")), client=client)
    assert harness.config == EvalConfig()


def test_eval_harness_accepts_custom_config():
    client = AsyncMock()
    cfg = EvalConfig(auto_route_threshold=0.90, picker_threshold=0.70)
    harness = EvalHarness(goal_set=_make_goal_set(_goal("synthesis")), client=client, config=cfg)
    assert harness.config.auto_route_threshold == pytest.approx(0.90)


# ── Perfect run (all goals routed correctly) ───────────────────────


def test_run_perfect_auto_route():
    """All goals are auto_route and the mock router returns AutoRouted with matching ID."""
    client = AsyncMock()
    goals = _make_goal_set(
        GoldenGoal(
            goal="research vendors", expected_blueprint_id="bp-1", expected_band="auto_route"
        ),
        GoldenGoal(
            goal="research more vendors", expected_blueprint_id="bp-1", expected_band="auto_route"
        ),
    )
    harness = EvalHarness(goal_set=goals, client=client)

    with patch.object(
        harness,
        "_route_goal",
        side_effect=[
            _make_auto_route_result("bp-1"),
            _make_auto_route_result("bp-1"),
        ],
    ):
        report = harness.run()

    assert report.total_goals == 2
    assert report.top1_accuracy == pytest.approx(1.0)
    assert report.band_accuracy == pytest.approx(1.0)
    assert report.false_positive_rate == pytest.approx(0.0)


def test_run_perfect_synthesis():
    """All goals are synthesis and the mock router returns SynthesisNeeded."""
    client = AsyncMock()
    goals = _make_goal_set(
        GoldenGoal(goal="do novel thing 1", expected_blueprint_id=None, expected_band="synthesis"),
        GoldenGoal(goal="do novel thing 2", expected_blueprint_id=None, expected_band="synthesis"),
    )
    harness = EvalHarness(goal_set=goals, client=client)

    with patch.object(
        harness,
        "_route_goal",
        side_effect=[
            _make_synthesis_result("do novel thing 1"),
            _make_synthesis_result("do novel thing 2"),
        ],
    ):
        report = harness.run()

    assert report.total_goals == 2
    assert report.top1_accuracy == pytest.approx(1.0)
    assert report.band_accuracy == pytest.approx(1.0)
    assert report.false_positive_rate == pytest.approx(0.0)


# ── Partial failures ───────────────────────────────────────────────


def test_run_partial_top1_miss():
    """Router returns correct band but wrong blueprint ID → band correct, top1 miss."""
    client = AsyncMock()
    goals = _make_goal_set(
        GoldenGoal(
            goal="research vendors", expected_blueprint_id="bp-correct", expected_band="auto_route"
        ),
    )
    harness = EvalHarness(goal_set=goals, client=client)

    with patch.object(harness, "_route_goal", return_value=_make_auto_route_result("bp-wrong")):
        report = harness.run()

    assert report.top1_accuracy == pytest.approx(0.0)
    assert report.band_accuracy == pytest.approx(1.0)


def test_run_synthesis_expected_but_auto_routed_counts_as_false_positive():
    """Router auto-routes a goal that should be synthesis → false positive."""
    client = AsyncMock()
    goals = _make_goal_set(
        GoldenGoal(goal="do novel thing", expected_blueprint_id=None, expected_band="synthesis"),
    )
    harness = EvalHarness(goal_set=goals, client=client)

    with patch.object(harness, "_route_goal", return_value=_make_auto_route_result("bp-1")):
        report = harness.run()

    assert report.false_positive_rate == pytest.approx(1.0)
    assert report.band_accuracy == pytest.approx(0.0)


def test_run_mixed_accuracy():
    """2 correct out of 4 goals."""
    client = AsyncMock()
    goals = _make_goal_set(
        GoldenGoal(
            goal="research vendors", expected_blueprint_id="bp-1", expected_band="auto_route"
        ),
        GoldenGoal(goal="triage tickets", expected_blueprint_id="bp-2", expected_band="auto_route"),
        GoldenGoal(goal="novel thing 1", expected_blueprint_id=None, expected_band="synthesis"),
        GoldenGoal(goal="novel thing 2", expected_blueprint_id=None, expected_band="synthesis"),
    )
    harness = EvalHarness(goal_set=goals, client=client)

    side_effects = [
        _make_auto_route_result("bp-1"),  # correct
        _make_auto_route_result("bp-wrong"),  # top1 miss, band correct
        _make_synthesis_result("novel thing 1"),  # correct
        _make_auto_route_result("bp-1"),  # false positive (synthesis expected)
    ]
    with patch.object(harness, "_route_goal", side_effect=side_effects):
        report = harness.run()

    assert report.total_goals == 4
    assert report.top1_accuracy == pytest.approx(0.5)  # 2/4
    assert report.band_accuracy == pytest.approx(0.75)  # 3/4
    assert report.false_positive_rate == pytest.approx(0.5)  # 1/2 synthesis goals false-positived


# ── Report shape ───────────────────────────────────────────────────


def test_run_returns_eval_report_instance():
    client = AsyncMock()
    goals = _make_goal_set(_goal("synthesis"))
    harness = EvalHarness(goal_set=goals, client=client)
    with patch.object(harness, "_route_goal", return_value=_make_synthesis_result()):
        report = harness.run()
    assert isinstance(report, EvalReport)


def test_run_records_positive_duration():
    client = AsyncMock()
    goals = _make_goal_set(_goal("synthesis"))
    harness = EvalHarness(goal_set=goals, client=client)
    with patch.object(harness, "_route_goal", return_value=_make_synthesis_result()):
        report = harness.run()
    assert report.duration_seconds >= 0.0


# ── run_eval convenience function ─────────────────────────────────


def test_run_eval_convenience_function():
    client = AsyncMock()
    goals = _make_goal_set(
        GoldenGoal(goal="novel task", expected_blueprint_id=None, expected_band="synthesis"),
    )
    with patch("sagewai.autopilot.eval_harness.harness.EvalHarness.run") as mock_run:
        mock_run.return_value = EvalReport(
            total_goals=1,
            top1_accuracy=1.0,
            band_accuracy=1.0,
            false_positive_rate=0.0,
            duration_seconds=0.01,
        )
        report = run_eval(goal_set=goals, client=client)
    assert isinstance(report, EvalReport)
    assert report.total_goals == 1


# ── Integration: full fixture set with mock client ─────────────────


def _build_full_mock_client(goal_set: GoldenGoalSet) -> AsyncMock:
    """Build a mock client that replays ideal responses for every goal."""
    from sagewai.autopilot.sagewai_llm.types import RetrieveBlueprintsResponse, RetrieveCandidate
    from tests.autopilot.fixtures import (
        make_synthetic_batch_blueprint,
        make_synthetic_event_driven_blueprint,
        make_synthetic_scheduled_blueprint,
    )

    # Build blueprint JSON map keyed by ID
    _bp_json_map = {
        "SYNTHETIC_scheduled_research": make_synthetic_scheduled_blueprint().model_dump_json(),
        "SYNTHETIC_event_triage": make_synthetic_event_driven_blueprint().model_dump_json(),
        "SYNTHETIC_batch_extract": make_synthetic_batch_blueprint().model_dump_json(),
    }
    # Use scheduled blueprint JSON as a generic noise blueprint
    _noise_bp_json = make_synthetic_scheduled_blueprint().model_dump_json()

    client = AsyncMock()

    async def retrieve_blueprints(goal: str, k: int = 5) -> RetrieveBlueprintsResponse:
        # Find the matching goal in the fixture set
        for gg in goal_set.goals:
            if gg.goal == goal:
                if gg.expected_blueprint_id is None:
                    # Return low-score candidates to trigger synthesis
                    candidates = tuple(
                        RetrieveCandidate(
                            blueprint_json=_noise_bp_json,
                            score=max(0.0, 0.30 - i * 0.02),
                        )
                        for i in range(k)
                    )
                    return RetrieveBlueprintsResponse(candidates=candidates)
                if gg.expected_band == "auto_route":
                    top_score = 0.92
                else:  # picker
                    top_score = 0.74
                top_bp_json = _bp_json_map[gg.expected_blueprint_id]
                candidates_list = [RetrieveCandidate(blueprint_json=top_bp_json, score=top_score)]
                for i in range(1, k):
                    candidates_list.append(
                        RetrieveCandidate(
                            blueprint_json=_noise_bp_json,
                            score=max(0.0, top_score - i * 0.12),
                        )
                    )
                return RetrieveBlueprintsResponse(candidates=tuple(candidates_list))
        # Unknown goal → synthesis fallback
        candidates = tuple(
            RetrieveCandidate(blueprint_json=_noise_bp_json, score=0.20) for _ in range(k)
        )
        return RetrieveBlueprintsResponse(candidates=candidates)

    client.retrieve_blueprints.side_effect = retrieve_blueprints
    return client


def test_full_fixture_set_integration():
    """End-to-end: run all 52 synthetic goals through the harness with a realistic mock."""
    from sagewai.autopilot.eval_harness.fixtures import SYNTHETIC_GOLDEN_GOALS

    client = _build_full_mock_client(SYNTHETIC_GOLDEN_GOALS)
    harness = EvalHarness(goal_set=SYNTHETIC_GOLDEN_GOALS, client=client)
    report = harness.run()

    assert report.total_goals == len(SYNTHETIC_GOLDEN_GOALS.goals)
    assert report.total_goals >= 50
    # With an ideal mock client all accuracy metrics should be perfect
    assert report.top1_accuracy == pytest.approx(1.0)
    assert report.band_accuracy == pytest.approx(1.0)
    assert report.false_positive_rate == pytest.approx(0.0)
    assert report.duration_seconds >= 0.0
