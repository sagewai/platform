# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""PostgreSQL-backed budget manager for per-agent spend tracking.

Drop-in replacement for the in-memory BudgetManager. Uses asyncpg
connection pool for safe concurrent access.

Usage::

    from sagewai.admin.postgres_budget import PostgresBudgetManager

    mgr = PostgresBudgetManager("postgresql://user:pass@host/db")
    await mgr.init()
    await mgr.add_limit(agent_name="a", max_daily_usd=5.0, max_monthly_usd=100.0)
    await mgr.record_spend(agent_name="a", cost_usd=0.50)
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


class PostgresBudgetManager:
    """PostgreSQL-backed budget manager.

    Tables ``budget_limits`` and ``budget_spend`` must exist
    (created by Alembic migration 005).
    """

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._pool: Any = None

    async def init(self) -> None:
        """Open a connection pool."""
        import asyncpg

        self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=5)

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def clear(self) -> None:
        """Delete all records (for testing)."""
        self._check()
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM budget_spend")
            await conn.execute("DELETE FROM budget_limits")

    # ------------------------------------------------------------------
    # Limits CRUD
    # ------------------------------------------------------------------

    async def add_limit(
        self,
        *,
        agent_name: str,
        max_daily_usd: float,
        max_monthly_usd: float,
        action: str = "warn",
        fallback_chain: list[str] | None = None,
        project_id: str | None = None,
    ) -> None:
        """Add or update a budget limit for an agent (upsert)."""
        self._check()
        resolved_project = _resolve_project(project_id)
        chain_json = json.dumps(fallback_chain) if fallback_chain else None
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO budget_limits"
                " (agent_name, project_id, max_daily_usd, max_monthly_usd, action, fallback_chain)"
                " VALUES ($1, $2, $3, $4, $5, $6)"
                " ON CONFLICT (agent_name, project_id) DO UPDATE SET"
                " max_daily_usd = EXCLUDED.max_daily_usd,"
                " max_monthly_usd = EXCLUDED.max_monthly_usd,"
                " action = EXCLUDED.action,"
                " fallback_chain = EXCLUDED.fallback_chain,"
                " updated_at = NOW()",
                agent_name,
                resolved_project,
                max_daily_usd,
                max_monthly_usd,
                action,
                chain_json,
            )

    async def remove_limit(
        self, agent_name: str, *, project_id: str | None = None
    ) -> None:
        """Remove a budget limit for an agent."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM budget_limits WHERE agent_name = $1 AND project_id = $2",
                agent_name,
                resolved_project,
            )

    async def list_limits(
        self, *, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List all configured budget limits for a project."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT agent_name, project_id, max_daily_usd, max_monthly_usd,"
                " action, fallback_chain"
                " FROM budget_limits WHERE project_id = $1 ORDER BY agent_name",
                resolved_project,
            )
        return [
            {
                "agent_name": r["agent_name"],
                "project_id": r["project_id"],
                "max_daily_usd": float(r["max_daily_usd"]),
                "max_monthly_usd": float(r["max_monthly_usd"]),
                "action": r["action"],
                "fallback_chain": (
                    json.loads(r["fallback_chain"])
                    if r["fallback_chain"]
                    else []
                ),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Spend tracking
    # ------------------------------------------------------------------

    async def record_spend(
        self, *, agent_name: str, cost_usd: float, project_id: str | None = None
    ) -> None:
        """Record a cost event for an agent."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO budget_spend (agent_name, project_id, cost_usd)"
                " VALUES ($1, $2, $3)",
                agent_name,
                resolved_project,
                cost_usd,
            )

    async def get_budget_status(
        self, agent_name: str, *, project_id: str | None = None
    ) -> dict[str, Any]:
        """Get current budget status for an agent."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            limit = await conn.fetchrow(
                "SELECT max_daily_usd, max_monthly_usd"
                " FROM budget_limits WHERE agent_name = $1 AND project_id = $2",
                agent_name,
                resolved_project,
            )
            daily = await self._daily_spend(conn, agent_name, resolved_project)
            monthly = await self._monthly_spend(conn, agent_name, resolved_project)

        return {
            "agent_name": agent_name,
            "project_id": resolved_project,
            "daily_spend_usd": daily,
            "monthly_spend_usd": monthly,
            "max_daily_usd": float(limit["max_daily_usd"]) if limit else None,
            "max_monthly_usd": (
                float(limit["max_monthly_usd"]) if limit else None
            ),
        }

    # ------------------------------------------------------------------
    # Budget checks
    # ------------------------------------------------------------------

    async def check_budget(
        self, agent_name: str, *, project_id: str | None = None
    ) -> dict[str, Any]:
        """Check if an agent is within budget."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            limit = await conn.fetchrow(
                "SELECT max_daily_usd, max_monthly_usd, action"
                " FROM budget_limits WHERE agent_name = $1 AND project_id = $2",
                agent_name,
                resolved_project,
            )
            if limit is None:
                return {"allowed": True, "action": "allow"}

            daily = await self._daily_spend(conn, agent_name, resolved_project)
            monthly = await self._monthly_spend(conn, agent_name, resolved_project)

        if daily > float(limit["max_daily_usd"]):
            return {
                "allowed": False,
                "action": limit["action"],
                "reason": (
                    f"Daily budget exceeded: ${daily:.4f}"
                    f" > ${float(limit['max_daily_usd']):.4f}"
                ),
                "daily_spend": daily,
                "monthly_spend": monthly,
            }

        if monthly > float(limit["max_monthly_usd"]):
            return {
                "allowed": False,
                "action": limit["action"],
                "reason": (
                    f"Monthly budget exceeded: ${monthly:.4f}"
                    f" > ${float(limit['max_monthly_usd']):.4f}"
                ),
                "daily_spend": daily,
                "monthly_spend": monthly,
            }

        return {
            "allowed": True,
            "action": "allow",
            "daily_spend": daily,
            "monthly_spend": monthly,
        }

    async def get_fallback_model(
        self, agent_name: str, current_model: str, *, project_id: str | None = None
    ) -> str | None:
        """Get the fallback model for an over-budget agent."""
        self._check()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            limit = await conn.fetchrow(
                "SELECT fallback_chain FROM budget_limits"
                " WHERE agent_name = $1 AND project_id = $2",
                agent_name,
                resolved_project,
            )
        if limit is None or not limit["fallback_chain"]:
            return None

        result = await self.check_budget(agent_name, project_id=resolved_project)
        if result["allowed"]:
            return None

        chain = json.loads(limit["fallback_chain"])
        for model in chain:
            if model != current_model:
                return model
        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    async def _daily_spend(conn: Any, agent_name: str, project_id: str = "default") -> float:
        """Calculate today's spend via SQL."""
        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total"
            " FROM budget_spend"
            " WHERE agent_name = $1 AND project_id = $2"
            " AND created_at >= DATE_TRUNC('day', NOW())",
            agent_name,
            project_id,
        )
        return float(row["total"])

    @staticmethod
    async def _monthly_spend(conn: Any, agent_name: str, project_id: str = "default") -> float:
        """Calculate last-30-days spend via SQL."""
        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total"
            " FROM budget_spend"
            " WHERE agent_name = $1 AND project_id = $2"
            " AND created_at >= NOW() - INTERVAL '30 days'",
            agent_name,
            project_id,
        )
        return float(row["total"])

    def _check(self) -> None:
        if self._pool is None:
            raise RuntimeError(
                "PostgresBudgetManager not initialized. Call init() first."
            )
