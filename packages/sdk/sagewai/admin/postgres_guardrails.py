# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""PostgreSQL-backed guardrail config + audit store.

Provides CRUD for per-agent guardrail configurations and querying
of guardrail events (audit log). Uses asyncpg connection pool for
safe concurrent access.

Tables: ``guardrail_configs``, ``guardrail_events`` (migration 005).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sagewai.core.context import get_current_project

logger = logging.getLogger(__name__)


def _resolve_project(project_id: str | None = None) -> str:
    """Resolve project_id from explicit param, contextvar, or default."""
    if project_id:
        return project_id
    ctx = get_current_project()
    return ctx.project_id if ctx else "default"


class PostgresGuardrailStore:
    """Manage per-agent guardrail configurations and audit events."""

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._pool: Any = None

    async def init(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=5)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def clear(self) -> None:
        """Delete all records (testing only)."""
        self._check()
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM guardrail_configs")

    # ------------------------------------------------------------------
    # Guardrail Config CRUD
    # ------------------------------------------------------------------

    async def list_configs(
        self,
        agent_name: str | None = None,
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List guardrail configs, optionally filtered by agent, scoped to project."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, agent_name, project_id, guardrail_type, enabled, config,"
                " created_at, updated_at"
                " FROM guardrail_configs"
                " WHERE project_id = $1"
                "   AND ($2::text IS NULL OR agent_name = $2)"
                " ORDER BY agent_name, guardrail_type",
                resolved_project,
                agent_name,
            )
        return [self._config_row_to_dict(r) for r in rows]

    async def get_config(
        self,
        agent_name: str,
        guardrail_type: str,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Get a single guardrail config, scoped to project."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, agent_name, project_id, guardrail_type, enabled, config,"
                " created_at, updated_at"
                " FROM guardrail_configs"
                " WHERE agent_name = $1 AND guardrail_type = $2 AND project_id = $3",
                agent_name,
                guardrail_type,
                resolved_project,
            )
        return self._config_row_to_dict(row) if row else None

    async def upsert_config(
        self,
        agent_name: str,
        guardrail_type: str,
        enabled: bool = True,
        config: dict[str, Any] | None = None,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a guardrail config (upsert on agent+type+project)."""
        self._check()
        resolved_project = _resolve_project(project_id)
        config_json = json.dumps(config) if config else None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO guardrail_configs"
                " (agent_name, project_id, guardrail_type, enabled, config)"
                " VALUES ($1, $2, $3, $4, $5)"
                " ON CONFLICT (agent_name, project_id, guardrail_type)"
                " DO UPDATE SET enabled = $4, config = $5, updated_at = NOW()"
                " RETURNING id, agent_name, project_id, guardrail_type, enabled, config,"
                " created_at, updated_at",
                agent_name,
                resolved_project,
                guardrail_type,
                enabled,
                config_json,
            )
        return self._config_row_to_dict(row)

    async def delete_config(
        self,
        agent_name: str,
        guardrail_type: str,
        *,
        project_id: str | None = None,
    ) -> bool:
        """Delete a guardrail config. Returns True if deleted."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM guardrail_configs"
                " WHERE agent_name = $1 AND guardrail_type = $2 AND project_id = $3",
                agent_name,
                guardrail_type,
                resolved_project,
            )
        return result == "DELETE 1"

    async def delete_all_configs(
        self, agent_name: str, *, project_id: str | None = None
    ) -> int:
        """Delete all guardrail configs for an agent."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM guardrail_configs WHERE agent_name = $1 AND project_id = $2",
                agent_name,
                resolved_project,
            )
        # result is e.g. "DELETE 3"
        return int(result.split()[-1])

    # ------------------------------------------------------------------
    # Audit Log (guardrail events)
    # ------------------------------------------------------------------

    async def list_events(
        self,
        agent_name: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query guardrail events with optional filters, scoped to project."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, agent_name, project_id, event_type, entity_type, action,"
                " detail, created_at"
                " FROM guardrail_events"
                " WHERE project_id = $1"
                "   AND ($2::text IS NULL OR agent_name = $2)"
                "   AND ($3::text IS NULL OR event_type = $3)"
                " ORDER BY created_at DESC"
                " LIMIT $4 OFFSET $5",
                resolved_project,
                agent_name,
                event_type,
                limit,
                offset,
            )
        return [
            {
                "id": r["id"],
                "agent_name": r["agent_name"],
                "project_id": r["project_id"],
                "event_type": r["event_type"],
                "entity_type": r["entity_type"],
                "action": r["action"],
                "detail": r["detail"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]

    async def count_events(
        self,
        agent_name: str | None = None,
        event_type: str | None = None,
        *,
        project_id: str | None = None,
    ) -> int:
        """Count guardrail events matching filters, scoped to project."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM guardrail_events"
                " WHERE project_id = $1"
                "   AND ($2::text IS NULL OR agent_name = $2)"
                "   AND ($3::text IS NULL OR event_type = $3)",
                resolved_project,
                agent_name,
                event_type,
            )
        return row["cnt"]

    async def export_events(
        self,
        agent_name: str | None = None,
        event_type: str | None = None,
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Export all matching events (no pagination, for CSV/JSON export)."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, agent_name, project_id, event_type, entity_type, action,"
                " detail, created_at"
                " FROM guardrail_events"
                " WHERE project_id = $1"
                "   AND ($2::text IS NULL OR agent_name = $2)"
                "   AND ($3::text IS NULL OR event_type = $3)"
                " ORDER BY created_at DESC",
                resolved_project,
                agent_name,
                event_type,
            )
        return [
            {
                "id": r["id"],
                "agent_name": r["agent_name"],
                "project_id": r["project_id"],
                "event_type": r["event_type"],
                "entity_type": r["entity_type"],
                "action": r["action"],
                "detail": r["detail"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _config_row_to_dict(row: Any) -> dict[str, Any]:
        config_raw = row["config"]
        parsed_config = json.loads(config_raw) if config_raw else None
        result: dict[str, Any] = {
            "id": row["id"],
            "agent_name": row["agent_name"],
            "guardrail_type": row["guardrail_type"],
            "enabled": row["enabled"],
            "config": parsed_config,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        if "project_id" in row.keys():
            result["project_id"] = row["project_id"]
        return result

    def _check(self) -> None:
        if self._pool is None:
            raise RuntimeError(
                "PostgresGuardrailStore not initialized. Call init() first."
            )
