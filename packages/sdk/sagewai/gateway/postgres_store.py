# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PostgreSQL-backed token store using SQLAlchemy Core.

One implementation replaces the former asyncpg-only PostgresTokenStore.
Works on both SQLite (default) and PostgreSQL via a shared AsyncEngine.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.engine import create_engine
from sagewai.db.models import AgentAccessTokenModel, Base
from sagewai.gateway.models import AccessToken, TokenStatus

logger = logging.getLogger(__name__)


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


class PostgresTokenStore:
    """TokenStore backed by SQLAlchemy Core — SQLite (default) or PostgreSQL.

    Parameters
    ----------
    engine:
        Pre-built :class:`AsyncEngine`.  When supplied, *database_url* and
        *pool* are ignored.
    database_url:
        Connection string passed to :func:`sagewai.db.engine.create_engine`.
        Ignored when *engine* is supplied.
    pool:
        Accepted for backwards-compatibility with callers that previously
        passed an asyncpg pool.  It is **not used** by this implementation;
        the SQLAlchemy engine manages its own connection pool.
    """

    def __init__(
        self,
        database_url: str | None = None,
        pool: Any = None,  # kept for API back-compat; not used
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._engine: AsyncEngine = _make_engine(database_url, pool, engine)

    async def init(self) -> None:
        """Bootstrap schema when using SQLite; no-op on PostgreSQL (Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Dispose the engine (releases pooled connections)."""
        await self._engine.dispose()

    async def save(self, token: AccessToken) -> None:
        """Upsert an access token — ON CONFLICT (token_id) DO UPDATE."""
        tbl = AgentAccessTokenModel.__table__
        expires_dt = datetime.fromtimestamp(token.expires_at, tz=timezone.utc)
        used_dt = (
            datetime.fromtimestamp(token.used_at, tz=timezone.utc)
            if token.used_at is not None
            else None
        )
        created_dt = datetime.fromtimestamp(token.created_at, tz=timezone.utc)

        values = {
            "token_id": token.token_id,
            "token_hash": token.token_hash,
            "token_suffix": token.token_suffix,
            "agent_name": token.agent_name,
            "grantor_id": token.grantor_id,
            "scopes": token.scopes,
            "status": token.status.value,
            "single_use": token.single_use,
            "expires_at": expires_dt,
            "used_at": used_dt,
            "created_at": created_dt,
        }
        stmt = upsert(
            tbl,
            values,
            index_elements=["token_id"],
            set_={
                "status": token.status.value,
                "used_at": used_dt,
            },
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get(self, token_id: str) -> AccessToken | None:
        """Get a token by ID."""
        tbl = AgentAccessTokenModel.__table__
        stmt = select(tbl).where(tbl.c.token_id == token_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        return self._row_to_token(row) if row is not None else None

    async def get_by_hash(self, token_hash: str) -> AccessToken | None:
        """Get a token by its SHA-256 hash."""
        tbl = AgentAccessTokenModel.__table__
        stmt = select(tbl).where(tbl.c.token_hash == token_hash)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        return self._row_to_token(row) if row is not None else None

    async def list_tokens(
        self, agent_name: str | None = None, limit: int = 50
    ) -> list[AccessToken]:
        """List tokens, optionally filtered by agent name."""
        tbl = AgentAccessTokenModel.__table__
        stmt = select(tbl).order_by(tbl.c.created_at.desc()).limit(limit)
        if agent_name is not None:
            stmt = stmt.where(tbl.c.agent_name == agent_name)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [self._row_to_token(row) for row in rows]

    async def revoke(self, token_id: str) -> None:
        """Revoke a token by ID."""
        tbl = AgentAccessTokenModel.__table__
        stmt = (
            tbl.update()
            .where(tbl.c.token_id == token_id)
            .values(status=TokenStatus.REVOKED.value)
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def delete(self, token_id: str) -> None:
        """Delete a token by ID."""
        stmt = sa_delete(AgentAccessTokenModel.__table__).where(
            AgentAccessTokenModel.__table__.c.token_id == token_id
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def cleanup_expired(self) -> int:
        """Remove expired tokens. Returns count removed."""
        tbl = AgentAccessTokenModel.__table__
        now_dt = datetime.now(timezone.utc)
        # Count first
        count_stmt = select(func.count()).select_from(tbl).where(tbl.c.expires_at < now_dt)
        async with self._engine.connect() as conn:
            count = (await conn.execute(count_stmt)).scalar_one()

        delete_stmt = sa_delete(tbl).where(tbl.c.expires_at < now_dt)
        async with self._engine.begin() as conn:
            await conn.execute(delete_stmt)

        logger.info("Cleaned up %d expired access tokens", count)
        return count

    @staticmethod
    def _dt_to_float(dt: Any) -> float:
        """Convert a datetime (possibly naive from SQLite) to a Unix timestamp."""
        if not hasattr(dt, "timestamp"):
            return float(dt)
        if dt.tzinfo is None:
            # SQLite returns naive datetimes; treat them as UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()

    @staticmethod
    def _row_to_token(row: Any) -> AccessToken:
        """Convert a SQLAlchemy row mapping to an AccessToken."""
        expires_at = PostgresTokenStore._dt_to_float(row["expires_at"])

        used_at_raw = row["used_at"]
        used_at = (
            PostgresTokenStore._dt_to_float(used_at_raw)
            if used_at_raw is not None
            else None
        )

        created_at = PostgresTokenStore._dt_to_float(row["created_at"])

        scopes = row["scopes"]
        if isinstance(scopes, str):
            import json
            scopes = json.loads(scopes)
        if scopes is None:
            scopes = []

        return AccessToken(
            token_id=row["token_id"],
            token_hash=row["token_hash"],
            token_suffix=row["token_suffix"] or "",
            agent_name=row["agent_name"],
            grantor_id=row["grantor_id"],
            scopes=list(scopes),
            status=TokenStatus(row["status"]),
            single_use=row["single_use"],
            expires_at=expires_at,
            used_at=used_at,
            created_at=created_at,
        )
