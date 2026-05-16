# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the plan-preview card builder."""

from __future__ import annotations

import pytest

from sagewai.autopilot.routing.preview import build_preview
from tests.autopilot.fixtures import (
    make_synthetic_batch_blueprint,
    make_synthetic_event_driven_blueprint,
    make_synthetic_scheduled_blueprint,
)

# ── Scheduled blueprint ────────────────────────────────────────────


@pytest.fixture()
def scheduled():
    return make_synthetic_scheduled_blueprint()


@pytest.fixture()
def event_driven():
    return make_synthetic_event_driven_blueprint()


@pytest.fixture()
def batch():
    return make_synthetic_batch_blueprint()


def test_preview_contains_blueprint_title(scheduled):
    preview = build_preview(scheduled, slots={})
    assert scheduled.title in preview


def test_preview_contains_blueprint_description(scheduled):
    preview = build_preview(scheduled, slots={})
    assert scheduled.description in preview


def test_preview_contains_category(scheduled):
    preview = build_preview(scheduled, slots={})
    assert scheduled.category in preview


def test_preview_contains_mode(scheduled):
    preview = build_preview(scheduled, slots={})
    assert scheduled.mode.value in preview


def test_preview_contains_slot_name_and_value(scheduled):
    slots = {"vendors": "https://openai.com", "schedule": "0 9 * * 1-5"}
    preview = build_preview(scheduled, slots=slots)
    assert "vendors" in preview
    assert "https://openai.com" in preview
    assert "schedule" in preview
    assert "0 9 * * 1-5" in preview


def test_preview_renders_none_slots_as_unset(scheduled):
    slots = {"vendors": None, "schedule": None}
    preview = build_preview(scheduled, slots=slots)
    assert "vendors" in preview
    assert "(unset)" in preview or "None" in preview


def test_preview_contains_tools_required(scheduled):
    preview = build_preview(scheduled, slots={})
    for tool in scheduled.tools_required:
        assert tool in preview


def test_preview_empty_slots_dict(scheduled):
    preview = build_preview(scheduled, slots={})
    assert isinstance(preview, str)
    assert len(preview) > 0


# ── Event-driven blueprint ─────────────────────────────────────────


def test_preview_event_driven_title(event_driven):
    preview = build_preview(event_driven, slots={})
    assert event_driven.title in preview


def test_preview_event_driven_category(event_driven):
    preview = build_preview(event_driven, slots={})
    assert "support" in preview


def test_preview_event_driven_mode(event_driven):
    preview = build_preview(event_driven, slots={})
    assert "event_driven" in preview


# ── Batch blueprint ────────────────────────────────────────────────


def test_preview_batch_contains_extraction_schema_slot(batch):
    slots = {"extraction_schema": '{"name": "string"}', "confidence_threshold": "0.9"}
    preview = build_preview(batch, slots=slots)
    assert "extraction_schema" in preview
    assert "confidence_threshold" in preview


# ── Return type invariants ─────────────────────────────────────────


@pytest.mark.parametrize(
    "bp_factory",
    [
        make_synthetic_scheduled_blueprint,
        make_synthetic_event_driven_blueprint,
        make_synthetic_batch_blueprint,
    ],
)
def test_preview_always_returns_non_empty_string(bp_factory):
    bp = bp_factory()
    preview = build_preview(bp, slots={})
    assert isinstance(preview, str)
    assert preview.strip() != ""


def test_preview_does_not_raise_on_extra_slot_keys(scheduled):
    """Extra keys in slots are rendered; blueprint.validate_slots is NOT called here."""
    preview = build_preview(scheduled, slots={"vendors": "x", "unknown_key": "y"})
    assert isinstance(preview, str)
