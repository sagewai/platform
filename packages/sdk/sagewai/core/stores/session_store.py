# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PostgreSQL-backed session store using asyncpg.

Stores SessionRecord as rows in the sessions table.
Uses raw asyncpg queries for performance (no ORM overhead).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from sagewai.core.session import SessionRecord

logger = logging.getLogger(__name__)


class PostgresSessionStore:
    """PostgreSQL-backed SessionStore using asyncpg.

    Parameters
    ----------
    database_url:
        PostgreSQL connection string (asyncpg format).
    pool:
        Existing asyncpg connection pool. If provided, database_url is ignored.
    """

    def __init__(
        self,
        database_url: str | None = None,
        pool: Any = None,
    ) -> None:
        self._database_url = database_url
        self._pool = pool

    async def initialize(self) -> None:
        """Create the connection pool if not provided."""
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(self._database_url)

    async def save(self, record: SessionRecord) -> None:
        """Upsert a session record."""
        await self._pool.execute(
            """
            INSERT INTO sessions (
                session_id, project_id, agent_name, messages,
                summary, memory_keys, created_at, updated_at
            ) VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb, to_timestamp($7), to_timestamp($8))
            ON CONFLICT (session_id, project_id)
            DO UPDATE SET
                messages = EXCLUDED.messages,
                summary = EXCLUDED.summary,
                memory_keys = EXCLUDED.memory_keys,
                updated_at = EXCLUDED.updated_at
            """,
            record.session_id,
            record.project_id or "",
            record.agent_name,
            json.dumps(record.messages),
            record.summary,
            json.dumps(record.memory_keys),
            record.created_at,
            time.time(),
        )

    async def load(self, session_id: str, project_id: str | None = None) -> SessionRecord | None:
        """Load a session record by ID and project."""
        row = await self._pool.fetchrow(
            """
            SELECT session_id, project_id, agent_name, messages,
                   summary, memory_keys, created_at, updated_at
            FROM sessions
            WHERE session_id = $1 AND project_id = $2
            """,
            session_id,
            project_id or "",
        )
        if row is None:
            return None
        return self._row_to_record(row)

    async def list_sessions(
        self, project_id: str | None = None, limit: int = 20
    ) -> list[SessionRecord]:
        """List sessions for a project, ordered by most recent."""
        rows = await self._pool.fetch(
            """
            SELECT session_id, project_id, agent_name, messages,
                   summary, memory_keys, created_at, updated_at
            FROM sessions
            WHERE project_id = $1
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            project_id or "",
            limit,
        )
        return [self._row_to_record(row) for row in rows]

    async def delete(self, session_id: str, project_id: str | None = None) -> None:
        """Delete a session record."""
        await self._pool.execute(
            "DELETE FROM sessions WHERE session_id = $1 AND project_id = $2",
            session_id,
            project_id or "",
        )

    @staticmethod
    def _row_to_record(row: Any) -> SessionRecord:
        """Convert a database row to a SessionRecord."""
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
