# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the public API surface of sagewai.autopilot.controller."""

from __future__ import annotations

import pytest

# ── controller subpackage surface ──────────────────────────────────


def test_controller_subpackage_exports_autopilot_controller():
    from sagewai.autopilot.controller import AutopilotController

    assert AutopilotController is not None


def test_controller_subpackage_exports_mission_driver():
    from sagewai.autopilot.controller import MissionDriver

    assert MissionDriver is not None


def test_controller_subpackage_exports_controller_config():
    from sagewai.autopilot.controller import ControllerConfig

    assert ControllerConfig is not None


def test_controller_subpackage_exports_mission_run_result():
    from sagewai.autopilot.controller import MissionRunResult

    assert MissionRunResult is not None


def test_controller_subpackage_exports_step_result():
    from sagewai.autopilot.controller import StepResult

    assert StepResult is not None


def test_controller_all_list_complete():
    import sagewai.autopilot.controller as mod

    expected = {
        "AutopilotController",
        "MissionDriver",
        "ControllerConfig",
        "MissionRunResult",
        "StepResult",
    }
    assert expected.issubset(set(mod.__all__))


# ── top-level autopilot re-exports ─────────────────────────────────


def test_autopilot_top_level_exports_autopilot_controller():
    from sagewai.autopilot import AutopilotController

    assert AutopilotController is not None


def test_autopilot_top_level_exports_mission_driver():
    from sagewai.autopilot import MissionDriver

    assert MissionDriver is not None


def test_autopilot_top_level_exports_controller_config():
    from sagewai.autopilot import ControllerConfig

    assert ControllerConfig is not None


def test_autopilot_top_level_exports_mission_run_result():
    from sagewai.autopilot import MissionRunResult

    assert MissionRunResult is not None


def test_autopilot_top_level_exports_step_result():
    from sagewai.autopilot import StepResult

    assert StepResult is not None


# ── parametrized import check ───────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "AutopilotController",
        "MissionDriver",
        "ControllerConfig",
        "MissionRunResult",
        "StepResult",
    ],
)
def test_autopilot_top_level_all_includes_controller_symbols(name):
    import sagewai.autopilot as mod

    assert name in mod.__all__, f"{name!r} missing from sagewai.autopilot.__all__"


# ── type contracts ─────────────────────────────────────────────────


def test_step_result_is_pydantic_model():
    from pydantic import BaseModel

    from sagewai.autopilot.controller import StepResult

    assert issubclass(StepResult, BaseModel)


def test_mission_run_result_is_pydantic_model():
    from pydantic import BaseModel

    from sagewai.autopilot.controller import MissionRunResult

    assert issubclass(MissionRunResult, BaseModel)


def test_controller_config_is_pydantic_model():
    from pydantic import BaseModel

    from sagewai.autopilot.controller import ControllerConfig

    assert issubclass(ControllerConfig, BaseModel)


def test_step_result_is_frozen():
    from sagewai.autopilot.controller import StepResult

    sr = StepResult(node_id="n", status="completed")
    with pytest.raises(Exception):
        sr.node_id = "mutated"  # type: ignore[misc]


def test_mission_run_result_is_frozen():
    from sagewai.autopilot.controller import MissionRunResult

    r = MissionRunResult(mission_id="ms-x", status="completed", steps=(), duration_seconds=0.1)
    with pytest.raises(Exception):
        r.status = "mutated"  # type: ignore[misc]


def test_step_telemetry_exported_from_controller():
    from sagewai.autopilot.controller import StepTelemetry  # noqa: F401


def test_step_telemetry_exported_from_autopilot():
    from sagewai.autopilot import StepTelemetry  # noqa: F401
