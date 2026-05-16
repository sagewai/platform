# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the extended mission record schema (Plan H — Task 1).

Covers:
* migrate_mission_record adds defaults for legacy records
* migrate_mission_record is idempotent
* list_missions returns migrated records
* get_mission returns a migrated record
* update_mission mutates and persists a record
* update_mission raises KeyError for unknown mission_id
* MissionStatus accepts all 5 valid status strings
"""

from __future__ import annotations

import pytest

from sagewai.admin.autopilot_lifecycle import MissionStatus
from sagewai.admin.autopilot_state import (
    get_mission,
    list_missions,
    migrate_mission_record,
    save_mission,
    update_mission,
)
from sagewai.admin.state_file import AdminStateFile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sf(tmp_path):
    """AdminStateFile backed by a temp directory."""
    return AdminStateFile(tmp_path / "state.json")


def _legacy_mission(mission_id: str = "m-001", project_id: str = "proj-x") -> dict:
    """Minimal mission dict as it would look before Plan H migration."""
    return {
        "mission_id": mission_id,
        "project_id": project_id,
        "status": "pending",
        "created_at": "2026-05-09T00:00:00+00:00",
        "goal_preview": "test goal",
        "slots": {},
        "blueprint_json": "{}",
        "score": 0.9,
    }


# ---------------------------------------------------------------------------
# migrate_mission_record
# ---------------------------------------------------------------------------


class TestMigrateMissionRecord:
    NEW_KEYS = {
        "run_id",
        "started_at",
        "finished_at",
        "total_cost_usd",
        "step_count",
        "last_event_at",
        "trace",
        "error",
    }

    def test_adds_new_keys_to_legacy_record(self):
        raw = _legacy_mission()
        result = migrate_mission_record(raw)
        for key in self.NEW_KEYS:
            assert key in result, f"expected key '{key}' in migrated record"

    def test_does_not_overwrite_existing_values(self):
        raw = _legacy_mission()
        raw["run_id"] = "run-existing"
        raw["step_count"] = 7
        result = migrate_mission_record(raw)
        assert result["run_id"] == "run-existing"
        assert result["step_count"] == 7

    def test_preserves_original_keys(self):
        raw = _legacy_mission("m-42", "proj-z")
        result = migrate_mission_record(raw)
        assert result["mission_id"] == "m-42"
        assert result["project_id"] == "proj-z"
        assert result["status"] == "pending"
        assert result["goal_preview"] == "test goal"

    def test_default_values(self):
        result = migrate_mission_record(_legacy_mission())
        assert result["run_id"] is None
        assert result["started_at"] is None
        assert result["finished_at"] is None
        assert result["total_cost_usd"] == 0.0
        assert result["step_count"] == 0
        assert result["last_event_at"] is None
        assert result["trace"] == []
        assert result["error"] is None

    def test_idempotent(self):
        raw = _legacy_mission()
        once = migrate_mission_record(raw)
        twice = migrate_mission_record(once)
        assert once == twice

    def test_does_not_mutate_input(self):
        raw = _legacy_mission()
        original_keys = set(raw.keys())
        migrate_mission_record(raw)
        assert set(raw.keys()) == original_keys


# ---------------------------------------------------------------------------
# list_missions — returns migrated records
# ---------------------------------------------------------------------------


class TestListMissionsReturnsmigratedRecords:
    def test_new_keys_present_in_listed_missions(self, sf):
        save_mission(sf, _legacy_mission("m-list-1", "proj-a"))
        save_mission(sf, _legacy_mission("m-list-2", "proj-a"))
        missions = list_missions(sf)
        assert len(missions) == 2
        for m in missions:
            assert "run_id" in m
            assert "trace" in m
            assert "total_cost_usd" in m
            assert "step_count" in m
            assert "last_event_at" in m
            assert "started_at" in m
            assert "finished_at" in m
            assert "error" in m

    def test_project_filter_still_works(self, sf):
        save_mission(sf, _legacy_mission("m-a1", "proj-a"))
        save_mission(sf, _legacy_mission("m-b1", "proj-b"))
        result = list_missions(sf, project_id="proj-a")
        assert len(result) == 1
        assert result[0]["mission_id"] == "m-a1"
        assert "trace" in result[0]


# ---------------------------------------------------------------------------
# get_mission — returns migrated record
# ---------------------------------------------------------------------------


class TestGetMissionReturnsMigratedRecord:
    def test_new_keys_present(self, sf):
        save_mission(sf, _legacy_mission("m-get-1", "proj-x"))
        m = get_mission(sf, "m-get-1")
        assert m is not None
        assert "run_id" in m
        assert "trace" in m
        assert "total_cost_usd" in m
        assert "step_count" in m

    def test_returns_none_for_unknown(self, sf):
        assert get_mission(sf, "nonexistent") is None


# ---------------------------------------------------------------------------
# update_mission
# ---------------------------------------------------------------------------


class TestUpdateMission:
    def test_mutates_and_returns_updated_dict(self, sf):
        save_mission(sf, _legacy_mission("m-upd-1", "proj-x"))

        def _set_run(m):
            m["run_id"] = "run-abc"
            m["step_count"] = 3

        result = update_mission(sf, "m-upd-1", _set_run)
        assert result["run_id"] == "run-abc"
        assert result["step_count"] == 3

    def test_mutation_is_persisted(self, sf):
        save_mission(sf, _legacy_mission("m-upd-2", "proj-x"))

        update_mission(sf, "m-upd-2", lambda m: m.update({"run_id": "run-xyz"}))

        # Read back via get_mission to confirm persistence
        fetched = get_mission(sf, "m-upd-2")
        assert fetched is not None
        assert fetched["run_id"] == "run-xyz"

    def test_raises_key_error_for_unknown_mission(self, sf):
        with pytest.raises(KeyError, match="no-such-mission"):
            update_mission(sf, "no-such-mission", lambda m: None)

    def test_trace_append_pattern(self, sf):
        save_mission(sf, _legacy_mission("m-trace-1", "proj-x"))

        def _append_event(m):
            m.setdefault("trace", []).append({"type": "step", "index": 0})

        update_mission(sf, "m-trace-1", _append_event)
        fetched = get_mission(sf, "m-trace-1")
        assert fetched is not None
        assert len(fetched["trace"]) == 1
        assert fetched["trace"][0]["type"] == "step"


# ---------------------------------------------------------------------------
# MissionStatus — accepts all 5 status strings
# ---------------------------------------------------------------------------


class TestMissionStatusValues:
    @pytest.mark.parametrize(
        "status",
        ["pending", "running", "completed", "failed", "cancelled"],
    )
    def test_valid_status_does_not_raise(self, status):
        ms = MissionStatus(status)
        assert ms.value == status

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError):
            MissionStatus("bogus")
