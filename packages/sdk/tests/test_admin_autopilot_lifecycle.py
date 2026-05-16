# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the mission lifecycle state machine."""

from __future__ import annotations

import datetime

import pytest

from sagewai.admin.autopilot_lifecycle import (
    IllegalTransition,
    MissionStatus,
    assert_transition,
    transition_mission,
)
from sagewai.admin.autopilot_state import save_mission
from sagewai.admin.state_file import AdminStateFile


@pytest.fixture()
def sf(tmp_path):
    return AdminStateFile(tmp_path / "state.json")


@pytest.fixture()
def pending_mission(sf):
    m = save_mission(
        sf,
        {
            "mission_id": "test-mission-001",
            "project_id": None,
            "status": "pending",
            "created_at": "2026-05-09T00:00:00+00:00",
            "goal_preview": "test goal",
            "slots": {},
            "blueprint_json": "{}",
            "score": None,
        },
    )
    return m


# ── assert_transition unit tests ─────────────────────────────────────


@pytest.mark.parametrize(
    "old,new",
    [
        (MissionStatus.PENDING, MissionStatus.RUNNING),
        (MissionStatus.PENDING, MissionStatus.CANCELLED),
        (MissionStatus.RUNNING, MissionStatus.COMPLETED),
        (MissionStatus.RUNNING, MissionStatus.FAILED),
        (MissionStatus.RUNNING, MissionStatus.CANCELLED),
    ],
)
def test_assert_transition_allowed(old, new):
    assert_transition(old, new)  # must not raise


@pytest.mark.parametrize(
    "old,new",
    [
        (MissionStatus.PENDING, MissionStatus.COMPLETED),
        (MissionStatus.PENDING, MissionStatus.FAILED),
        (MissionStatus.COMPLETED, MissionStatus.RUNNING),
        (MissionStatus.COMPLETED, MissionStatus.PENDING),
        (MissionStatus.FAILED, MissionStatus.RUNNING),
        (MissionStatus.CANCELLED, MissionStatus.RUNNING),
        (MissionStatus.CANCELLED, MissionStatus.PENDING),
    ],
)
def test_assert_transition_rejected(old, new):
    with pytest.raises(IllegalTransition):
        assert_transition(old, new)


# ── transition_mission integration tests ─────────────────────────────


def test_pending_to_running_sets_started_at(sf, pending_mission):
    fixed = datetime.datetime(2026, 5, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
    result = transition_mission(sf, "test-mission-001", MissionStatus.RUNNING, now=fixed)
    assert result["status"] == "running"
    assert result["started_at"] == fixed.isoformat()


def test_running_to_completed_sets_finished_at(sf, pending_mission):
    fixed = datetime.datetime(2026, 5, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
    transition_mission(sf, "test-mission-001", MissionStatus.RUNNING, now=fixed)
    result = transition_mission(sf, "test-mission-001", MissionStatus.COMPLETED, now=fixed)
    assert result["status"] == "completed"
    assert result["finished_at"] == fixed.isoformat()


def test_running_to_failed_sets_failure_reason(sf, pending_mission):
    transition_mission(sf, "test-mission-001", MissionStatus.RUNNING)
    result = transition_mission(
        sf, "test-mission-001", MissionStatus.FAILED, reason="timeout"
    )
    assert result["status"] == "failed"
    assert result["failure_reason"] == "timeout"
    assert "finished_at" in result


def test_running_to_cancelled_sets_timestamps(sf, pending_mission):
    transition_mission(sf, "test-mission-001", MissionStatus.RUNNING)
    result = transition_mission(
        sf, "test-mission-001", MissionStatus.CANCELLED, reason="operator request"
    )
    assert result["status"] == "cancelled"
    assert result["cancel_reason"] == "operator request"
    assert "cancelled_at" in result


def test_pending_to_completed_rejected(sf, pending_mission):
    with pytest.raises(IllegalTransition):
        transition_mission(sf, "test-mission-001", MissionStatus.COMPLETED)


def test_completed_to_running_rejected(sf, pending_mission):
    transition_mission(sf, "test-mission-001", MissionStatus.RUNNING)
    transition_mission(sf, "test-mission-001", MissionStatus.COMPLETED)
    with pytest.raises(IllegalTransition):
        transition_mission(sf, "test-mission-001", MissionStatus.RUNNING)


def test_cancelled_to_running_rejected(sf, pending_mission):
    transition_mission(sf, "test-mission-001", MissionStatus.CANCELLED)
    with pytest.raises(IllegalTransition):
        transition_mission(sf, "test-mission-001", MissionStatus.RUNNING)


def test_unknown_status_raises(sf, pending_mission):
    # Inject a bad status directly to simulate corrupted state
    def _corrupt(data):
        for m in data.get("autopilot_missions", []):
            if m["mission_id"] == "test-mission-001":
                m["status"] = "gibberish"

    sf._mutate(_corrupt)
    with pytest.raises(IllegalTransition):
        transition_mission(sf, "test-mission-001", MissionStatus.RUNNING)


def test_missing_mission_raises(sf):
    with pytest.raises(KeyError):
        transition_mission(sf, "no-such-mission", MissionStatus.RUNNING)
