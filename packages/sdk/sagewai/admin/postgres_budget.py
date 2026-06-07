# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Budget manager backed by SQLAlchemy Core — works on both SQLite and PostgreSQL.

One implementation replaces the former asyncpg-only PostgresBudgetManager.
The class name and all public method signatures are unchanged so callers
require no modification.

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
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.core.context import get_current_project
from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.engine import create_engine
from sagewai.db.models import Base, BudgetLimitModel, BudgetSpend

logger = logging.getLogger(__name__)

_limit_tbl = BudgetLimitModel.__table__
_spend_tbl = BudgetSpend.__table__


def _resolve_project(project_id: str | None = None) -> str:
    """Resolve project_id from explicit param, contextvar, or default."""
    if project_id:
        return project_id
    ctx = get_current_project()
    return ctx.project_id if ctx else "default"


class PostgresBudgetManager:
    """Budget manager using SQLAlchemy Core — SQLite (default) or PostgreSQL.

    Tables ``budget_limits`` and ``budget_spend`` must exist
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
        ignored.
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
        async with self._engine.begin() as conn:
            await conn.execute(sa_delete(_spend_tbl))
            await conn.execute(sa_delete(_limit_tbl))

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
        resolved_project = _resolve_project(project_id)
        chain_json = json.dumps(fallback_chain) if fallback_chain else None
        now = datetime.now(timezone.utc)
        values = {
            "agent_name": agent_name,
            "project_id": resolved_project,
            "max_daily_usd": max_daily_usd,
            "max_monthly_usd": max_monthly_usd,
            "action": action,
            "fallback_chain": chain_json,
            "created_at": now,
            "updated_at": now,
        }
        stmt = upsert(
            _limit_tbl,
            values,
            index_elements=["agent_name", "project_id"],
            set_={
                "max_daily_usd": max_daily_usd,
                "max_monthly_usd": max_monthly_usd,
                "action": action,
                "fallback_chain": chain_json,
                "updated_at": now,
            },
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def remove_limit(
        self, agent_name: str, *, project_id: str | None = None
    ) -> None:
        """Remove a budget limit for an agent."""
        resolved_project = _resolve_project(project_id)
        stmt = sa_delete(_limit_tbl).where(
            _limit_tbl.c.agent_name == agent_name,
            _limit_tbl.c.project_id == resolved_project,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def list_limits(
        self, *, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List all configured budget limits for a project."""
        resolved_project = _resolve_project(project_id)
        stmt = (
            select(
                _limit_tbl.c.agent_name,
                _limit_tbl.c.project_id,
                _limit_tbl.c.max_daily_usd,
                _limit_tbl.c.max_monthly_usd,
                _limit_tbl.c.action,
                _limit_tbl.c.fallback_chain,
            )
            .where(_limit_tbl.c.project_id == resolved_project)
            .order_by(_limit_tbl.c.agent_name)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [
            {
                "agent_name": r["agent_name"],
                "project_id": r["project_id"],
                "max_daily_usd": float(r["max_daily_usd"]),
                "max_monthly_usd": float(r["max_monthly_usd"]),
                "action": r["action"],
                "fallback_chain": (
                    json.loads(r["fallback_chain"]) if r["fallback_chain"] else []
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
        resolved_project = _resolve_project(project_id)
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_spend_tbl).values(
                    agent_name=agent_name,
                    project_id=resolved_project,
                    cost_usd=cost_usd,
                    created_at=now,
                )
            )

    async def get_budget_status(
        self, agent_name: str, *, project_id: str | None = None
    ) -> dict[str, Any]:
        """Get current budget status for an agent."""
        resolved_project = _resolve_project(project_id)
        limit_stmt = select(
            _limit_tbl.c.max_daily_usd,
            _limit_tbl.c.max_monthly_usd,
        ).where(
            _limit_tbl.c.agent_name == agent_name,
            _limit_tbl.c.project_id == resolved_project,
        )
        async with self._engine.connect() as conn:
            limit_row = (await conn.execute(limit_stmt)).mappings().first()
            daily = await self._daily_spend(conn, agent_name, resolved_project)
            monthly = await self._monthly_spend(conn, agent_name, resolved_project)

        max_daily = float(limit_row["max_daily_usd"]) if limit_row else None
        max_monthly = float(limit_row["max_monthly_usd"]) if limit_row else None
        return {
            "agent_name": agent_name,
            "project_id": resolved_project,
            "daily_spend_usd": daily,
            "monthly_spend_usd": monthly,
            "max_daily_usd": max_daily,
            "max_monthly_usd": max_monthly,
            "daily_remaining_usd": (max_daily - daily) if max_daily is not None else None,
            "monthly_remaining_usd": (max_monthly - monthly) if max_monthly is not None else None,
        }

    # ------------------------------------------------------------------
    # Budget checks
    # ------------------------------------------------------------------

    async def check_budget(
        self, agent_name: str, *, project_id: str | None = None
    ) -> dict[str, Any]:
        """Check if an agent is within budget."""
        resolved_project = _resolve_project(project_id)
        limit_stmt = select(
            _limit_tbl.c.max_daily_usd,
            _limit_tbl.c.max_monthly_usd,
            _limit_tbl.c.action,
        ).where(
            _limit_tbl.c.agent_name == agent_name,
            _limit_tbl.c.project_id == resolved_project,
        )
        async with self._engine.connect() as conn:
            limit_row = (await conn.execute(limit_stmt)).mappings().first()
            if limit_row is None:
                return {"allowed": True, "action": "allow"}
            daily = await self._daily_spend(conn, agent_name, resolved_project)
            monthly = await self._monthly_spend(conn, agent_name, resolved_project)

        max_daily = float(limit_row["max_daily_usd"])
        max_monthly = float(limit_row["max_monthly_usd"])
        action = limit_row["action"]

        if daily > max_daily:
            return {
                "allowed": False,
                "action": action,
                "reason": (
                    f"Daily budget exceeded: ${daily:.4f} > ${max_daily:.4f}"
                ),
                "daily_spend": daily,
                "monthly_spend": monthly,
            }

        if monthly > max_monthly:
            return {
                "allowed": False,
                "action": action,
                "reason": (
                    f"Monthly budget exceeded: ${monthly:.4f} > ${max_monthly:.4f}"
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
        resolved_project = _resolve_project(project_id)
        limit_stmt = select(_limit_tbl.c.fallback_chain).where(
            _limit_tbl.c.agent_name == agent_name,
            _limit_tbl.c.project_id == resolved_project,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(limit_stmt)).mappings().first()
        if row is None or not row["fallback_chain"]:
            return None

        result = await self.check_budget(agent_name, project_id=resolved_project)
        if result["allowed"]:
            return None

        chain = json.loads(row["fallback_chain"])
        for model in chain:
            if model != current_model:
                return model
        return None

    # ------------------------------------------------------------------
    # Internal helpers — portable date-range spend queries
    # ------------------------------------------------------------------

    @staticmethod
    async def _daily_spend(conn: Any, agent_name: str, project_id: str) -> float:
        """Calculate today's spend (since midnight UTC) via SQLAlchemy Core."""
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = select(
            func.coalesce(func.sum(_spend_tbl.c.cost_usd), 0).label("total")
        ).where(
            _spend_tbl.c.agent_name == agent_name,
            _spend_tbl.c.project_id == project_id,
            _spend_tbl.c.created_at >= day_start,
        )
        row = (await conn.execute(stmt)).mappings().first()
        return float(row["total"])

    @staticmethod
    async def _monthly_spend(conn: Any, agent_name: str, project_id: str) -> float:
        """Calculate last-30-days spend via SQLAlchemy Core."""
        now = datetime.now(timezone.utc)
        month_start = now - timedelta(days=30)
        stmt = select(
            func.coalesce(func.sum(_spend_tbl.c.cost_usd), 0).label("total")
        ).where(
            _spend_tbl.c.agent_name == agent_name,
            _spend_tbl.c.project_id == project_id,
            _spend_tbl.c.created_at >= month_start,
        )
        row = (await conn.execute(stmt)).mappings().first()
        return float(row["total"])
