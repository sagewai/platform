# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Named validator registry and built-in slot validators.

A validator is any callable with signature
``(value: Any, *, slot_name: str) -> Any`` that returns the normalized
value on success and raises :class:`SlotValidationError` on failure.

The :data:`default_registry` ships with the validators needed by the
three reference-blueprint shapes (scheduled, event-driven, batch).
Additional validators can be registered by callers.
"""

from __future__ import annotations

import re
from typing import Any, Protocol
from urllib.parse import urlparse

from .errors import SlotValidationError


class Validator(Protocol):
    def __call__(self, value: Any, *, slot_name: str) -> Any: ...


class ValidatorRegistry:
    """A name-keyed registry of validators."""

    def __init__(self) -> None:
        self._validators: dict[str, Validator] = {}

    def register(self, name: str, validator: Validator) -> None:
        if name in self._validators:
            raise ValueError(f"validator {name!r} already registered")
        self._validators[name] = validator

    def get(self, name: str) -> Validator:
        if name not in self._validators:
            raise KeyError(f"unknown validator {name!r}")
        return self._validators[name]


# ── Built-in validators ───────────────────────────────────────────


_CRON_FIELD_BOUNDS = [
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 7),  # day of week (0 and 7 both mean Sunday)
]

_CRON_TOKEN_RE = re.compile(r"^(\*|(\d+)(-\d+)?)(/\d+)?(,(\*|(\d+)(-\d+)?)(/\d+)?)*$")


def _cron_field_in_range(token: str, lo: int, hi: int) -> bool:
    if token == "*" or token.startswith("*/"):
        return True
    for part in token.split(","):
        part = part.split("/", 1)[0]
        if "-" in part:
            a, b = part.split("-", 1)
            if not (lo <= int(a) <= hi and lo <= int(b) <= hi):
                return False
        else:
            if not (lo <= int(part) <= hi):
                return False
    return True


def validate_cron(value: Any, *, slot_name: str) -> str:
    """Accept 5-field POSIX cron expressions only."""
    if not isinstance(value, str) or not value.strip():
        raise SlotValidationError(slot_name, "cron expression must be a non-empty string")
    fields = value.split()
    if len(fields) != 5:
        raise SlotValidationError(
            slot_name, f"cron expression must have 5 fields, got {len(fields)}"
        )
    for token, (lo, hi) in zip(fields, _CRON_FIELD_BOUNDS):
        if not _CRON_TOKEN_RE.match(token):
            raise SlotValidationError(slot_name, f"invalid cron token: {token!r}")
        if not _cron_field_in_range(token, lo, hi):
            raise SlotValidationError(slot_name, f"cron field {token!r} out of range [{lo},{hi}]")
    return value


def validate_url_list(value: Any, *, slot_name: str) -> list[str]:
    """Accept a non-empty list of valid http(s) URLs."""
    if not isinstance(value, list):
        raise SlotValidationError(slot_name, "must be a list")
    if len(value) == 0:
        raise SlotValidationError(slot_name, "must not be empty")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise SlotValidationError(slot_name, "invalid url (not a string)")
        parsed = urlparse(item)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SlotValidationError(slot_name, f"invalid url: {item!r}")
        normalized.append(item)
    return normalized


def validate_json_schema(value: Any, *, slot_name: str) -> dict[str, Any]:
    """Accept a minimally-valid JSON Schema object.

    This is intentionally loose — full JSON-Schema-draft validation is
    out of scope. We require: it's a dict, it has a 'type' key, and
    if properties are declared they form a dict.
    """
    if not isinstance(value, dict):
        raise SlotValidationError(slot_name, "must be a dict")
    if "type" not in value:
        raise SlotValidationError(slot_name, "missing 'type' key")
    if "properties" in value and not isinstance(value["properties"], dict):
        raise SlotValidationError(slot_name, "'properties' must be a dict")
    return value


# ── Default registry ──────────────────────────────────────────────


default_registry = ValidatorRegistry()
default_registry.register("cron", validate_cron)
default_registry.register("url_list", validate_url_list)
default_registry.register("json_schema", validate_json_schema)
