# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Verify the optional setup.credential_fields schema addition is backward-compatible."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "sagewai/tools/catalog/_schema.json"
)


def _validator():
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))


def _minimal_entry(**overrides):
    base = {
        "id": "demo",
        "version": "0.1.0",
        "title": "Demo",
        "description": "x",
        "category": "test",
        "kind": "sdk",
        "sandbox_tier": "SANDBOXED",
        "exec": {"sdk": {"entrypoint": "pkg.mod:fn"}},
        "scopes": [],
        "setup": {"auth_complexity": "none", "body": "x"},
    }
    base.update(overrides)
    return base


def test_entry_without_credential_fields_validates():
    """Batch-1 entries (no credential_fields) must still validate."""
    errors = list(_validator().iter_errors(_minimal_entry()))
    assert errors == [], errors


def test_entry_with_credential_fields_validates():
    entry = _minimal_entry()
    entry["setup"] = {
        "auth_complexity": "api_key",
        "credential_fields": [
            {
                "name": "API_TOKEN",
                "label": "API Token",
                "type": "password",
                "description": "Bearer token for service X",
            }
        ],
        "body": "Paste your token...",
    }
    errors = list(_validator().iter_errors(entry))
    assert errors == [], errors


def test_credential_field_rejects_bad_type():
    entry = _minimal_entry()
    entry["setup"] = {
        "auth_complexity": "api_key",
        "credential_fields": [
            {"name": "X", "label": "X", "type": "telepathy"}
        ],
        "body": "x",
    }
    errors = list(_validator().iter_errors(entry))
    assert errors


def test_credential_field_requires_name_and_label():
    entry = _minimal_entry()
    entry["setup"] = {
        "auth_complexity": "api_key",
        "credential_fields": [{"type": "password"}],
        "body": "x",
    }
    errors = list(_validator().iter_errors(entry))
    assert errors
