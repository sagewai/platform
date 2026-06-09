# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Analytics store backed by SQLAlchemy Core — works on both SQLite and PostgreSQL.

One implementation replaces the former asyncpg-only PostgresAnalyticsStore.
The class name and all public method signatures are unchanged so callers
require no modification.

Usage::

    from sagewai.admin.postgres_analytics import PostgresAnalyticsStore

    store = PostgresAnalyticsStore("postgresql://user:pass@host/db")
    await store.init()
    await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
    costs = await store.get_costs()
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.core.context import get_current_project
from sagewai.db import factory
from sagewai.db.engine import create_engine
from sagewai.db.models import Base, CostRecord, GuardrailEventModel
from sagewai.observability.costs import is_local_model


def _resolve_project(project_id: str | None = None) -> str:
    """Resolve project_id from explicit param, contextvar, or default."""
    if project_id:
        return project_id
    ctx = get_current_project()
    return ctx.project_id if ctx else "default"

logger = logging.getLogger(__name__)

_cost_tbl = CostRecord.__table__
_event_tbl = GuardrailEventModel.__table__


def _apply_project_filter(stmt, table, project_id: str | None):
    if project_id is None:
        return stmt
    return stmt.where(table.c.project_id == project_id)


class PostgresAnalyticsStore:
    """Analytics store using SQLAlchemy Core — SQLite (default) or PostgreSQL.

    Tables ``cost_records`` and ``guardrail_events`` must exist
    (created by Alembic migration 005, or via ``Base.metadata.create_all``
    on SQLite).

    Parameters
    ----------
    database_url:
        Connection string passed to :func:`sagewai.db.engine.create_engine`.
        Ignored when *engine* is supplied.  Accepted for backward-compat
        with callers that previously passed the URL as the only argument.
    engine:
        Pre-built :class:`AsyncEngine`.  When supplied, *database_url* is
        ignored and the asyncpg *pool* is unused.
    """

    def __init__(
        self,
        database_url: str | None = None,
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        if engine is not None:
            self._engine: AsyncEngine = engine
        elif database_url is not None:
            self._engine = create_engine(database_url)
        else:
            self._engine = factory.get_engine()

    async def init(self) -> None:
        """Bootstrap schema on SQLite; no-op on PostgreSQL (Alembic owns it).

        Kept for API back-compat with the asyncpg version.
        """
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Dispose the engine (compat with asyncpg version that closed the pool)."""
        await self._engine.dispose()

    async def clear(self) -> None:
        """Delete all records (for testing)."""
        from sqlalchemy import delete as sa_delete

        async with self._engine.begin() as conn:
            await conn.execute(sa_delete(_cost_tbl))
            await conn.execute(sa_delete(_event_tbl))

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    async def record_cost(
        self,
        agent_name: str,
        model: str,
        cost_usd: float,
        tokens: int,
        project_id: str | None = None,
    ) -> None:
        """Record a cost event."""
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_cost_tbl).values(
                    agent_name=agent_name,
                    model=model,
                    cost_usd=cost_usd,
                    tokens=tokens,
                    created_at=now,
                    project_id=_resolve_project(project_id),
                )
            )

    async def record_guardrail_event(
        self,
        agent_name: str,
        event_type: str,
        detail: str | None = None,
        project_id: str | None = None,
    ) -> None:
        """Record a guardrail event."""
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_event_tbl).values(
                    agent_name=agent_name,
                    event_type=event_type,
                    detail=detail,
                    created_at=now,
                    project_id=_resolve_project(project_id),
                )
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_costs(
        self, agent_name: str | None = None, *, project_id: str | None = None
    ) -> dict[str, Any]:
        """Get cost analytics, optionally filtered by project and agent."""
        stmt = select(
            func.coalesce(func.sum(_cost_tbl.c.cost_usd), 0).label("total"),
            func.count().label("cnt"),
        )
        stmt = _apply_project_filter(stmt, _cost_tbl, project_id)
        if agent_name is not None:
            stmt = stmt.where(_cost_tbl.c.agent_name == agent_name)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return {
            "total_cost_usd": float(row["total"]),
            "record_count": int(row["cnt"]),
        }

    async def get_usage(
        self, agent_name: str | None = None, *, project_id: str | None = None
    ) -> dict[str, Any]:
        """Get token usage analytics, optionally filtered by project and agent."""
        stmt = select(
            func.coalesce(func.sum(_cost_tbl.c.tokens), 0).label("total_tokens"),
            func.count().label("cnt"),
        )
        stmt = _apply_project_filter(stmt, _cost_tbl, project_id)
        if agent_name is not None:
            stmt = stmt.where(_cost_tbl.c.agent_name == agent_name)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return {
            "total_tokens": int(row["total_tokens"]),
            "record_count": int(row["cnt"]),
        }

    async def get_risks(
        self, agent_name: str | None = None, *, project_id: str | None = None
    ) -> dict[str, Any]:
        """Get risk analytics — PII events, hallucination flags, etc."""
        stmt = select(
            _event_tbl.c.event_type,
            func.count().label("cnt"),
        ).group_by(_event_tbl.c.event_type)
        stmt = _apply_project_filter(stmt, _event_tbl, project_id)
        if agent_name is not None:
            stmt = stmt.where(_event_tbl.c.agent_name == agent_name)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        counts: dict[str, int] = {r["event_type"]: int(r["cnt"]) for r in rows}
        total = sum(counts.values())
        # Accept both "pii" and "pii_detected" for backwards compat
        pii = (
            counts.get("pii", 0)
            + counts.get("pii_detected", 0)
            + counts.get("guardrail_violation", 0)
        )
        return {
            "pii_events": pii,
            "hallucination_flags": counts.get("hallucination", 0),
            "content_filter_events": counts.get("content_filter", 0),
            "total_events": total,
        }

    async def get_model_analytics(self, *, project_id: str | None = None) -> list[dict[str, Any]]:
        """Get per-model analytics, optionally filtered by project."""
        stmt = (
            select(
                _cost_tbl.c.model,
                func.sum(_cost_tbl.c.cost_usd).label("total_cost"),
                func.sum(_cost_tbl.c.tokens).label("total_tokens"),
                func.count().label("requests"),
            )
            .group_by(_cost_tbl.c.model)
            .order_by(func.sum(_cost_tbl.c.cost_usd).desc())
        )
        stmt = _apply_project_filter(stmt, _cost_tbl, project_id)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        result = []
        for r in rows:
            total_tokens = int(r["total_tokens"])
            total_cost = float(r["total_cost"])
            cost_per_1k = (total_cost / total_tokens * 1000) if total_tokens > 0 else 0.0
            result.append(
                {
                    "model": r["model"],
                    "total_cost_usd": total_cost,
                    "total_tokens": total_tokens,
                    "request_count": int(r["requests"]),
                    "cost_per_1k_tokens": cost_per_1k,
                    "is_local": is_local_model(r["model"]),
                }
            )
        return result

    async def get_agent_analytics(self, *, project_id: str | None = None) -> list[dict[str, Any]]:
        """Get per-agent analytics, optionally filtered by project.

        ARRAY_AGG(DISTINCT model) is not portable; we instead collect the
        distinct models in Python by running a secondary
        SELECT DISTINCT (agent_name, model) query — one round-trip, but
        fully portable and produces the same result shape.
        """
        # Aggregate: totals per agent
        agg_stmt = (
            select(
                _cost_tbl.c.agent_name,
                func.sum(_cost_tbl.c.cost_usd).label("total_cost"),
                func.sum(_cost_tbl.c.tokens).label("total_tokens"),
                func.count().label("requests"),
            )
            .group_by(_cost_tbl.c.agent_name)
            .order_by(func.sum(_cost_tbl.c.cost_usd).desc())
        )
        agg_stmt = _apply_project_filter(agg_stmt, _cost_tbl, project_id)
        # Distinct models per agent
        models_stmt = select(
            _cost_tbl.c.agent_name,
            _cost_tbl.c.model,
        ).distinct()
        models_stmt = _apply_project_filter(models_stmt, _cost_tbl, project_id)

        async with self._engine.connect() as conn:
            agg_rows = (await conn.execute(agg_stmt)).mappings().all()
            model_rows = (await conn.execute(models_stmt)).mappings().all()

        # Build agent -> set of models mapping
        agent_models: dict[str, set[str]] = {}
        for r in model_rows:
            agent_models.setdefault(r["agent_name"], set()).add(r["model"])

        return [
            {
                "agent_name": r["agent_name"],
                "total_cost_usd": float(r["total_cost"]),
                "total_tokens": int(r["total_tokens"]),
                "request_count": int(r["requests"]),
                "models_used": sorted(agent_models.get(r["agent_name"], set())),
            }
            for r in agg_rows
        ]
