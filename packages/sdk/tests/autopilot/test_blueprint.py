"""Tests for the top-level Blueprint model."""

from __future__ import annotations

import pytest

from sagewai.autopilot._types import Mode
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.errors import BlueprintValidationError, SlotValidationError
from sagewai.autopilot.validators import default_registry

from .fixtures import (
    make_synthetic_batch_blueprint,
    make_synthetic_event_driven_blueprint,
    make_synthetic_scheduled_blueprint,
)

# ── Construction ──────────────────────────────────────────────────


def test_scheduled_fixture_constructs():
    bp = make_synthetic_scheduled_blueprint()
    assert bp.mode is Mode.SCHEDULED
    assert bp.id.startswith("SYNTHETIC_")


def test_event_driven_fixture_constructs():
    bp = make_synthetic_event_driven_blueprint()
    assert bp.mode is Mode.EVENT_DRIVEN
    assert bp.learning_loop_target is not None
    assert bp.learning_loop_target.trigger_after_labeled_samples == 500


def test_batch_fixture_constructs():
    bp = make_synthetic_batch_blueprint()
    assert bp.mode is Mode.BATCH
    assert "out_review" in {n.id for n in bp.agent_graph.nodes}


def test_blueprint_rejects_blank_id():
    # model_copy does not re-run validators in Pydantic v2, so we
    # force re-validation via model_validate on the dumped dict.
    bp = make_synthetic_scheduled_blueprint()
    data = bp.model_dump(mode="python")
    data["id"] = ""
    with pytest.raises(Exception):
        Blueprint.model_validate(data)


def test_blueprint_rejects_slots_that_collide_with_optional_slots():
    bp = make_synthetic_scheduled_blueprint()
    data = bp.model_dump(mode="python")
    # Copy the one required slot into optional_slots to force a collision.
    data["optional_slots"] = dict(data["required_slots"])
    with pytest.raises(BlueprintValidationError, match="collide"):
        Blueprint.model_validate(data)


# ── validate_slots integration ────────────────────────────────────


def test_validate_slots_passes_for_valid_inputs():
    bp = make_synthetic_scheduled_blueprint()
    got = bp.validate_slots(
        {"vendors": ["https://paperclip.ing"]},
        registry=default_registry,
    )
    assert got["vendors"] == ["https://paperclip.ing"]
    assert got["schedule"] == "0 9 * * 1-5"  # default filled in


def test_validate_slots_raises_on_missing_required():
    bp = make_synthetic_scheduled_blueprint()
    with pytest.raises(SlotValidationError, match="vendors"):
        bp.validate_slots({}, registry=default_registry)


def test_validate_slots_raises_on_invalid_cron():
    bp = make_synthetic_scheduled_blueprint()
    with pytest.raises(SlotValidationError, match="schedule"):
        bp.validate_slots(
            {"vendors": ["https://paperclip.ing"], "schedule": "not a cron"},
            registry=default_registry,
        )


def test_validate_slots_drops_unknown_keys_with_error():
    bp = make_synthetic_scheduled_blueprint()
    with pytest.raises(BlueprintValidationError, match="unknown slot"):
        bp.validate_slots(
            {"vendors": ["https://paperclip.ing"], "made_up": 42},
            registry=default_registry,
        )


# ── Round-trip ────────────────────────────────────────────────────


def test_blueprint_json_round_trip():
    original = make_synthetic_event_driven_blueprint()
    as_json = original.model_dump_json()
    restored = Blueprint.model_validate_json(as_json)
    assert restored == original
