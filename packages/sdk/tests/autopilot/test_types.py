# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for autopilot enum types."""

from __future__ import annotations

import pytest

from sagewai.autopilot._types import AgentKind, MissionState, Mode, Operator


def test_mode_members():
    assert {m.value for m in Mode} == {"scheduled", "event_driven", "batch"}


def test_agent_kind_members():
    assert {k.value for k in AgentKind} == {"llm", "deterministic"}


def test_operator_members():
    assert {o.value for o in Operator} == {">=", "<=", "==", ">", "<"}


def test_mission_state_members():
    assert {s.value for s in MissionState} == {
        "draft",
        "approved",
        "scheduled",
        "running",
        "completed",
        "failed",
    }


@pytest.mark.parametrize("value", ["scheduled", "event_driven", "batch"])
def test_mode_round_trip_from_string(value: str):
    assert Mode(value).value == value


def test_mode_rejects_unknown_value():
    with pytest.raises(ValueError):
        Mode("cron")  # old vocabulary, must be rejected
