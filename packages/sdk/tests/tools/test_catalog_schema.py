# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sagewai/tools/catalog/_schema.json"


def _validator():
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))


def test_schema_loads_and_is_valid_draft202012():
    Draft202012Validator.check_schema(json.loads(SCHEMA_PATH.read_text()))


def test_schema_accepts_minimal_sdk_entry():
    entry = {
        "id": "fetch_url",
        "version": "0.1.0",
        "title": "Fetch URL",
        "description": "GET a URL and return the body.",
        "category": "network",
        "kind": "sdk",
        "sandbox_tier": "SANDBOXED",
        "exec": {"sdk": {"entrypoint": "sagewai.tools.builtins:fetch_url"}},
        "scopes": ["network.outbound.fetch"],
        "setup": {"auth_complexity": "none", "body": "Bundled. No setup."},
    }
    errors = list(_validator().iter_errors(entry))
    assert errors == [], errors


def test_schema_rejects_tagged_union_mismatch():
    entry = {
        "id": "bogus", "version": "0.1.0", "title": "Bogus", "description": "x",
        "category": "network", "kind": "http", "sandbox_tier": "SANDBOXED",
        "exec": {"mcp": {"server_ref": "stdio:nope"}},
        "scopes": [],
        "setup": {"auth_complexity": "none", "body": "x"},
    }
    errors = list(_validator().iter_errors(entry))
    assert errors, "schema must reject kind/exec mismatch"


def test_schema_rejects_unknown_kind():
    entry = {
        "id": "bogus", "version": "0.1.0", "title": "Bogus", "description": "x",
        "category": "network", "kind": "telepathy", "sandbox_tier": "SANDBOXED",
        "exec": {}, "scopes": [],
        "setup": {"auth_complexity": "none", "body": "x"},
    }
    errors = list(_validator().iter_errors(entry))
    assert errors


def test_schema_rejects_bad_id_pattern():
    entry = {
        "id": "Bad-ID", "version": "0.1.0", "title": "x", "description": "x",
        "category": "network", "kind": "sdk", "sandbox_tier": "SANDBOXED",
        "exec": {"sdk": {"entrypoint": "a:b"}}, "scopes": [],
        "setup": {"auth_complexity": "none", "body": "x"},
    }
    errors = list(_validator().iter_errors(entry))
    assert any("id" in str(e.path) or "id" in str(e.message) for e in errors)


def test_schema_rejects_malformed_scope_string():
    entry = {
        "id": "bogus", "version": "0.1.0", "title": "x", "description": "x",
        "category": "network", "kind": "sdk", "sandbox_tier": "SANDBOXED",
        "exec": {"sdk": {"entrypoint": "a:b"}}, "scopes": ["not_dotted"],
        "setup": {"auth_complexity": "none", "body": "x"},
    }
    errors = list(_validator().iter_errors(entry))
    assert errors
