# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Generic CRUD layer for connection records.

Persists records to a single JSON file (``$SAGEWAI_HOME/config/connections.json``
in production; injected path in tests). Atomic writes via the same
tempfile + ``os.replace`` + 0o600 chmod pattern used by the existing
inference-providers store. ``protocol_data`` is opaque to the store —
plugin validation (PR2) and credentials encryption (PR3) layer on top.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sagewai import home
from sagewai.connections.errors import (
    ConnectionNotFoundError,
    DuplicateDisplayNameError,
    IdCollisionError,
    StoreCorruptedError,
    UnknownProtocolError,
    UnsupportedStoreVersionError,
)
from sagewai.connections.models import Connection, valid_protocol_ids


_STORE_VERSION = 2


def _default_store_path() -> Path:
    """Resolve the on-disk store path with env override.

    ``SAGEWAI_CONNECTIONS_FILE`` overrides; default is
    ``$SAGEWAI_HOME/config/connections.json``. PR4 wires this into the admin via a
    module-level helper so tests can monkeypatch.
    """
    override = os.environ.get("SAGEWAI_CONNECTIONS_FILE")
    if override:
        return Path(override).expanduser()
    return home.config_dir() / "connections.json"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Default-key extractor used by ``set_default`` / ``create``: returns the
# tuple element that must be unique among defaults inside one
# ``(project_id, protocol)`` group. PR2 plugins inject real extractors
# (``provider`` for oauth2, ``provider_key`` for inference); PR1 default
# returns None — at most one default per (project_id, protocol).
DefaultKeyExtractor = Callable[[dict[str, Any]], str | None]


def _no_default_key(_protocol_data: dict[str, Any]) -> str | None:
    return None


class ConnectionStore:
    """File-backed CRUD on connection records.

    Instantiate with a path; not a singleton. The admin and CLI both
    construct fresh instances pointing at the same file (atomic-write
    semantics make this safe). Tests inject ``tmp_path / "store.json"``.
    """

    _UPDATABLE_FIELDS: frozenset[str] = frozenset(
        {
            "display_name",
            "tags",
            "credentials_backend",
            "protocol_data",
            "status",
            "last_error",
        }
    )

    def __init__(
        self,
        store_path: Path,
        *,
        allowed_protocols: tuple[str, ...] | None = None,
        default_key_for: dict[str, DefaultKeyExtractor] | None = None,
    ) -> None:
        self._path = Path(store_path)
        self._allowed_protocols = (
            allowed_protocols if allowed_protocols is not None else valid_protocol_ids()
        )
        self._default_key_for = default_key_for or {}

    # ── raw read/write ──────────────────────────────────────────────

    def _read_raw(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"version": _STORE_VERSION, "connections": []}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StoreCorruptedError(f"{self._path}: malformed JSON: {exc}") from exc
        if not isinstance(data, dict) or "version" not in data:
            raise StoreCorruptedError(f"{self._path}: missing required 'version' key")
        version = data.get("version")
        if version != _STORE_VERSION:
            raise UnsupportedStoreVersionError(
                f"{self._path}: version {version!r} not supported by this build "
                f"(expected {_STORE_VERSION})"
            )
        if "connections" not in data or not isinstance(data["connections"], list):
            raise StoreCorruptedError(
                f"{self._path}: missing or non-array 'connections' key"
            )
        return data

    def _write_raw(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=self._path.parent,
            prefix=".connections.",
            suffix=".tmp",
        ) as tmp:
            json.dump(data, tmp, indent=2, default=str)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, self._path)

    # ── row ↔ Connection adapter ────────────────────────────────────

    def _row_to_connection(self, row: dict[str, Any]) -> Connection:
        return Connection(
            id=row["id"],
            protocol=row["protocol"],
            project_id=row.get("project_id"),
            display_name=row["display_name"],
            tags=tuple(row.get("tags", [])),
            credentials_backend=row.get("credentials_backend"),
            status=row["status"],
            last_tested_at=row.get("last_tested_at"),
            last_test_ok=row.get("last_test_ok"),
            is_default=row.get("is_default", False),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_error=row.get("last_error"),
            protocol_data=row.get("protocol_data", {}),
        )

    # ── helpers ─────────────────────────────────────────────────────

    def _validate_protocol(self, protocol: str) -> None:
        if protocol not in self._allowed_protocols:
            raise UnknownProtocolError(
                f"protocol {protocol!r} not in allowed set {self._allowed_protocols!r}"
            )

    def _default_key_value(
        self, protocol: str, protocol_data: dict[str, Any]
    ) -> str | None:
        extractor = self._default_key_for.get(protocol, _no_default_key)
        return extractor(protocol_data)

    def _generate_id(self, protocol: str) -> str:
        return f"conn_{protocol}_{secrets.token_hex(8)}"

    # ── reads ───────────────────────────────────────────────────────

    def list(
        self,
        project_id: str | None,
        *,
        protocol: str | None = None,
        tag: str | None = None,
    ) -> list[Connection]:
        """Return connections in ``project_id``, optionally filtered."""
        rows = self._read_raw()["connections"]
        out: list[Connection] = []
        for row in rows:
            if row.get("project_id") != project_id:
                continue
            if protocol is not None and row.get("protocol") != protocol:
                continue
            if tag is not None and tag not in row.get("tags", []):
                continue
            out.append(self._row_to_connection(row))
        return out

    def get(self, connection_id: str) -> Connection | None:
        """Return one connection by id, or None."""
        for row in self._read_raw()["connections"]:
            if row.get("id") == connection_id:
                return self._row_to_connection(row)
        return None

    # ── writes ──────────────────────────────────────────────────────

    def create(
        self,
        *,
        protocol: str,
        project_id: str | None,
        display_name: str,
        tags: list[str],
        protocol_data: dict[str, Any],
        credentials_backend: dict[str, Any] | None = None,
        id_override: str | None = None,
    ) -> Connection:
        """Create + persist a new connection.

        Raises ``UnknownProtocolError`` if ``protocol`` is not in the
        configured allowed-protocols set. Raises
        ``DuplicateDisplayNameError`` if ``(project_id, protocol,
        display_name)`` already exists. Raises ``IdCollisionError`` if
        ``id_override`` is given and an existing connection already has
        that id.

        When ``id_override`` is None (the default), a fresh id is
        generated via ``_generate_id(protocol)``.

        Marks the new connection ``is_default=True`` if no other
        connection in the same ``(project_id, protocol,
        default_key(protocol_data))`` group is already default.
        """
        self._validate_protocol(protocol)
        raw = self._read_raw()
        rows = raw["connections"]

        # Duplicate-display-name check
        for row in rows:
            if (
                row.get("project_id") == project_id
                and row.get("protocol") == protocol
                and row.get("display_name") == display_name
            ):
                raise DuplicateDisplayNameError(
                    f"({project_id!r}, {protocol!r}, {display_name!r}) already exists"
                )

        # Id-collision check (only when id_override is supplied)
        if id_override is not None:
            for row in rows:
                if row.get("id") == id_override:
                    raise IdCollisionError(id_override)

        # Default-flag computation
        new_key = self._default_key_value(protocol, protocol_data)
        has_existing_default_in_group = any(
            row.get("project_id") == project_id
            and row.get("protocol") == protocol
            and row.get("is_default", False)
            and self._default_key_value(protocol, row.get("protocol_data", {})) == new_key
            for row in rows
        )
        is_default = not has_existing_default_in_group

        new_id = id_override if id_override is not None else self._generate_id(protocol)
        now = _utcnow_iso()
        row = {
            "id": new_id,
            "kind": "connection",
            "protocol": protocol,
            "project_id": project_id,
            "display_name": display_name,
            "tags": list(tags),
            "credentials_backend": credentials_backend,
            "status": "pending",
            "last_tested_at": None,
            "last_test_ok": None,
            "is_default": is_default,
            "created_at": now,
            "updated_at": now,
            "last_error": None,
            "protocol_data": protocol_data,
        }
        rows.append(row)
        self._write_raw(raw)
        return self._row_to_connection(row)

    def update(self, connection_id: str, **fields: Any) -> Connection:
        """Partial update. Returns the updated record.

        Updatable fields: ``display_name``, ``tags``,
        ``credentials_backend``, ``protocol_data``, ``status``,
        ``last_error``. Other fields are immutable post-create. Pass
        an unknown field name to raise ``ValueError``.

        Raises ``ConnectionNotFoundError`` if no record matches the id.
        Raises ``DuplicateDisplayNameError`` if a display-name change
        would collide.
        """
        bad = set(fields) - self._UPDATABLE_FIELDS
        if bad:
            raise ValueError(f"unknown updatable fields: {sorted(bad)}")
        raw = self._read_raw()
        rows = raw["connections"]
        target_index = None
        for i, row in enumerate(rows):
            if row.get("id") == connection_id:
                target_index = i
                break
        if target_index is None:
            raise ConnectionNotFoundError(connection_id)
        target = rows[target_index]

        # Display-name collision check (if changing)
        if "display_name" in fields:
            new_name = fields["display_name"]
            for j, row in enumerate(rows):
                if j == target_index:
                    continue
                if (
                    row.get("project_id") == target.get("project_id")
                    and row.get("protocol") == target.get("protocol")
                    and row.get("display_name") == new_name
                ):
                    raise DuplicateDisplayNameError(
                        f"({target.get('project_id')!r}, "
                        f"{target.get('protocol')!r}, {new_name!r}) already exists"
                    )

        # Apply
        if "tags" in fields:
            fields["tags"] = list(fields["tags"])
        target.update(fields)
        target["updated_at"] = _utcnow_iso()
        self._write_raw(raw)
        return self._row_to_connection(target)

    def delete(self, connection_id: str) -> bool:
        """Hard-delete a connection. Returns True if a row was removed."""
        raw = self._read_raw()
        rows = raw["connections"]
        before = len(rows)
        raw["connections"] = [r for r in rows if r.get("id") != connection_id]
        if len(raw["connections"]) == before:
            return False
        self._write_raw(raw)
        return True

    def set_default(self, connection_id: str) -> Connection:
        """Mark this connection as default for its (project, protocol,
        default_key) group. Unset prior default in the same group.

        Raises ``ConnectionNotFoundError`` if no row matches.
        """
        raw = self._read_raw()
        rows = raw["connections"]
        target = None
        for row in rows:
            if row.get("id") == connection_id:
                target = row
                break
        if target is None:
            raise ConnectionNotFoundError(connection_id)
        group = (
            target.get("project_id"),
            target.get("protocol"),
            self._default_key_value(target["protocol"], target.get("protocol_data", {})),
        )
        now = _utcnow_iso()
        for row in rows:
            if row is target:
                continue
            row_group = (
                row.get("project_id"),
                row.get("protocol"),
                self._default_key_value(row["protocol"], row.get("protocol_data", {})),
            )
            if row_group == group and row.get("is_default", False):
                row["is_default"] = False
                row["updated_at"] = now
        target["is_default"] = True
        target["updated_at"] = now
        self._write_raw(raw)
        return self._row_to_connection(target)

    def update_test_result(self, connection_id: str, *, ok: bool) -> Connection:
        """Record the outcome of a plugin ``test()`` call.

        Updates ``last_tested_at`` and ``last_test_ok``; does not touch
        ``status`` (plugins manage status via ``update``).
        """
        raw = self._read_raw()
        target = None
        for row in raw["connections"]:
            if row.get("id") == connection_id:
                target = row
                break
        if target is None:
            raise ConnectionNotFoundError(connection_id)
        now = _utcnow_iso()
        target["last_tested_at"] = now
        target["last_test_ok"] = ok
        target["updated_at"] = now
        self._write_raw(raw)
        return self._row_to_connection(target)


__all__ = ["ConnectionStore", "DefaultKeyExtractor"]
