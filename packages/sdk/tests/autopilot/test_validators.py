# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the validator registry and built-in validators."""

from __future__ import annotations

import pytest

from sagewai.autopilot.errors import SlotValidationError
from sagewai.autopilot.validators import (
    ValidatorRegistry,
    default_registry,
    validate_cron,
    validate_json_schema,
    validate_url_list,
)

# ── Registry behavior ──────────────────────────────────────────────


def test_registry_registers_and_looks_up_validator():
    reg = ValidatorRegistry()

    def my_validator(value, *, slot_name):
        return value

    reg.register("my_validator", my_validator)
    assert reg.get("my_validator") is my_validator


def test_registry_raises_on_unknown_name():
    reg = ValidatorRegistry()
    with pytest.raises(KeyError, match="unknown"):
        reg.get("unknown")


def test_registry_rejects_duplicate_registration():
    reg = ValidatorRegistry()
    reg.register("x", lambda v, slot_name: v)
    with pytest.raises(ValueError, match="already registered"):
        reg.register("x", lambda v, slot_name: v)


def test_default_registry_has_builtins():
    for name in ("cron", "url_list", "json_schema"):
        assert default_registry.get(name) is not None


# ── validate_cron ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "expr",
    [
        "0 9 * * 1-5",
        "*/5 * * * *",
        "0 0 1 * *",
        "30 14 * * 0,6",
    ],
)
def test_validate_cron_accepts_valid_expressions(expr: str):
    assert validate_cron(expr, slot_name="schedule") == expr


@pytest.mark.parametrize(
    "expr",
    [
        "",
        "not a cron",
        "60 * * * *",  # minute out of range
        "0 25 * * *",  # hour out of range
        "0 9 * *",  # only 4 fields
    ],
)
def test_validate_cron_rejects_invalid_expressions(expr: str):
    with pytest.raises(SlotValidationError):
        validate_cron(expr, slot_name="schedule")


# ── validate_url_list ──────────────────────────────────────────────


def test_validate_url_list_accepts_list_of_urls():
    urls = ["https://paperclip.ing", "https://openclaw.ai"]
    assert validate_url_list(urls, slot_name="vendors") == urls


def test_validate_url_list_rejects_non_list():
    with pytest.raises(SlotValidationError, match="must be a list"):
        validate_url_list("https://paperclip.ing", slot_name="vendors")


def test_validate_url_list_rejects_empty_list():
    with pytest.raises(SlotValidationError, match="must not be empty"):
        validate_url_list([], slot_name="vendors")


def test_validate_url_list_rejects_invalid_urls():
    with pytest.raises(SlotValidationError, match="invalid url"):
        validate_url_list(["not-a-url"], slot_name="vendors")


def test_validate_url_list_rejects_non_string_item():
    with pytest.raises(SlotValidationError, match="not a string"):
        validate_url_list([123], slot_name="vendors")


# ── validate_json_schema ───────────────────────────────────────────


def test_validate_json_schema_accepts_valid_object_schema():
    schema = {
        "type": "object",
        "properties": {"invoice_no": {"type": "string"}},
        "required": ["invoice_no"],
    }
    assert validate_json_schema(schema, slot_name="extraction_schema") == schema


def test_validate_json_schema_rejects_non_dict():
    with pytest.raises(SlotValidationError, match="must be a dict"):
        validate_json_schema("string schema", slot_name="extraction_schema")


def test_validate_json_schema_rejects_missing_type():
    with pytest.raises(SlotValidationError, match="missing 'type'"):
        validate_json_schema({"properties": {}}, slot_name="extraction_schema")


def test_validate_json_schema_rejects_non_dict_properties():
    with pytest.raises(SlotValidationError, match="'properties' must be a dict"):
        validate_json_schema(
            {"type": "object", "properties": "not a dict"},
            slot_name="extraction_schema",
        )
