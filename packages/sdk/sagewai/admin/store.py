# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Agent run store — persistent storage for run records.

Backed by SQLAlchemy Core, compatible with both SQLite (default) and
PostgreSQL. The class name and all public method signatures are
unchanged so callers require no modification.

Usage::

    from sagewai.admin.store import RunStore

    # Default engine (SQLite or $SAGEWAI_DATABASE_URL):
    store = RunStore()
    await store.init()

    # Explicit URL (old positional form — still supported):
    store = RunStore("postgresql://user:pass@host/db")
    await store.init()

    # Injected engine (test / DI form):
    store = RunStore(engine=my_async_engine)

    run_id = await store.save_run(
        agent_name="scout",
        input_text="Find info about AI",
        output_text="Here is what I found...",
        total_tokens=450,
    )

    runs = await store.list_runs(agent_name="scout", limit=10)
    run = await store.get_run(run_id)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.admin import scoping
from sagewai.core.context import get_current_project
from sagewai.db import factory
from sagewai.db.engine import create_engine
from sagewai.db.models import AgentRun, Base

logger = logging.getLogger(__name__)

_tbl = AgentRun.__table__


def _resolve_project(project_id: str | None = None) -> str:
    """Resolve project_id from explicit param, contextvar, or default."""
    if project_id:
        return project_id
    ctx = get_current_project()
    return ctx.project_id if ctx else "default"


@dataclass
class RunRecord:
    """A stored agent run record."""

    run_id: str
    agent_name: str
    project_id: str = "default"
    status: str = "completed"
    input_text: str = ""
    output_text: str = ""
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    duration_ms: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    completed_at: float = 0.0
    error: str | None = None
    checkpoint_run_id: str | None = None
    run_type: str = "standalone"
    parent_workflow_run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "run_id": self.run_id,
            "agent_name": self.agent_name,
            "project_id": self.project_id,
            "status": self.status,
            "input_text": self.input_text,
            "output_text": self.output_text,
            "total_tokens": self.total_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "model": self.model,
            "duration_ms": self.duration_ms,
            "tool_calls": self.tool_calls,
            "metadata": self.metadata,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "run_type": self.run_type,
            "parent_workflow_run_id": self.parent_workflow_run_id,
        }


class RunStore:
    """SQLAlchemy Core agent run store — SQLite (default) or PostgreSQL.

    Constructor forms (all equivalent from caller perspective):

    * ``RunStore()``
        Uses the process-wide engine from :func:`sagewai.db.factory.get_engine`.
    * ``RunStore("postgresql://user:pass@host/db")``
        Positional URL string — back-compat with old callers.
    * ``RunStore(engine=my_engine)``
        Injected engine; used by tests and DI containers.
    * ``RunStore(database_url="...")``
        Keyword URL — also supported.

    On SQLite, :meth:`init` creates the schema via ``create_all``.
    On PostgreSQL, :meth:`init` is a no-op (Alembic owns the schema).
    """

    def __init__(
        self,
        engine_or_url: AsyncEngine | str | None = None,
        *,
        database_url: str | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        # Resolve which engine to use, in priority order:
        #   1. `engine=` keyword argument
        #   2. positional AsyncEngine
        #   3. positional str URL
        #   4. `database_url=` keyword URL
        #   5. factory default
        if engine is not None:
            self._engine: AsyncEngine = engine
        elif isinstance(engine_or_url, AsyncEngine):
            self._engine = engine_or_url
        elif isinstance(engine_or_url, str):
            self._engine = create_engine(engine_or_url)
        elif database_url is not None:
            self._engine = create_engine(database_url)
        else:
            self._engine = factory.get_engine()

    @property
    def is_connected(self) -> bool:
        """True once the engine is available (immediately after construction)."""
        return self._engine is not None

    async def init(self) -> None:
        """Bootstrap the schema on SQLite; no-op on PostgreSQL (Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """No-op — the factory or caller owns the engine lifecycle."""

    async def save_run(
        self,
        *,
        agent_name: str,
        input_text: str = "",
        output_text: str = "",
        status: str = "completed",
        total_tokens: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        model: str = "",
        duration_ms: int = 0,
        tool_calls: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        started_at: float | None = None,
        completed_at: float | None = None,
        error: str | None = None,
        project_id: str | None = None,
        run_type: str = "standalone",
        parent_workflow_run_id: str | None = None,
    ) -> str:
        """Save a new run record. Returns the generated run_id."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)
        run_id = uuid.uuid4().hex[:12]
        now = time.time()

        # Note: the ORM attribute is `metadata_` but the column name is "metadata".
        # When inserting via Core we use the column key "metadata".
        stmt = insert(_tbl).values(
            run_id=run_id,
            agent_name=agent_name,
            project_id=resolved_project,
            status=status,
            input_text=input_text,
            output_text=output_text,
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            model=model,
            duration_ms=duration_ms,
            tool_calls=tool_calls or [],
            metadata=metadata or {},
            started_at=started_at if started_at is not None else now,
            completed_at=completed_at if completed_at is not None else now,
            error=error,
            run_type=run_type,
            parent_workflow_run_id=parent_workflow_run_id,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
        return run_id

    async def get_run(self, run_id: str, *, project_id: str | None = None) -> RunRecord | None:
        """Get a run by ID, scoped to project."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)
        stmt = select(_tbl).where(
            _tbl.c.run_id == run_id,
            _tbl.c.project_id == resolved_project,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        if row is None:
            return None
        return self._row_to_record(row)

    async def list_runs(
        self,
        *,
        agent_name: str | None = None,
        status: str | None = None,
        model: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 50,
        offset: int = 0,
        project_id: str | None = None,
        run_type: str | None = None,
        exclude_run_types: list[str] | None = None,
    ) -> list[RunRecord]:
        """List runs with optional filtering, scoped to project."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)

        stmt = select(_tbl).where(_tbl.c.project_id == resolved_project)

        if agent_name:
            stmt = stmt.where(_tbl.c.agent_name.ilike(f"%{agent_name}%"))
        if status:
            stmt = stmt.where(_tbl.c.status == status)
        if model:
            stmt = stmt.where(_tbl.c.model == model)
        if since:
            stmt = stmt.where(_tbl.c.started_at >= since)
        if until:
            stmt = stmt.where(_tbl.c.started_at <= until)
        if run_type:
            stmt = stmt.where(_tbl.c.run_type == run_type)
        if exclude_run_types:
            stmt = stmt.where(_tbl.c.run_type.notin_(exclude_run_types))

        stmt = stmt.order_by(_tbl.c.started_at.desc()).limit(limit).offset(offset)

        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [self._row_to_record(row) for row in rows]

    async def delete_run(self, run_id: str, *, project_id: str | None = None) -> bool:
        """Delete a run by ID, scoped to project. Returns True if deleted."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)
        stmt = sa_delete(_tbl).where(
            _tbl.c.run_id == run_id,
            _tbl.c.project_id == resolved_project,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
        return True

    async def count(
        self,
        *,
        agent_name: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """Count runs with optional filtering, scoped to project."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)

        stmt = select(func.count()).select_from(_tbl).where(
            _tbl.c.project_id == resolved_project
        )
        if agent_name:
            stmt = stmt.where(_tbl.c.agent_name.ilike(f"%{agent_name}%"))
        if status:
            stmt = stmt.where(_tbl.c.status == status)

        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            return result.scalar() or 0

    async def clear(self) -> None:
        """Delete all runs."""
        self._check_connected()
        async with self._engine.begin() as conn:
            await conn.execute(sa_delete(_tbl))

    # ------------------------------------------------------------------
    # ctx-scoped API (multi-tenancy v2) — own + org-shared on reads,
    # own rows only on mutations. The route-facing surface; the
    # project_id-keyed methods above stay for the engine / legacy callers.
    # ------------------------------------------------------------------

    async def list_runs_for(
        self,
        ctx,
        *,
        agent_name: str | None = None,
        status: str | None = None,
        run_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunRecord]:
        """List runs visible to ``ctx`` (own project + org-shared global rows)."""
        self._check_connected()
        scoping.require_ctx(ctx)
        stmt = scoping.apply_scope(select(_tbl), _tbl, ctx)
        if agent_name:
            stmt = stmt.where(_tbl.c.agent_name.ilike(f"%{agent_name}%"))
        if status:
            stmt = stmt.where(_tbl.c.status == status)
        if run_type:
            stmt = stmt.where(_tbl.c.run_type == run_type)
        stmt = stmt.order_by(_tbl.c.started_at.desc()).limit(limit).offset(offset)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [self._row_to_record(row) for row in rows]

    async def get_run_for(self, run_id: str, ctx) -> RunRecord | None:
        """Get a run by id, or ``None`` when it is outside ``ctx``'s read scope."""
        self._check_connected()
        scoping.require_ctx(ctx)
        async with self._engine.connect() as conn:
            row = (
                (await conn.execute(select(_tbl).where(_tbl.c.run_id == run_id))).mappings().first()
            )
        if row is None or not scoping.row_in_scope(row, ctx):
            return None
        return self._row_to_record(row)

    async def save_run_for(
        self,
        ctx,
        *,
        agent_name: str,
        run_id: str | None = None,
        input_text: str = "",
        output_text: str = "",
        status: str = "completed",
        total_tokens: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        model: str = "",
        duration_ms: int = 0,
        tool_calls: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        started_at: float | None = None,
        completed_at: float | None = None,
        error: str | None = None,
        run_type: str = "standalone",
        parent_workflow_run_id: str | None = None,
    ) -> str:
        """Save a run, stamping ``project_id`` from ``ctx`` (never the body).

        When ``run_id`` is given, that id is persisted (so a route can emit an id
        before the run finishes and still resolve it afterwards); otherwise one is
        generated. Returns the persisted run_id.
        """
        self._check_connected()
        scoping.require_ctx(ctx)
        run_id = run_id or uuid.uuid4().hex[:12]
        now = time.time()
        stmt = insert(_tbl).values(
            run_id=run_id,
            agent_name=agent_name,
            status=status,
            input_text=input_text,
            output_text=output_text,
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            model=model,
            duration_ms=duration_ms,
            tool_calls=tool_calls or [],
            metadata=metadata or {},
            started_at=started_at if started_at is not None else now,
            completed_at=completed_at if completed_at is not None else now,
            error=error,
            run_type=run_type,
            parent_workflow_run_id=parent_workflow_run_id,
            **scoping.scope_values(ctx),
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
        return run_id

    async def delete_run_for(self, run_id: str, ctx) -> bool:
        """Delete a run in ``ctx``'s write scope (own rows only). True if one went.

        A project actor cannot delete an org-shared row — the write-scope filter
        excludes inherited globals, so the delete matches zero rows.
        """
        self._check_connected()
        scoping.require_ctx(ctx)
        stmt = sa_delete(_tbl).where(_tbl.c.run_id == run_id, scoping.write_scope_filter(_tbl, ctx))
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount == 1

    async def cancel_run_for(self, run_id: str, ctx) -> bool:
        """Mark a run ``cancelled`` in ``ctx``'s write scope. True if one changed.

        Like :meth:`delete_run_for`, the write-scope filter is own-rows-only, so a
        project actor cannot cancel another project's run (or an org-shared row it
        merely inherits for reads) — the update matches zero rows and returns False.
        """
        self._check_connected()
        scoping.require_ctx(ctx)
        stmt = (
            sa_update(_tbl)
            .where(_tbl.c.run_id == run_id, scoping.write_scope_filter(_tbl, ctx))
            .values(status="cancelled")
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount == 1

    # ------------------------------------------------------------------
    # Event hook for BaseAgent integration
    # ------------------------------------------------------------------

    def create_event_hook(self):
        """Create an event hook that auto-records runs from BaseAgent.

        Returns:
            A callable matching ``EventCallback`` (event, data) -> None.
        """
        store = self
        run_starts: dict[str, float] = {}
        run_data: dict[str, dict[str, Any]] = {}

        async def hook(event: Any, data: dict[str, Any]) -> None:
            event_value = event.value if hasattr(event, "value") else str(event)
            agent_name = data.get("agent", "unknown")

            if event_value == "run_started":
                run_starts[agent_name] = time.time()
                run_data[agent_name] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                    "model": "",
                    "duration_ms": 0.0,
                    "tool_calls": [],
                }
            elif event_value == "llm_call_finished":
                acc = run_data.get(agent_name, {})
                acc["input_tokens"] = acc.get("input_tokens", 0) + data.get("input_tokens", 0)
                acc["output_tokens"] = acc.get("output_tokens", 0) + data.get("output_tokens", 0)
                acc["cost_usd"] = acc.get("cost_usd", 0.0) + data.get("cost_usd", 0.0)
                acc["duration_ms"] = acc.get("duration_ms", 0.0) + data.get("duration_ms", 0.0)
                acc["model"] = data.get("model", acc.get("model", ""))
            elif event_value == "tool_call_result":
                acc = run_data.get(agent_name, {})
                tool_calls = acc.get("tool_calls", [])
                tool_calls.append(
                    {
                        "tool_name": data.get("tool_name", ""),
                        "tool_call_id": data.get("tool_call_id", ""),
                    }
                )
                acc["tool_calls"] = tool_calls
            elif event_value == "run_finished":
                started = run_starts.pop(agent_name, time.time())
                acc = run_data.pop(agent_name, {})
                try:
                    await store.save_run(
                        agent_name=agent_name,
                        input_text=str(data.get("input", "")),
                        output_text=str(data.get("output", "")),
                        input_tokens=acc.get("input_tokens", 0),
                        output_tokens=acc.get("output_tokens", 0),
                        total_tokens=(acc.get("input_tokens", 0) + acc.get("output_tokens", 0)),
                        cost_usd=acc.get("cost_usd", 0.0),
                        model=acc.get("model", ""),
                        duration_ms=int(acc.get("duration_ms", 0.0)),
                        tool_calls=acc.get("tool_calls", []),
                        started_at=started,
                    )
                except Exception:
                    logger.exception("Failed to save run for agent %s", agent_name)

        return hook

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _row_to_record(self, row: Any) -> RunRecord:
        """Convert a SQLAlchemy RowMapping to a RunRecord.

        The column in the table is named "metadata" (the ORM attribute is
        metadata_), so we read row["metadata"] here.
        """
        tool_calls = row["tool_calls"]
        if isinstance(tool_calls, str):
            tool_calls = json.loads(tool_calls)

        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        return RunRecord(
            run_id=row["run_id"],
            agent_name=row["agent_name"],
            project_id=row.get("project_id", "default"),
            status=row["status"],
            input_text=row["input_text"],
            output_text=row["output_text"],
            total_tokens=row["total_tokens"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_usd=row["cost_usd"],
            model=row["model"],
            duration_ms=row["duration_ms"],
            tool_calls=tool_calls or [],
            metadata=metadata or {},
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            error=row["error"],
            run_type=row.get("run_type", "standalone"),
            parent_workflow_run_id=row.get("parent_workflow_run_id"),
        )

    def _check_connected(self) -> None:
        if self._engine is None:
            raise RuntimeError("RunStore has no engine. Pass engine= or a database URL.")
