# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for SlotSpec and slot validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.autopilot.errors import SlotValidationError
from sagewai.autopilot.slots import SlotSpec
from sagewai.autopilot.validators import default_registry


def test_slotspec_required_by_default():
    spec = SlotSpec(type_="str", description="vendor name")
    assert spec.required is True
    assert spec.default is None


def test_slotspec_optional_with_default():
    spec = SlotSpec(
        type_="float",
        description="monthly budget in USD",
        required=False,
        default=5.0,
    )
    assert spec.required is False
    assert spec.default == 5.0


def test_slotspec_validate_dispatches_to_named_validator():
    spec = SlotSpec(type_="list[str]", description="vendors", validator_name="url_list")
    got = spec.validate_value(
        ["https://paperclip.ing"],
        slot_name="vendors",
        registry=default_registry,
    )
    assert got == ["https://paperclip.ing"]


def test_slotspec_validate_raises_on_invalid_value():
    spec = SlotSpec(type_="list[str]", description="vendors", validator_name="url_list")
    with pytest.raises(SlotValidationError):
        spec.validate_value([], slot_name="vendors", registry=default_registry)


def test_slotspec_validate_passes_through_when_no_validator():
    spec = SlotSpec(type_="str", description="channel")
    got = spec.validate_value("slack", slot_name="output_channel", registry=default_registry)
    assert got == "slack"


def test_slotspec_rejects_missing_required_value():
    spec = SlotSpec(type_="str", description="vendor")
    with pytest.raises(SlotValidationError, match="required"):
        spec.validate_value(None, slot_name="vendor", registry=default_registry)


def test_slotspec_missing_optional_returns_default():
    spec = SlotSpec(
        type_="str",
        description="channel",
        required=False,
        default="admin_ui",
    )
    got = spec.validate_value(None, slot_name="output_channel", registry=default_registry)
    assert got == "admin_ui"


def test_slotspec_unknown_validator_name_is_rejected_eagerly():
    spec = SlotSpec(type_="str", description="x", validator_name="does_not_exist")
    with pytest.raises(SlotValidationError, match="does_not_exist"):
        spec.validate_value("x", slot_name="x", registry=default_registry)


def test_slotspec_model_is_frozen():
    spec = SlotSpec(type_="str", description="x")
    with pytest.raises(ValidationError):
        spec.required = False  # type: ignore[misc]
