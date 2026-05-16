# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the Mission runtime instance and lifecycle."""

from __future__ import annotations

import pytest

from sagewai.autopilot._types import MissionState
from sagewai.autopilot.errors import MissionLifecycleError, SlotValidationError
from sagewai.autopilot.mission import Mission
from sagewai.autopilot.validators import default_registry

from .fixtures import (
    make_synthetic_batch_blueprint,
    make_synthetic_event_driven_blueprint,
    make_synthetic_scheduled_blueprint,
)


def _bind_scheduled() -> Mission:
    bp = make_synthetic_scheduled_blueprint()
    return Mission.from_blueprint(
        bp,
        project_id="proj-test",
        slots={"vendors": ["https://paperclip.ing"]},
        registry=default_registry,
    )


# ── Binding ──────────────────────────────────────────────────────


def test_from_blueprint_binds_valid_slots():
    m = _bind_scheduled()
    assert m.state is MissionState.DRAFT
    assert m.slots["vendors"] == ["https://paperclip.ing"]
    assert m.slots["schedule"] == "0 9 * * 1-5"
    assert m.project_id == "proj-test"
    assert m.blueprint_id == "SYNTHETIC_scheduled_research"


def test_from_blueprint_raises_on_invalid_slots():
    bp = make_synthetic_scheduled_blueprint()
    with pytest.raises(SlotValidationError):
        Mission.from_blueprint(
            bp,
            project_id="proj-test",
            slots={"vendors": []},
            registry=default_registry,
        )


def test_mission_generates_unique_ids():
    a = _bind_scheduled()
    b = _bind_scheduled()
    assert a.mission_id != b.mission_id


# ── Lifecycle transitions ────────────────────────────────────────


def test_happy_path_draft_to_completed():
    m = _bind_scheduled()
    m.transition_to(MissionState.APPROVED)
    m.transition_to(MissionState.SCHEDULED)
    m.transition_to(MissionState.RUNNING)
    m.transition_to(MissionState.COMPLETED)
    assert m.state is MissionState.COMPLETED


def test_running_to_failed_allowed():
    m = _bind_scheduled()
    m.transition_to(MissionState.APPROVED)
    m.transition_to(MissionState.SCHEDULED)
    m.transition_to(MissionState.RUNNING)
    m.transition_to(MissionState.FAILED)
    assert m.state is MissionState.FAILED


def test_draft_to_running_is_illegal():
    m = _bind_scheduled()
    with pytest.raises(MissionLifecycleError):
        m.transition_to(MissionState.RUNNING)


def test_completed_is_terminal():
    m = _bind_scheduled()
    m.transition_to(MissionState.APPROVED)
    m.transition_to(MissionState.SCHEDULED)
    m.transition_to(MissionState.RUNNING)
    m.transition_to(MissionState.COMPLETED)
    with pytest.raises(MissionLifecycleError):
        m.transition_to(MissionState.RUNNING)


def test_failed_is_terminal():
    m = _bind_scheduled()
    m.transition_to(MissionState.APPROVED)
    m.transition_to(MissionState.SCHEDULED)
    m.transition_to(MissionState.RUNNING)
    m.transition_to(MissionState.FAILED)
    with pytest.raises(MissionLifecycleError):
        m.transition_to(MissionState.COMPLETED)


def test_scheduled_can_go_back_to_approved():
    # Used by the Curator/Promoter later when a mission is paused
    # pending a fine-tune swap.
    m = _bind_scheduled()
    m.transition_to(MissionState.APPROVED)
    m.transition_to(MissionState.SCHEDULED)
    m.transition_to(MissionState.APPROVED)
    assert m.state is MissionState.APPROVED


# ── Works for every mode fixture ─────────────────────────────────


def test_event_driven_fixture_can_be_bound_as_mission():
    bp = make_synthetic_event_driven_blueprint()
    m = Mission.from_blueprint(
        bp,
        project_id="proj-test",
        slots={},  # taxonomy has a default
        registry=default_registry,
    )
    assert m.slots["taxonomy"] == ["billing", "bug", "other"]


def test_batch_fixture_can_be_bound_as_mission():
    bp = make_synthetic_batch_blueprint()
    m = Mission.from_blueprint(
        bp,
        project_id="proj-test",
        slots={
            "extraction_schema": {
                "type": "object",
                "properties": {"invoice_no": {"type": "string"}},
            },
        },
        registry=default_registry,
    )
    assert m.slots["confidence_threshold"] == 0.85
