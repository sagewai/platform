# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Guardrail config + audit store backed by SQLAlchemy Core.

Works on both SQLite and PostgreSQL. One implementation replaces the
former asyncpg-only PostgresGuardrailStore. The class name and all
public method signatures are unchanged so callers require no modification.

Tables: ``guardrail_configs``, ``guardrail_events`` (migration 005).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.core.context import get_current_project
from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.engine import create_engine
from sagewai.db.models import Base, GuardrailConfig, GuardrailEventModel

logger = logging.getLogger(__name__)

_cfg_tbl = GuardrailConfig.__table__
_event_tbl = GuardrailEventModel.__table__


def _resolve_project(project_id: str | None = None) -> str:
    """Resolve project_id from explicit param, contextvar, or default."""
    if project_id:
        return project_id
    ctx = get_current_project()
    return ctx.project_id if ctx else "default"


class PostgresGuardrailStore:
    """Guardrail config and audit store using SQLAlchemy Core.

    Tables ``guardrail_configs`` and ``guardrail_events`` must exist
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
        """Delete all config and event records (testing only)."""
        async with self._engine.begin() as conn:
            await conn.execute(sa_delete(_event_tbl))
            await conn.execute(sa_delete(_cfg_tbl))

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
        resolved_project = _resolve_project(project_id)
        stmt = select(_cfg_tbl).where(_cfg_tbl.c.project_id == resolved_project)
        if agent_name is not None:
            stmt = stmt.where(_cfg_tbl.c.agent_name == agent_name)
        stmt = stmt.order_by(_cfg_tbl.c.agent_name, _cfg_tbl.c.guardrail_type)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [self._config_row_to_dict(r) for r in rows]

    async def get_config(
        self,
        agent_name: str,
        guardrail_type: str,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Get a single guardrail config, scoped to project."""
        resolved_project = _resolve_project(project_id)
        stmt = select(_cfg_tbl).where(
            _cfg_tbl.c.agent_name == agent_name,
            _cfg_tbl.c.guardrail_type == guardrail_type,
            _cfg_tbl.c.project_id == resolved_project,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
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
        """Create or update a guardrail config (upsert on agent+type+project).

        Returns the resulting row.  The asyncpg version used RETURNING; we
        reproduce the same result shape by executing a SELECT after the upsert.
        """
        resolved_project = _resolve_project(project_id)
        config_json = json.dumps(config) if config else None
        now = datetime.now(timezone.utc)
        values = {
            "agent_name": agent_name,
            "project_id": resolved_project,
            "guardrail_type": guardrail_type,
            "enabled": enabled,
            "config": config_json,
            "created_at": now,
            "updated_at": now,
        }
        stmt = upsert(
            _cfg_tbl,
            values,
            index_elements=["agent_name", "project_id", "guardrail_type"],
            set_={
                "enabled": enabled,
                "config": config_json,
                "updated_at": now,
            },
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

        # SELECT the final row (portable RETURNING substitute)
        result = await self.get_config(agent_name, guardrail_type, project_id=resolved_project)
        if result is None:
            raise RuntimeError("upsert_config failed to read back the row")
        return result

    async def delete_config(
        self,
        agent_name: str,
        guardrail_type: str,
        *,
        project_id: str | None = None,
    ) -> bool:
        """Delete a guardrail config. Returns True if a row was deleted."""
        resolved_project = _resolve_project(project_id)
        stmt = sa_delete(_cfg_tbl).where(
            _cfg_tbl.c.agent_name == agent_name,
            _cfg_tbl.c.guardrail_type == guardrail_type,
            _cfg_tbl.c.project_id == resolved_project,
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount == 1

    async def delete_all_configs(
        self, agent_name: str, *, project_id: str | None = None
    ) -> int:
        """Delete all guardrail configs for an agent. Returns count deleted."""
        resolved_project = _resolve_project(project_id)
        stmt = sa_delete(_cfg_tbl).where(
            _cfg_tbl.c.agent_name == agent_name,
            _cfg_tbl.c.project_id == resolved_project,
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount

    # ------------------------------------------------------------------
    # Audit Log (guardrail events)
    # ------------------------------------------------------------------

    async def record_guardrail_event(
        self,
        agent_name: str,
        event_type: str,
        detail: str | None = None,
        *,
        project_id: str | None = None,
        entity_type: str | None = None,
        action: str | None = None,
    ) -> None:
        """Record a guardrail event into the audit log.

        This method is added in the SQLAlchemy Core version to allow
        self-contained testing without an external asyncpg connection.
        """
        resolved_project = _resolve_project(project_id)
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_event_tbl).values(
                    agent_name=agent_name,
                    project_id=resolved_project,
                    event_type=event_type,
                    entity_type=entity_type,
                    action=action,
                    detail=detail,
                    created_at=now,
                )
            )

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
        resolved_project = _resolve_project(project_id)
        stmt = select(_event_tbl).where(_event_tbl.c.project_id == resolved_project)
        if agent_name is not None:
            stmt = stmt.where(_event_tbl.c.agent_name == agent_name)
        if event_type is not None:
            stmt = stmt.where(_event_tbl.c.event_type == event_type)
        stmt = stmt.order_by(_event_tbl.c.created_at.desc()).limit(limit).offset(offset)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [self._event_row_to_dict(r) for r in rows]

    async def count_events(
        self,
        agent_name: str | None = None,
        event_type: str | None = None,
        *,
        project_id: str | None = None,
    ) -> int:
        """Count guardrail events matching filters, scoped to project."""
        resolved_project = _resolve_project(project_id)
        stmt = select(func.count().label("cnt")).select_from(_event_tbl).where(
            _event_tbl.c.project_id == resolved_project
        )
        if agent_name is not None:
            stmt = stmt.where(_event_tbl.c.agent_name == agent_name)
        if event_type is not None:
            stmt = stmt.where(_event_tbl.c.event_type == event_type)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return int(row["cnt"])

    async def export_events(
        self,
        agent_name: str | None = None,
        event_type: str | None = None,
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Export all matching events (no pagination, for CSV/JSON export)."""
        resolved_project = _resolve_project(project_id)
        stmt = select(_event_tbl).where(_event_tbl.c.project_id == resolved_project)
        if agent_name is not None:
            stmt = stmt.where(_event_tbl.c.agent_name == agent_name)
        if event_type is not None:
            stmt = stmt.where(_event_tbl.c.event_type == event_type)
        stmt = stmt.order_by(_event_tbl.c.created_at.desc())
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [self._event_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _config_row_to_dict(row: Any) -> dict[str, Any]:
        config_raw = row["config"]
        parsed_config = json.loads(config_raw) if config_raw else None
        created_at = row["created_at"]
        updated_at = row["updated_at"]
        result: dict[str, Any] = {
            "id": row["id"],
            "agent_name": row["agent_name"],
            "guardrail_type": row["guardrail_type"],
            "enabled": row["enabled"],
            "config": parsed_config,
            "created_at": (
                created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
            ) if created_at else None,
            "updated_at": (
                updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at)
            ) if updated_at else None,
        }
        if "project_id" in row.keys():
            result["project_id"] = row["project_id"]
        return result

    @staticmethod
    def _event_row_to_dict(row: Any) -> dict[str, Any]:
        created_at = row["created_at"]
        return {
            "id": row["id"],
            "agent_name": row["agent_name"],
            "project_id": row["project_id"],
            "event_type": row["event_type"],
            "entity_type": row["entity_type"],
            "action": row["action"],
            "detail": row["detail"],
            "created_at": (
                created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
            ) if created_at else None,
        }
