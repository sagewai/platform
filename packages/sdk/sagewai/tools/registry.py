# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tool catalog registry.

Loads every ``catalog/*.yaml`` into a frozen :class:`CatalogEntry` and
exposes lookup helpers. Validates each file against ``_schema.json`` at
load time. Duplicate ``id`` or schema violations are fatal — the SDK
refuses to import if the catalog is malformed.

Validation order per file (two-pass): YAML parse → schema validation →
duplicate-id check → filename-must-match-id check. Duplicate-id is detected
before the filename mismatch so two well-formed files declaring the same id
raise a clear "duplicate id" error regardless of their filenames.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator

_CATALOG_DIR = Path(__file__).resolve().parent / "catalog"
_SCHEMA_PATH = _CATALOG_DIR / "_schema.json"


class CatalogError(RuntimeError):
    """Catalog is malformed (schema violation, duplicate id, parse error)."""


class ToolNotFoundError(KeyError):
    """No catalog entry with this id."""


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    id: str
    version: str
    title: str
    description: str
    category: str
    kind: str
    sandbox_tier: str
    exec_: Mapping[str, Any]
    scopes: frozenset[str]
    setup: Mapping[str, Any]
    schemas: Mapping[str, Any] = field(default_factory=dict)

    @property
    def auth_complexity(self) -> str:
        return self.setup["auth_complexity"]


_entries: dict[str, CatalogEntry] = {}
_loaded: bool = False


def _reset() -> None:
    """Test-only: clear cached load."""
    global _entries, _loaded
    _entries = {}
    _loaded = False


def load() -> None:
    """Load (or reload) every YAML in the catalog dir.

    Two-pass strategy:
    - Pass 1: parse YAML, validate schema, detect duplicate ids.
    - Pass 2: verify each file's stem matches its declared id.

    This ordering ensures a "duplicate id" error surfaces clearly when two
    well-formed files declare the same id, even if filenames differ.
    """
    global _loaded
    if _loaded:
        return
    schema = json.loads((_CATALOG_DIR / "_schema.json").read_text())
    validator = Draft202012Validator(schema)

    # Pass 1: parse + schema-validate + duplicate-id detection
    parsed: list[tuple[Path, dict[str, Any]]] = []
    seen_ids: dict[str, Path] = {}
    for path in sorted(_CATALOG_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise CatalogError(f"{path.name}: YAML parse error: {exc}") from exc
        if not isinstance(raw, dict):
            raise CatalogError(f"{path.name}: top-level must be a mapping")
        errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.path))
        if errors:
            msgs = "; ".join(f"{list(e.path)}: {e.message}" for e in errors)
            raise CatalogError(f"{path.name}: schema violation: {msgs}")
        tool_id = raw["id"]
        if tool_id in seen_ids:
            raise CatalogError(
                f"{path.name}: duplicate id {tool_id!r} (first seen in {seen_ids[tool_id].name})"
            )
        seen_ids[tool_id] = path
        parsed.append((path, raw))

    # Pass 2: filename-must-match-id check
    new: dict[str, CatalogEntry] = {}
    for path, raw in parsed:
        if path.stem != raw["id"]:
            raise CatalogError(
                f"{path.name}: id {raw['id']!r} does not match filename stem {path.stem!r}"
            )
        new[raw["id"]] = CatalogEntry(
            id=raw["id"],
            version=raw["version"],
            title=raw["title"],
            description=raw["description"],
            category=raw["category"],
            kind=raw["kind"],
            sandbox_tier=raw["sandbox_tier"],
            exec_=raw["exec"],
            scopes=frozenset(raw["scopes"]),
            setup=raw["setup"],
            schemas=raw.get("schemas", {}),
        )

    _entries.clear()
    _entries.update(new)
    _loaded = True


def lookup(tool_id: str) -> CatalogEntry:
    if not _loaded:
        load()
    try:
        return _entries[tool_id]
    except KeyError as exc:
        raise ToolNotFoundError(tool_id) from exc


def lookup_or_none(tool_id: str) -> CatalogEntry | None:
    if not _loaded:
        load()
    return _entries.get(tool_id)


def list_by_tier(tier: str) -> list[CatalogEntry]:
    if not _loaded:
        load()
    return [e for e in _entries.values() if e.auth_complexity == tier]


def list_by_category(category: str) -> list[CatalogEntry]:
    if not _loaded:
        load()
    return [e for e in _entries.values() if e.category == category]


def scopes_for(tool_id: str) -> frozenset[str]:
    """Return scopes for a catalogued tool, or empty frozenset if not catalogued."""
    if not _loaded:
        load()
    entry = _entries.get(tool_id)
    return entry.scopes if entry is not None else frozenset()


__all__ = [
    "CatalogEntry",
    "CatalogError",
    "ToolNotFoundError",
    "load",
    "lookup",
    "lookup_or_none",
    "list_by_tier",
    "list_by_category",
    "scopes_for",
]
