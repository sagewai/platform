# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Crypto-agnostic helpers for walking and redacting provider secret fields.

Shared between the file-backed AdminStateFile and the Postgres provider store.
This module intentionally imports NO crypto — callers that need encryption or
decryption keep those routines in their own layer (e.g. state_file.py).
"""

from __future__ import annotations

import copy
from typing import Any

# Canonical set of field names that hold secret material in a provider config.
PROVIDER_SECRET_FIELDS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "key",
    "secret",
    "client_secret",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "passphrase",
    "private_key",
)

# Marker prefix written by Fernet encryption.  Kept here so callers can detect
# encrypted-vs-plaintext without importing the crypto layer.
FERNET_PREFIX = "fernet:"


def walk_secret_fields(config: Any, fn: Any) -> None:
    """Call ``fn(parent_dict, key)`` for every secret-named key at any depth.

    ``config`` is the provider's *config* dict (not the whole provider record).
    The walker recurses into nested dicts and lists so that secrets buried in
    sub-objects (e.g. ``{"nested": {"api_key": "..."}}`` ) are visited too.
    """
    if isinstance(config, dict):
        for k, v in list(config.items()):
            if k in PROVIDER_SECRET_FIELDS:
                fn(config, k)
            else:
                walk_secret_fields(v, fn)
    elif isinstance(config, list):
        for item in config:
            walk_secret_fields(item, fn)


def has_plaintext_secret(record: dict[str, Any]) -> bool:
    """Return True if any secret field in ``record["config"]`` is a non-empty
    plaintext string (i.e. does not start with the fernet prefix)."""
    found: list[bool] = []

    def _check(parent: dict[str, Any], k: str) -> None:
        v = parent.get(k)
        if isinstance(v, str) and v and not v.startswith(FERNET_PREFIX):
            found.append(True)

    walk_secret_fields(record.get("config"), _check)
    return bool(found)


def is_encrypted(record: dict[str, Any]) -> bool:
    """Return True if any secret field in ``record["config"]`` starts with the
    fernet prefix."""
    found: list[bool] = []

    def _check(parent: dict[str, Any], k: str) -> None:
        v = parent.get(k)
        if isinstance(v, str) and v.startswith(FERNET_PREFIX):
            found.append(True)

    walk_secret_fields(record.get("config"), _check)
    return bool(found)


def redact_secrets(record: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *record* with all secret fields removed.

    For each secret field that had a truthy value, a ``<field>_set: True``
    sentinel is added to the same dict so callers know the field existed.
    The *original* record is never mutated.
    """
    out = copy.deepcopy(record)

    def _red(parent: dict[str, Any], k: str) -> None:
        val = parent.pop(k, None)  # always strip secret material
        if val:
            parent[k + "_set"] = True

    walk_secret_fields(out.get("config"), _red)
    return out
