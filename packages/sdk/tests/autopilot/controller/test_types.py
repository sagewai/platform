# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for controller result types: StepResult and MissionRunResult."""

from __future__ import annotations

import pytest

from sagewai.autopilot.controller.types import MissionRunResult, StepResult

# ── StepResult ─────────────────────────────────────────────────────


def test_step_result_stores_fields():
    sr = StepResult(node_id="scout", status="completed", output_preview="done")
    assert sr.node_id == "scout"
    assert sr.status == "completed"
    assert sr.output_preview == "done"


def test_step_result_output_preview_optional():
    sr = StepResult(node_id="scout", status="completed")
    assert sr.output_preview is None


def test_step_result_requires_node_id():
    with pytest.raises(Exception):
        StepResult(node_id="", status="completed")


def test_step_result_requires_status():
    with pytest.raises(Exception):
        StepResult(node_id="scout", status="")


def test_step_result_is_immutable():
    sr = StepResult(node_id="scout", status="completed")
    with pytest.raises(Exception):
        sr.status = "failed"  # type: ignore[misc]


def test_step_result_valid_statuses():
    for status in ("completed", "failed", "skipped"):
        sr = StepResult(node_id="n", status=status)
        assert sr.status == status


# ── MissionRunResult ────────────────────────────────────────────────


def _make_run_result(**kwargs) -> MissionRunResult:
    defaults = dict(
        mission_id="ms-abc123",
        status="completed",
        steps=(StepResult(node_id="scout", status="completed"),),
        duration_seconds=0.42,
        error=None,
    )
    defaults.update(kwargs)
    return MissionRunResult(**defaults)


def test_mission_run_result_stores_fields():
    r = _make_run_result()
    assert r.mission_id == "ms-abc123"
    assert r.status == "completed"
    assert len(r.steps) == 1
    assert r.steps[0].node_id == "scout"
    assert r.duration_seconds == pytest.approx(0.42)
    assert r.error is None


def test_mission_run_result_failed_with_error():
    r = _make_run_result(status="failed", error="something went wrong")
    assert r.status == "failed"
    assert r.error == "something went wrong"


def test_mission_run_result_zero_steps_allowed():
    r = _make_run_result(steps=())
    assert r.steps == ()


def test_mission_run_result_requires_mission_id():
    with pytest.raises(Exception):
        _make_run_result(mission_id="")


def test_mission_run_result_duration_non_negative():
    with pytest.raises(Exception):
        _make_run_result(duration_seconds=-0.1)


def test_mission_run_result_is_immutable():
    r = _make_run_result()
    with pytest.raises(Exception):
        r.status = "failed"  # type: ignore[misc]


def test_mission_run_result_multiple_steps():
    steps = (
        StepResult(node_id="scout", status="completed", output_preview="ok"),
        StepResult(node_id="summarizer", status="completed", output_preview="summary"),
    )
    r = _make_run_result(steps=steps)
    assert len(r.steps) == 2
    assert r.steps[1].node_id == "summarizer"


def test_mission_run_result_json_round_trip():
    r = _make_run_result()
    reloaded = MissionRunResult.model_validate_json(r.model_dump_json())
    assert reloaded.mission_id == r.mission_id
    assert reloaded.status == r.status
    assert len(reloaded.steps) == len(r.steps)
