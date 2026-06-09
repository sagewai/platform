# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Postgres-backed tenant connection store.

This mirrors the file-backed ``ConnectionStore`` invariants while storing rows
in the tenant-scoped ``connection`` table. Reads are exact-scope reads; callers
compose project + org-shared inheritance explicitly so shadowing remains visible
at the route/execution boundary.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.connections.errors import (
    ConnectionNotFoundError,
    DuplicateDisplayNameError,
    IdCollisionError,
    UnknownProtocolError,
)
from sagewai.connections.models import Connection, valid_protocol_ids
from sagewai.connections.store import DefaultKeyExtractor
from sagewai.db import factory
from sagewai.db.models import Base, ConnectionModel

_tbl = ConnectionModel.__table__


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return _utcnow_iso()
    return str(value)


def _no_default_key(_protocol_data: dict[str, Any]) -> str | None:
    return None


class PostgresConnectionStore:
    """Tenant-scoped CRUD on connection records."""

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
        *,
        engine: AsyncEngine | None = None,
        allowed_protocols: tuple[str, ...] | None = None,
        default_key_for: dict[str, DefaultKeyExtractor] | None = None,
    ) -> None:
        self._engine = engine or factory.get_engine()
        self._allowed_protocols = (
            allowed_protocols if allowed_protocols is not None else valid_protocol_ids()
        )
        self._default_key_for = default_key_for or {}

    async def init(self) -> None:
        """Create the schema on SQLite (no-op on Postgres; Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

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

    def _row_to_connection(self, row: Any) -> Connection:
        return Connection(
            id=row["id"],
            protocol=row["protocol"],
            project_id=row["project_id"],
            display_name=row["display_name"],
            tags=tuple(row["tags"] or []),
            credentials_backend=row["credentials_backend"],
            status=row["status"],
            last_tested_at=row["last_tested_at"],
            last_test_ok=row["last_test_ok"],
            is_default=bool(row["is_default"]),
            created_at=_dt_iso(row["created_at"]),
            updated_at=_dt_iso(row["updated_at"]),
            last_error=row["last_error"],
            protocol_data=row["protocol_data"] or {},
        )

    async def list(
        self,
        project_id: str | None,
        *,
        protocol: str | None = None,
        tag: str | None = None,
    ) -> list[Connection]:
        stmt = select(_tbl).where(_tbl.c.project_id.is_(None) if project_id is None else _tbl.c.project_id == project_id)
        if protocol is not None:
            stmt = stmt.where(_tbl.c.protocol == protocol)
        stmt = stmt.order_by(_tbl.c.created_at)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        out = [self._row_to_connection(r) for r in rows]
        if tag is not None:
            out = [c for c in out if tag in c.tags]
        return out

    async def get(self, connection_id: str) -> Connection | None:
        async with self._engine.connect() as conn:
            row = (
                (await conn.execute(select(_tbl).where(_tbl.c.id == connection_id)))
                .mappings()
                .first()
            )
        return self._row_to_connection(row) if row is not None else None

    async def create(
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
        self._validate_protocol(protocol)
        async with self._engine.begin() as conn:
            duplicate = (
                await conn.execute(
                    select(_tbl.c.id).where(
                        _tbl.c.project_id.is_(None)
                        if project_id is None
                        else _tbl.c.project_id == project_id,
                        _tbl.c.protocol == protocol,
                        _tbl.c.display_name == display_name,
                    )
                )
            ).first()
            if duplicate is not None:
                raise DuplicateDisplayNameError(
                    f"({project_id!r}, {protocol!r}, {display_name!r}) already exists"
                )
            new_id = id_override if id_override is not None else self._generate_id(protocol)
            if (
                await conn.execute(select(_tbl.c.id).where(_tbl.c.id == new_id))
            ).first() is not None:
                raise IdCollisionError(new_id)
            new_key = self._default_key_value(protocol, protocol_data)
            peers = (
                (
                    await conn.execute(
                        select(_tbl.c.is_default, _tbl.c.protocol_data).where(
                            _tbl.c.project_id.is_(None)
                            if project_id is None
                            else _tbl.c.project_id == project_id,
                            _tbl.c.protocol == protocol,
                        )
                    )
                )
                .mappings()
                .all()
            )
            has_existing_default = any(
                bool(row["is_default"])
                and self._default_key_value(protocol, row["protocol_data"] or {}) == new_key
                for row in peers
            )
            now = datetime.now(timezone.utc)
            row = {
                "id": new_id,
                "project_id": project_id,
                "protocol": protocol,
                "display_name": display_name,
                "tags": list(tags),
                "credentials_backend": credentials_backend,
                "status": "pending",
                "last_tested_at": None,
                "last_test_ok": None,
                "is_default": not has_existing_default,
                "last_error": None,
                "protocol_data": protocol_data,
                "created_at": now,
                "updated_at": now,
            }
            await conn.execute(_tbl.insert().values(**row))
        return self._row_to_connection(row)

    async def update(self, connection_id: str, **fields: Any) -> Connection:
        bad = set(fields) - self._UPDATABLE_FIELDS
        if bad:
            raise ValueError(f"unknown updatable fields: {sorted(bad)}")
        async with self._engine.begin() as conn:
            target = (
                (await conn.execute(select(_tbl).where(_tbl.c.id == connection_id)))
                .mappings()
                .first()
            )
            if target is None:
                raise ConnectionNotFoundError(connection_id)
            if "display_name" in fields:
                dup = (
                    await conn.execute(
                        select(_tbl.c.id).where(
                            _tbl.c.id != connection_id,
                            _tbl.c.project_id.is_(None)
                            if target["project_id"] is None
                            else _tbl.c.project_id == target["project_id"],
                            _tbl.c.protocol == target["protocol"],
                            _tbl.c.display_name == fields["display_name"],
                        )
                    )
                ).first()
                if dup is not None:
                    raise DuplicateDisplayNameError(
                        f"({target['project_id']!r}, {target['protocol']!r}, "
                        f"{fields['display_name']!r}) already exists"
                    )
            if "tags" in fields:
                fields["tags"] = list(fields["tags"])
            fields["updated_at"] = datetime.now(timezone.utc)
            await conn.execute(sa_update(_tbl).where(_tbl.c.id == connection_id).values(**fields))
            row = (
                (await conn.execute(select(_tbl).where(_tbl.c.id == connection_id)))
                .mappings()
                .one()
            )
        return self._row_to_connection(row)

    async def delete(self, connection_id: str) -> bool:
        async with self._engine.begin() as conn:
            result = await conn.execute(sa_delete(_tbl).where(_tbl.c.id == connection_id))
        return result.rowcount == 1

    async def set_default(self, connection_id: str) -> Connection:
        async with self._engine.begin() as conn:
            target = (
                (await conn.execute(select(_tbl).where(_tbl.c.id == connection_id)))
                .mappings()
                .first()
            )
            if target is None:
                raise ConnectionNotFoundError(connection_id)
            target_key = self._default_key_value(
                target["protocol"], target["protocol_data"] or {}
            )
            rows = (
                (
                    await conn.execute(
                        select(_tbl.c.id, _tbl.c.protocol_data).where(
                            _tbl.c.project_id.is_(None)
                            if target["project_id"] is None
                            else _tbl.c.project_id == target["project_id"],
                            _tbl.c.protocol == target["protocol"],
                        )
                    )
                )
                .mappings()
                .all()
            )
            now = datetime.now(timezone.utc)
            for row in rows:
                if (
                    row["id"] != connection_id
                    and self._default_key_value(target["protocol"], row["protocol_data"] or {})
                    == target_key
                ):
                    await conn.execute(
                        sa_update(_tbl)
                        .where(_tbl.c.id == row["id"])
                        .values(is_default=False, updated_at=now)
                    )
            await conn.execute(
                sa_update(_tbl)
                .where(_tbl.c.id == connection_id)
                .values(is_default=True, updated_at=now)
            )
            fresh = (
                (await conn.execute(select(_tbl).where(_tbl.c.id == connection_id)))
                .mappings()
                .one()
            )
        return self._row_to_connection(fresh)

    async def update_test_result(self, connection_id: str, *, ok: bool) -> Connection:
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa_update(_tbl)
                .where(_tbl.c.id == connection_id)
                .values(
                    last_tested_at=now.isoformat(),
                    last_test_ok=ok,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise ConnectionNotFoundError(connection_id)
            row = (
                (await conn.execute(select(_tbl).where(_tbl.c.id == connection_id)))
                .mappings()
                .one()
            )
        return self._row_to_connection(row)


__all__ = ["PostgresConnectionStore"]
