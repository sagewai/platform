# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""PostgreSQL-backed analytics store for cost and guardrail tracking.

Drop-in replacement for the in-memory AnalyticsStore. Uses asyncpg
connection pool for safe concurrent access.

Usage::

    from sagewai.admin.postgres_analytics import PostgresAnalyticsStore

    store = PostgresAnalyticsStore("postgresql://user:pass@host/db")
    await store.init()
    await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
    costs = await store.get_costs()
"""

from __future__ import annotations

import logging
from typing import Any

from sagewai.observability.costs import is_local_model

logger = logging.getLogger(__name__)


class PostgresAnalyticsStore:
    """PostgreSQL-backed analytics store.

    Tables ``cost_records`` and ``guardrail_events`` must exist
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
            await conn.execute("DELETE FROM cost_records")
            await conn.execute("DELETE FROM guardrail_events")

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    async def record_cost(
        self,
        agent_name: str,
        model: str,
        cost_usd: float,
        tokens: int,
    ) -> None:
        """Record a cost event."""
        self._check()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO cost_records (agent_name, model, cost_usd, tokens)"
                " VALUES ($1, $2, $3, $4)",
                agent_name,
                model,
                cost_usd,
                tokens,
            )

    async def record_guardrail_event(
        self,
        agent_name: str,
        event_type: str,
        detail: str | None = None,
    ) -> None:
        """Record a guardrail event."""
        self._check()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO guardrail_events (agent_name, event_type, detail)"
                " VALUES ($1, $2, $3)",
                agent_name,
                event_type,
                detail,
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_costs(
        self, agent_name: str | None = None
    ) -> dict[str, Any]:
        """Get cost analytics, optionally filtered by agent."""
        self._check()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total,"
                " COUNT(*) AS cnt"
                " FROM cost_records"
                " WHERE ($1::text IS NULL OR agent_name = $1)",
                agent_name,
            )
        return {
            "total_cost_usd": float(row["total"]),
            "record_count": row["cnt"],
        }

    async def get_usage(
        self, agent_name: str | None = None
    ) -> dict[str, Any]:
        """Get token usage analytics."""
        self._check()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COALESCE(SUM(tokens), 0) AS total_tokens,"
                " COUNT(*) AS cnt"
                " FROM cost_records"
                " WHERE ($1::text IS NULL OR agent_name = $1)",
                agent_name,
            )
        return {
            "total_tokens": int(row["total_tokens"]),
            "record_count": row["cnt"],
        }

    async def get_risks(
        self, agent_name: str | None = None
    ) -> dict[str, Any]:
        """Get risk analytics — PII events, hallucination flags, etc."""
        self._check()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT event_type, COUNT(*) AS cnt"
                " FROM guardrail_events"
                " WHERE ($1::text IS NULL OR agent_name = $1)"
                " GROUP BY event_type",
                agent_name,
            )
        counts: dict[str, int] = {r["event_type"]: r["cnt"] for r in rows}
        total = sum(counts.values())
        # Accept both "pii" and "pii_detected" for backwards compat
        pii = counts.get("pii", 0) + counts.get("pii_detected", 0) + counts.get("guardrail_violation", 0)
        return {
            "pii_events": pii,
            "hallucination_flags": counts.get("hallucination", 0),
            "content_filter_events": counts.get("content_filter", 0),
            "total_events": total,
        }

    async def get_model_analytics(self) -> list[dict[str, Any]]:
        """Get per-model analytics."""
        self._check()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT model,"
                " SUM(cost_usd) AS total_cost,"
                " SUM(tokens) AS total_tokens,"
                " COUNT(*) AS requests"
                " FROM cost_records"
                " GROUP BY model"
                " ORDER BY total_cost DESC"
            )
        result = []
        for r in rows:
            total_tokens = int(r["total_tokens"])
            total_cost = float(r["total_cost"])
            cost_per_1k = (total_cost / total_tokens * 1000) if total_tokens > 0 else 0.0
            result.append({
                "model": r["model"],
                "total_cost_usd": total_cost,
                "total_tokens": total_tokens,
                "request_count": r["requests"],
                "cost_per_1k_tokens": cost_per_1k,
                "is_local": is_local_model(r["model"]),
            })
        return result

    async def get_agent_analytics(self) -> list[dict[str, Any]]:
        """Get per-agent analytics."""
        self._check()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT agent_name,"
                " SUM(cost_usd) AS total_cost,"
                " SUM(tokens) AS total_tokens,"
                " COUNT(*) AS requests,"
                " ARRAY_AGG(DISTINCT model) AS models_used"
                " FROM cost_records"
                " GROUP BY agent_name"
                " ORDER BY total_cost DESC"
            )
        return [
            {
                "agent_name": r["agent_name"],
                "total_cost_usd": float(r["total_cost"]),
                "total_tokens": int(r["total_tokens"]),
                "request_count": r["requests"],
                "models_used": list(r["models_used"]),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check(self) -> None:
        if self._pool is None:
            raise RuntimeError(
                "PostgresAnalyticsStore not initialized. Call init() first."
            )
