# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Session store backed by SQLAlchemy Core — works on both SQLite and PostgreSQL.

One implementation replaces the former asyncpg-only PostgresSessionStore.
The class name and all public method signatures are unchanged so callers
require no modification.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.core.session import SessionRecord
from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.engine import create_engine
from sagewai.db.models import Base, SessionModel

logger = logging.getLogger(__name__)


class PostgresSessionStore:
    """Session store using SQLAlchemy Core — SQLite (default) or PostgreSQL.

    Parameters
    ----------
    engine:
        Pre-built :class:`AsyncEngine`.  When supplied, *database_url* is
        ignored and *pool* is unused.
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
        if engine is not None:
            self._engine: AsyncEngine = engine
        elif database_url is not None:
            self._engine = create_engine(database_url)
        else:
            self._engine = factory.get_engine()
        # pool is intentionally ignored; SQLAlchemy engine owns connection pooling

    async def initialize(self) -> None:
        """Bootstrap the schema when using SQLite; no-op on PostgreSQL (Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    async def save(self, record: SessionRecord) -> None:
        """Upsert a session record."""
        now = datetime.now(timezone.utc)
        created_dt = datetime.fromtimestamp(record.created_at, tz=timezone.utc)

        values = {
            "session_id": record.session_id,
            "project_id": record.project_id or "",
            "agent_name": record.agent_name,
            "messages": record.messages,
            "summary": record.summary,
            "memory_keys": record.memory_keys,
            "created_at": created_dt,
            "updated_at": now,
        }
        stmt = upsert(
            SessionModel.__table__,
            values,
            index_elements=["session_id", "project_id"],
            set_={
                "messages": record.messages,
                "summary": record.summary,
                "memory_keys": record.memory_keys,
                "updated_at": now,
            },
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def load(self, session_id: str, project_id: str | None = None) -> SessionRecord | None:
        """Load a session record by ID and project."""
        tbl = SessionModel.__table__
        stmt = select(tbl).where(
            tbl.c.session_id == session_id,
            tbl.c.project_id == (project_id or ""),
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        if row is None:
            return None
        return self._row_to_record(row)

    async def list_sessions(
        self, project_id: str | None = None, limit: int = 20
    ) -> list[SessionRecord]:
        """List sessions for a project, ordered by most recent."""
        tbl = SessionModel.__table__
        stmt = (
            select(tbl)
            .where(tbl.c.project_id == (project_id or ""))
            .order_by(tbl.c.updated_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [self._row_to_record(row) for row in rows]

    async def delete(self, session_id: str, project_id: str | None = None) -> None:
        """Delete a session record."""
        stmt = sa_delete(SessionModel.__table__).where(
            SessionModel.session_id == session_id,
            SessionModel.project_id == (project_id or ""),
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    @staticmethod
    def _row_to_record(row: Any) -> SessionRecord:
        """Convert a SQLAlchemy row mapping to a SessionRecord."""
        messages = row["messages"]
        if isinstance(messages, str):
            messages = json.loads(messages)

        memory_keys = row["memory_keys"]
        if isinstance(memory_keys, str):
            memory_keys = json.loads(memory_keys)

        project_id = row["project_id"]
        if project_id == "":
            project_id = None

        created_at = row["created_at"]
        if hasattr(created_at, "timestamp"):
            created_at = created_at.timestamp()

        updated_at = row["updated_at"]
        if hasattr(updated_at, "timestamp"):
            updated_at = updated_at.timestamp()

        return SessionRecord(
            session_id=row["session_id"],
            project_id=project_id,
            agent_name=row["agent_name"],
            messages=messages,
            summary=row["summary"],
            memory_keys=memory_keys,
            created_at=created_at,
            updated_at=updated_at,
        )


# Dialect-neutral alias for future use
SessionStoreSQL = PostgresSessionStore
