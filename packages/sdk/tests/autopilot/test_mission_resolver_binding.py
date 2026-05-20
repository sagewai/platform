# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Verify the autopilot binds the mission_state resolver at controller init."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from sagewai.tools.builtins import mission_state as ms
from sagewai.autopilot.controller.controller import AutopilotController
from sagewai.autopilot.controller.types import ControllerConfig
from sagewai.autopilot.mission import Mission


@pytest.fixture(autouse=True)
def restore_resolver():
    """Restore the default resolver after each test to avoid cross-test leakage."""
    yield
    ms.set_mission_resolver(ms._default_resolver)


def _make_controller() -> AutopilotController:
    return AutopilotController(
        router=MagicMock(),
        client=MagicMock(),
        config=ControllerConfig(project_id="test"),
    )


def test_resolver_is_bound_after_controller_init():
    """AutopilotController.__init__ must call set_mission_resolver."""
    # Reset to the explicit default first so we know the starting state.
    ms.set_mission_resolver(ms._default_resolver)

    _ = _make_controller()

    assert ms._resolver is not ms._default_resolver, (
        "AutopilotController did not bind a mission resolver. "
        "set_mission_resolver(...) must be called in AutopilotController.__init__."
    )


def test_resolver_raises_for_unknown_mission_id():
    """The bound resolver should raise KeyError for an unknown mission_id."""
    ctrl = _make_controller()
    # The resolver is now bound to this controller's _lookup_mission_by_id.
    with pytest.raises(KeyError, match="unknown mission_id"):
        ms._resolver("nonexistent-id")


def test_resolver_returns_registered_mission():
    """A mission registered via _register_mission must be resolvable."""
    ctrl = _make_controller()
    mission = Mission(
        mission_id="ms-test123",
        project_id="test",
        blueprint_id="bp-1",
        blueprint_version="1.0.0",
        slots={},
    )
    ctrl._register_mission(mission)

    resolved = ms._resolver("ms-test123")
    assert resolved is mission


def test_mission_duck_typed_methods_exist():
    """Mission must expose all four duck-typed methods required by mission_state builtins."""
    mission = Mission(
        mission_id="ms-abc",
        project_id="test",
        blueprint_id="bp-1",
        blueprint_version="1.0.0",
        slots={},
    )
    assert callable(getattr(mission, "record_step_result", None))
    assert callable(getattr(mission, "record_progress", None))
    assert callable(getattr(mission, "emit_hitl_request", None))
    assert callable(getattr(mission, "publish_event", None))


def test_mission_record_step_result():
    m = Mission(
        mission_id="ms-abc", project_id="p", blueprint_id="bp", blueprint_version="1", slots={}
    )
    m.record_step_result("step-1", {"output": "ok"})
    assert m._step_results == [("step-1", {"output": "ok"})]


def test_mission_record_progress():
    m = Mission(
        mission_id="ms-abc", project_id="p", blueprint_id="bp", blueprint_version="1", slots={}
    )
    m.record_progress(0.5, note="halfway")
    assert m._progress == 0.5
    assert any("halfway" in str(e) for e in m._events)


def test_mission_emit_hitl_request():
    m = Mission(
        mission_id="ms-abc", project_id="p", blueprint_id="bp", blueprint_version="1", slots={}
    )
    m.emit_hitl_request("req-1", reason="needs approval", payload={"key": "val"})
    assert m._hitl_requests == [("req-1", "needs approval", {"key": "val"})]


def test_mission_publish_event():
    m = Mission(
        mission_id="ms-abc", project_id="p", blueprint_id="bp", blueprint_version="1", slots={}
    )
    m.publish_event("step.completed", {"result": "success"})
    assert ("step.completed", {"result": "success"}) in m._events
