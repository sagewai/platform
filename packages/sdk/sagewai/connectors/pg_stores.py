# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SQLAlchemy Core implementations of connector stores.

All four classes work on both SQLite (default, via aiosqlite) and PostgreSQL
(via asyncpg).  They replace the former asyncpg-only implementations that
relied on a raw asyncpg connection pool.

Back-compat note
----------------
The old constructors accepted a single positional ``pool`` argument.  The
new constructors accept:

* ``engine=`` — a pre-built :class:`AsyncEngine` (highest priority)
* ``database_url=`` — passed to :func:`sagewai.db.engine.create_engine`
* ``pool`` — **ignored**; accepted as a positional arg so existing call
  sites do not raise ``TypeError``

When none of the above is supplied, ``factory.get_engine()`` is used
(process-wide default, usually SQLite unless ``SAGEWAI_DATABASE_URL`` is set).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.connectors.base import ConnectorStatus, TokenSet
from sagewai.connectors.stores import CredentialStore, CursorStore, OAuthTokenStore
from sagewai.core.context import resolve_project_id
from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.engine import create_engine
from sagewai.db.models import (
    Base,
    ConnectorCredentialModel,
    ConnectorCursorModel,
    CustomConnectorModel,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_engine(
    database_url: str | None,
    pool: Any,
    engine: AsyncEngine | None,
) -> AsyncEngine:
    """Resolve the engine from the three possible constructor inputs."""
    if engine is not None:
        return engine
    if database_url is not None:
        return create_engine(database_url)
    # pool is ignored (asyncpg back-compat)
    return factory.get_engine()


async def _init_sqlite(engine: AsyncEngine) -> None:
    """Bootstrap schema when using SQLite; no-op on PostgreSQL (Alembic owns it)."""
    if engine.dialect.name == "sqlite":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


# ---------------------------------------------------------------------------
# 1. PostgresCredentialStore
# ---------------------------------------------------------------------------


class PostgresCredentialStore(CredentialStore):
    """Credential store backed by the ``connector_credentials`` table.

    Uses SQLAlchemy Core — works on both SQLite and PostgreSQL.
    """

    def __init__(
        self,
        database_url: str | None = None,
        pool: Any = None,
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._engine = _make_engine(database_url, pool, engine)

    async def init(self) -> None:
        """Bootstrap the schema on SQLite; no-op on PostgreSQL."""
        await _init_sqlite(self._engine)

    # keep old name as alias for callers that used initialize()
    async def initialize(self) -> None:
        await self.init()

    async def get(
        self, connector_name: str, *, project_id: str | None = None
    ) -> dict[str, str] | None:
        pid = resolve_project_id(project_id)
        tbl = ConnectorCredentialModel.__table__
        stmt = select(tbl.c.config).where(
            tbl.c.project_id == pid,
            tbl.c.connector_name == connector_name,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.first()
        if row is None:
            return None
        val = row[0]
        if val is None:
            return None
        # SQLite may return a JSON string if the column is stored as TEXT
        if isinstance(val, str):
            import json
            return json.loads(val)
        return val

    async def put(
        self,
        connector_name: str,
        credentials: dict[str, str],
        *,
        project_id: str | None = None,
    ) -> None:
        pid = resolve_project_id(project_id)
        now = datetime.now(timezone.utc)
        tbl = ConnectorCredentialModel.__table__
        values = {
            "id": str(uuid.uuid4()),
            "project_id": pid,
            "connector_name": connector_name,
            "display_name": connector_name,
            "config": credentials,
            "status": "configured",
            "created_at": now,
            "updated_at": now,
        }
        stmt = upsert(
            tbl,
            values,
            index_elements=["project_id", "connector_name"],
            set_={
                "config": credentials,
                "status": "configured",
                "updated_at": now,
            },
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def delete(
        self, connector_name: str, *, project_id: str | None = None
    ) -> None:
        pid = resolve_project_id(project_id)
        tbl = ConnectorCredentialModel.__table__
        stmt = sa_delete(tbl).where(
            tbl.c.project_id == pid,
            tbl.c.connector_name == connector_name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def list_all(
        self, *, project_id: str | None = None
    ) -> list[ConnectorStatus]:
        pid = resolve_project_id(project_id)
        tbl = ConnectorCredentialModel.__table__
        stmt = select(tbl.c.connector_name, tbl.c.status, tbl.c.config).where(
            tbl.c.project_id == pid
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [
            ConnectorStatus(
                connector_name=row["connector_name"],
                status=row["status"] or "not_configured",
                has_credentials=row["config"] is not None,
            )
            for row in rows
        ]


# ---------------------------------------------------------------------------
# 2. PostgresOAuthTokenStore
# ---------------------------------------------------------------------------


class PostgresOAuthTokenStore(OAuthTokenStore):
    """OAuth token store backed by the ``connector_credentials`` table.

    Uses SQLAlchemy Core — works on both SQLite and PostgreSQL.
    The credential row must already exist (created by PostgresCredentialStore.put)
    before save_token() can update it.
    """

    def __init__(
        self,
        database_url: str | None = None,
        pool: Any = None,
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._engine = _make_engine(database_url, pool, engine)

    async def init(self) -> None:
        """Bootstrap the schema on SQLite; no-op on PostgreSQL."""
        await _init_sqlite(self._engine)

    async def initialize(self) -> None:
        await self.init()

    async def get_token(
        self, connector_name: str, *, project_id: str | None = None
    ) -> TokenSet | None:
        pid = resolve_project_id(project_id)
        tbl = ConnectorCredentialModel.__table__
        stmt = select(
            tbl.c.access_token,
            tbl.c.refresh_token,
            tbl.c.token_type,
            tbl.c.expires_at,
            tbl.c.scope,
        ).where(
            tbl.c.project_id == pid,
            tbl.c.connector_name == connector_name,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        if row is None or row["access_token"] is None:
            return None
        return TokenSet(
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            token_type=row["token_type"] or "Bearer",
            expires_at=row["expires_at"],
            scope=row["scope"],
        )

    async def save_token(
        self,
        connector_name: str,
        token_set: TokenSet,
        *,
        project_id: str | None = None,
    ) -> None:
        pid = resolve_project_id(project_id)
        now = datetime.now(timezone.utc)
        tbl = ConnectorCredentialModel.__table__
        stmt = (
            tbl.update()
            .where(
                tbl.c.project_id == pid,
                tbl.c.connector_name == connector_name,
            )
            .values(
                access_token=token_set.access_token,
                refresh_token=token_set.refresh_token,
                token_type=token_set.token_type,
                expires_at=token_set.expires_at,
                scope=token_set.scope,
                updated_at=now,
            )
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def needs_refresh(
        self, connector_name: str, *, project_id: str | None = None
    ) -> bool:
        token = await self.get_token(connector_name, project_id=project_id)
        if token is None:
            return True
        if token.expires_at is None:
            return False
        exp = token.expires_at
        # SQLite returns naive datetimes from timezone-aware columns; normalise.
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp


# ---------------------------------------------------------------------------
# 3. PostgresCustomConnectorStore
# ---------------------------------------------------------------------------


class PostgresCustomConnectorStore:
    """CRUD store for user-defined custom connectors in the ``custom_connectors`` table.

    Uses SQLAlchemy Core — works on both SQLite and PostgreSQL.
    """

    def __init__(
        self,
        database_url: str | None = None,
        pool: Any = None,
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._engine = _make_engine(database_url, pool, engine)

    async def init(self) -> None:
        """Bootstrap the schema on SQLite; no-op on PostgreSQL."""
        await _init_sqlite(self._engine)

    async def initialize(self) -> None:
        await self.init()

    async def save(
        self, spec: dict[str, Any], *, project_id: str | None = None
    ) -> None:
        """Insert or update a custom connector spec."""
        pid = resolve_project_id(project_id)
        now = datetime.now(timezone.utc)
        tbl = CustomConnectorModel.__table__
        values = {
            "name": spec["name"],
            "project_id": pid,
            "display_name": spec.get("display_name", spec["name"]),
            "category": spec.get("category", "custom"),
            "description": spec.get("description", ""),
            "auth_type": spec.get("auth_type", "api_key"),
            "auth_fields_json": spec.get("auth_fields", []),
            "mcp_command_json": spec.get("mcp_command", []),
            "docs_url": spec.get("docs_url"),
            "agent_description": spec.get("agent_description", ""),
            "example_prompt": spec.get("example_prompt", ""),
            "oauth_authorize_url": spec.get("oauth_authorize_url"),
            "oauth_token_url": spec.get("oauth_token_url"),
            "oauth_scopes_json": spec.get("oauth_scopes", []),
            "supports_webhook": spec.get("supports_webhook", False),
            "supports_listener": spec.get("supports_listener", False),
            "supports_poller": spec.get("supports_poller", False),
            "created_at": now,
            "updated_at": now,
        }
        # ON CONFLICT targets the PRIMARY KEY ("name").  Migration 001
        # creates custom_connectors with name as PK and no (project_id, name)
        # unique constraint, so index_elements must be ["name"] — not
        # ["project_id", "name"] — to match the real production schema.
        stmt = upsert(
            tbl,
            values,
            index_elements=["name"],
            set_={
                "project_id": pid,
                "display_name": values["display_name"],
                "category": values["category"],
                "description": values["description"],
                "auth_type": values["auth_type"],
                "auth_fields_json": values["auth_fields_json"],
                "mcp_command_json": values["mcp_command_json"],
                "docs_url": values["docs_url"],
                "agent_description": values["agent_description"],
                "example_prompt": values["example_prompt"],
                "oauth_authorize_url": values["oauth_authorize_url"],
                "oauth_token_url": values["oauth_token_url"],
                "oauth_scopes_json": values["oauth_scopes_json"],
                "supports_webhook": values["supports_webhook"],
                "supports_listener": values["supports_listener"],
                "supports_poller": values["supports_poller"],
                "updated_at": now,
            },
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get(
        self, name: str, *, project_id: str | None = None
    ) -> dict[str, Any] | None:
        pid = resolve_project_id(project_id)
        tbl = CustomConnectorModel.__table__
        stmt = select(tbl).where(
            tbl.c.project_id == pid,
            tbl.c.name == name,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        return self._row_to_dict(row) if row else None

    async def list_all(
        self, *, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        pid = resolve_project_id(project_id)
        tbl = CustomConnectorModel.__table__
        stmt = (
            select(tbl)
            .where(tbl.c.project_id == pid)
            .order_by(tbl.c.name)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [self._row_to_dict(r) for r in rows]

    async def delete(
        self, name: str, *, project_id: str | None = None
    ) -> None:
        pid = resolve_project_id(project_id)
        tbl = CustomConnectorModel.__table__
        stmt = sa_delete(tbl).where(
            tbl.c.project_id == pid,
            tbl.c.name == name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        auth_fields = row["auth_fields_json"]
        if isinstance(auth_fields, str):
            import json
            auth_fields = json.loads(auth_fields)
        mcp_command = row["mcp_command_json"]
        if isinstance(mcp_command, str):
            import json
            mcp_command = json.loads(mcp_command)
        oauth_scopes = row["oauth_scopes_json"]
        if isinstance(oauth_scopes, str):
            import json
            oauth_scopes = json.loads(oauth_scopes)
        return {
            "name": row["name"],
            "display_name": row["display_name"],
            "category": row["category"],
            "description": row["description"],
            "auth_type": row["auth_type"],
            "auth_fields": auth_fields or [],
            "mcp_command": mcp_command or [],
            "docs_url": row["docs_url"],
            "agent_description": row["agent_description"],
            "example_prompt": row["example_prompt"],
            "oauth_authorize_url": row["oauth_authorize_url"],
            "oauth_token_url": row["oauth_token_url"],
            "oauth_scopes": oauth_scopes or [],
            "supports_webhook": row["supports_webhook"],
            "supports_listener": row["supports_listener"],
            "supports_poller": row["supports_poller"],
        }


# ---------------------------------------------------------------------------
# 4. PostgresCursorStore
# ---------------------------------------------------------------------------


class PostgresCursorStore(CursorStore):
    """Cursor store backed by the ``connector_cursors`` table.

    Uses SQLAlchemy Core — works on both SQLite and PostgreSQL.
    """

    def __init__(
        self,
        database_url: str | None = None,
        pool: Any = None,
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._engine = _make_engine(database_url, pool, engine)

    async def init(self) -> None:
        """Bootstrap the schema on SQLite; no-op on PostgreSQL."""
        await _init_sqlite(self._engine)

    async def initialize(self) -> None:
        await self.init()

    async def get(
        self, connector: str, channel: str, *, project_id: str | None = None
    ) -> str | None:
        pid = resolve_project_id(project_id)
        tbl = ConnectorCursorModel.__table__
        stmt = select(tbl.c.cursor_value).where(
            tbl.c.project_id == pid,
            tbl.c.connector_name == connector,
            tbl.c.channel == channel,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.first()
        return row[0] if row else None

    async def set(
        self,
        connector: str,
        channel: str,
        cursor: str,
        *,
        project_id: str | None = None,
    ) -> None:
        pid = resolve_project_id(project_id)
        now = datetime.now(timezone.utc)
        tbl = ConnectorCursorModel.__table__
        values = {
            "project_id": pid,
            "connector_name": connector,
            "channel": channel,
            "cursor_value": cursor,
            "updated_at": now,
        }
        stmt = upsert(
            tbl,
            values,
            index_elements=["project_id", "connector_name", "channel"],
            set_={
                "cursor_value": cursor,
                "updated_at": now,
            },
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
