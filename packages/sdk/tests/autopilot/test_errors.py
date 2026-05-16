# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the autopilot error hierarchy."""

from __future__ import annotations

import pytest

from sagewai.autopilot.errors import (
    AgentGraphError,
    AutopilotError,
    BlueprintValidationError,
    MissionLifecycleError,
    SlotValidationError,
)
from sagewai.errors import SagewaiError


def test_autopilot_error_inherits_sagewai_error():
    assert issubclass(AutopilotError, SagewaiError)


@pytest.mark.parametrize(
    "cls",
    [
        SlotValidationError,
        BlueprintValidationError,
        AgentGraphError,
        MissionLifecycleError,
    ],
)
def test_all_subclasses_inherit_autopilot_error(cls: type[Exception]) -> None:
    assert issubclass(cls, AutopilotError)


def test_slot_validation_error_carries_slot_name():
    err = SlotValidationError("vendors", "must not be empty")
    assert err.slot_name == "vendors"
    assert "vendors" in str(err)
    assert "must not be empty" in str(err)


def test_agent_graph_error_carries_node_id_when_provided():
    err = AgentGraphError("cycle detected", node_id="scout")
    assert err.node_id == "scout"
    assert "scout" in str(err)


def test_mission_lifecycle_error_shows_from_and_to_states():
    err = MissionLifecycleError(from_state="running", to_state="draft")
    msg = str(err)
    assert "running" in msg
    assert "draft" in msg
