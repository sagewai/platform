# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Postgres-backed store for persisting playground agent specs."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class PostgresAgentStore:
    """Persists playground AgentSpec records to the playground_agents table.

    Uses an asyncpg connection pool for all operations.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def save(self, name: str, spec_dict: dict[str, Any]) -> None:
        """Insert or update an agent spec."""
        # Strip transient provider fields that shouldn't be persisted
        persistable = {
            k: v
            for k, v in spec_dict.items()
            if k not in ("api_base", "api_key", "custom_llm_provider")
        }
        spec_json = json.dumps(persistable)
        await self._pool.execute(
            """
            INSERT INTO playground_agents (name, spec, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (name) DO UPDATE
              SET spec = $2, updated_at = now()
            """,
            name,
            spec_json,
        )

    async def delete(self, name: str) -> bool:
        """Delete an agent spec. Returns True if a row was deleted."""
        result = await self._pool.execute(
            "DELETE FROM playground_agents WHERE name = $1",
            name,
        )
        return result == "DELETE 1"

    async def rename(self, old_name: str, new_name: str) -> bool:
        """Rename an agent. Returns True on success."""
        # Load, update name in spec JSON, delete old, insert new
        row = await self._pool.fetchrow(
            "SELECT spec FROM playground_agents WHERE name = $1",
            old_name,
        )
        if row is None:
            return False
        spec_dict = json.loads(row["spec"])
        spec_dict["name"] = new_name
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM playground_agents WHERE name = $1",
                    old_name,
                )
                await conn.execute(
                    """
                    INSERT INTO playground_agents (name, spec, updated_at)
                    VALUES ($1, $2, now())
                    """,
                    new_name,
                    json.dumps(spec_dict),
                )
        return True

    async def list_all(self) -> list[dict[str, Any]]:
        """Load all persisted agent specs."""
        rows = await self._pool.fetch(
            "SELECT name, spec FROM playground_agents ORDER BY created_at"
        )
        results = []
        for row in rows:
            try:
                results.append(json.loads(row["spec"]))
            except (json.JSONDecodeError, KeyError):
                logger.warning("Corrupt agent spec for %r — skipping", row["name"])
        return results
