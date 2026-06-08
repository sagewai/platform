# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Per-step prompt logging store.

Captures every LLM call's full prompt messages and response, enabling
prompt iteration and debugging of multi-turn agent loops.

Backed by SQLAlchemy Core — works on both SQLite (default) and PostgreSQL.
Mirrors the :class:`~sagewai.admin.store.RunStore` pattern: engine-injected
constructor, ``init()`` creates the schema on SQLite (no-op on Postgres),
and all JSON columns receive Python objects directly.

Usage::

    from sagewai.observability.prompt_store import PromptStore

    store = PromptStore("postgresql://user:pass@host/db")
    await store.init()

    log_id = await store.save_prompt_log(
        run_id="abc123",
        agent_name="scout",
        step_index=0,
        model="gpt-4o",
        prompt_messages=[{"role": "user", "content": "Hello"}],
        response_message={"role": "assistant", "content": "Hi there"},
        input_tokens=10,
        output_tokens=5,
    )

    logs = await store.list_prompt_logs(run_id="abc123")
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.admin import scoping
from sagewai.core.context import resolve_project_id
from sagewai.db import factory
from sagewai.db.engine import create_engine
from sagewai.db.models import Base, PromptLog

logger = logging.getLogger(__name__)

_tbl = PromptLog.__table__


@dataclass
class PromptLogRecord:
    """A single prompt-level log entry."""

    log_id: str
    run_id: str
    agent_name: str
    project_id: str | None = None
    step_index: int = 0
    model: str = ""
    prompt_messages: list[dict[str, Any]] = field(default_factory=list)
    response_message: dict[str, Any] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    strategy: str = "react"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    # Unified fields (formerly on SavedInteraction)
    is_example: bool = False
    tags: list[str] = field(default_factory=list)
    source: str = "playground"
    input_text: str = ""
    output_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "log_id": self.log_id,
            "run_id": self.run_id,
            "agent_name": self.agent_name,
            "project_id": self.project_id,
            "step_index": self.step_index,
            "model": self.model,
            "prompt_messages": self.prompt_messages,
            "response_message": self.response_message,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "strategy": self.strategy,
            "metadata": self.metadata,
            "quality": (self.metadata or {}).get("quality", 0),
            "created_at": self.created_at,
            "is_example": self.is_example,
            "tags": self.tags,
            "source": self.source,
            "input_text": self.input_text,
            "output_text": self.output_text,
        }


class PromptStore:
    """SQLAlchemy Core per-step prompt log store — SQLite (default) or PostgreSQL.

    Constructor forms (all equivalent from the caller's perspective):

    * ``PromptStore()``
        Uses the process-wide engine from :func:`sagewai.db.factory.get_engine`.
    * ``PromptStore("postgresql://user:pass@host/db")``
        Positional URL string — back-compat with existing callers.
    * ``PromptStore(engine=my_async_engine)``
        Injected engine; used by tests and DI containers.
    * ``PromptStore(database_url="...")``
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
        # Priority: engine= > positional AsyncEngine > positional str > database_url= > factory
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

    async def save_prompt_log(
        self,
        *,
        run_id: str = "",
        agent_name: str,
        step_index: int = 0,
        model: str = "",
        prompt_messages: list[dict[str, Any]] | None = None,
        response_message: dict[str, Any] | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
        strategy: str = "react",
        metadata: dict[str, Any] | None = None,
        is_example: bool = False,
        tags: list[str] | None = None,
        source: str = "playground",
        input_text: str = "",
        output_text: str = "",
        project_id: str | None = None,
    ) -> str:
        """Save a new prompt log record. Returns the generated log_id."""
        self._check_connected()
        pid = resolve_project_id(project_id)
        log_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc)
        # Empty run_id → NULL to satisfy FK constraint
        effective_run_id = run_id if run_id else None

        # Note: ORM attr is `metadata_` but column name is "metadata".
        # `tags` is a Text column (JSON string), not JSONType — must encode explicitly.
        stmt = insert(_tbl).values(
            log_id=log_id,
            run_id=effective_run_id,
            agent_name=agent_name,
            step_index=step_index,
            model=model,
            prompt_messages=prompt_messages or [],
            response_message=response_message or {},
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            strategy=strategy,
            metadata=metadata or {},
            created_at=now,
            is_example=is_example,
            tags=json.dumps(tags or []),
            source=source,
            input_text=input_text,
            output_text=output_text,
            project_id=pid,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
        return log_id

    async def get_prompt_log(self, log_id: str) -> PromptLogRecord | None:
        """Get a prompt log by ID."""
        self._check_connected()
        stmt = select(_tbl).where(_tbl.c.log_id == log_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        if row is None:
            return None
        return self._row_to_record(row)

    async def list_prompt_logs(
        self,
        *,
        run_id: str | None = None,
        agent_name: str | None = None,
        model: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PromptLogRecord]:
        """List prompt logs with optional filtering."""
        self._check_connected()
        pid = resolve_project_id(project_id)

        stmt = select(_tbl).where(_tbl.c.project_id == pid)

        if run_id:
            stmt = stmt.where(_tbl.c.run_id == run_id)
        if agent_name:
            stmt = stmt.where(_tbl.c.agent_name.ilike(f"%{agent_name}%"))
        if model:
            stmt = stmt.where(_tbl.c.model.ilike(f"%{model}%"))

        stmt = stmt.order_by(_tbl.c.created_at.desc()).limit(limit).offset(offset)

        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [self._row_to_record(row) for row in rows]

    async def update_prompt_log(
        self,
        log_id: str,
        *,
        tags: list[str] | None = None,
        is_example: bool | None = None,
    ) -> PromptLogRecord | None:
        """Update tags or is_example flag on an existing prompt log."""
        self._check_connected()
        updates: dict[str, Any] = {}

        if tags is not None:
            updates["tags"] = json.dumps(tags)
        if is_example is not None:
            updates["is_example"] = is_example

        if not updates:
            return await self.get_prompt_log(log_id)

        stmt = sa_update(_tbl).where(_tbl.c.log_id == log_id).values(**updates)
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

        return await self.get_prompt_log(log_id)

    async def delete_prompt_log(self, log_id: str) -> bool:
        """Delete a prompt log record. Returns True if deleted."""
        self._check_connected()
        stmt = sa_delete(_tbl).where(_tbl.c.log_id == log_id)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount == 1

    async def list_examples(
        self,
        *,
        agent_name: str,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[PromptLogRecord]:
        """List prompt logs marked as examples for a specific agent."""
        self._check_connected()
        pid = resolve_project_id(project_id)
        stmt = (
            select(_tbl)
            .where(
                _tbl.c.project_id == pid,
                _tbl.c.agent_name == agent_name,
                _tbl.c.is_example == True,  # noqa: E712
            )
            .order_by(_tbl.c.created_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [self._row_to_record(row) for row in rows]

    # ------------------------------------------------------------------
    # ctx-scoped API (multi-tenancy v2) — own + org-shared on reads, own
    # rows only on mutations. These are the route-facing surface; the
    # log_id-keyed methods above carry NO project filter (a cross-tenant
    # leak if exposed) and remain only for the unscoped internal callers.
    # ------------------------------------------------------------------

    async def list_prompt_logs_for(
        self,
        ctx,
        *,
        run_id: str | None = None,
        agent_name: str | None = None,
        model: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PromptLogRecord]:
        """List prompt logs visible to ``ctx`` (own project + org-shared global)."""
        self._check_connected()
        scoping.require_ctx(ctx)
        stmt = scoping.apply_scope(select(_tbl), _tbl, ctx)
        if run_id:
            stmt = stmt.where(_tbl.c.run_id == run_id)
        if agent_name:
            stmt = stmt.where(_tbl.c.agent_name.ilike(f"%{agent_name}%"))
        if model:
            stmt = stmt.where(_tbl.c.model.ilike(f"%{model}%"))
        stmt = stmt.order_by(_tbl.c.created_at.desc()).limit(limit).offset(offset)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [self._row_to_record(row) for row in rows]

    async def get_prompt_log_for(self, log_id: str, ctx) -> PromptLogRecord | None:
        """Get a prompt log by id, or ``None`` when outside ``ctx``'s read scope."""
        self._check_connected()
        scoping.require_ctx(ctx)
        async with self._engine.connect() as conn:
            row = (
                (await conn.execute(select(_tbl).where(_tbl.c.log_id == log_id))).mappings().first()
            )
        if row is None or not scoping.row_in_scope(row, ctx):
            return None
        return self._row_to_record(row)

    async def save_prompt_log_for(
        self,
        ctx,
        *,
        run_id: str = "",
        agent_name: str,
        step_index: int = 0,
        model: str = "",
        prompt_messages: list[dict[str, Any]] | None = None,
        response_message: dict[str, Any] | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
        strategy: str = "react",
        metadata: dict[str, Any] | None = None,
        is_example: bool = False,
        tags: list[str] | None = None,
        source: str = "playground",
        input_text: str = "",
        output_text: str = "",
    ) -> str:
        """Save a prompt log, stamping ``project_id`` from ``ctx`` (never the body)."""
        self._check_connected()
        scoping.require_ctx(ctx)
        log_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc)
        effective_run_id = run_id if run_id else None
        stmt = insert(_tbl).values(
            log_id=log_id,
            run_id=effective_run_id,
            agent_name=agent_name,
            step_index=step_index,
            model=model,
            prompt_messages=prompt_messages or [],
            response_message=response_message or {},
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            strategy=strategy,
            metadata=metadata or {},
            created_at=now,
            is_example=is_example,
            tags=json.dumps(tags or []),
            source=source,
            input_text=input_text,
            output_text=output_text,
            **scoping.scope_values(ctx),
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
        return log_id

    async def update_prompt_log_for(
        self,
        log_id: str,
        ctx,
        *,
        tags: list[str] | None = None,
        is_example: bool | None = None,
        quality: int | None = None,
    ) -> PromptLogRecord | None:
        """Update a log in ``ctx``'s write scope (own rows only).

        ``quality`` has no dedicated column; it is merged into the ``metadata``
        JSON (read-modify-write). Returns the updated record, or ``None`` when the
        log is missing or not writable by ``ctx`` (a project actor may not mutate
        an org-shared row).
        """
        self._check_connected()
        scoping.require_ctx(ctx)
        updates: dict[str, Any] = {}
        if tags is not None:
            updates["tags"] = json.dumps(tags)
        if is_example is not None:
            updates["is_example"] = is_example

        async with self._engine.begin() as conn:
            row = (
                (await conn.execute(select(_tbl).where(_tbl.c.log_id == log_id))).mappings().first()
            )
            if row is None or not scoping.row_writable(row, ctx):
                return None
            if quality is not None:
                existing = row["metadata"]
                if isinstance(existing, str):
                    existing = json.loads(existing)
                merged = dict(existing or {})
                merged["quality"] = quality
                updates["metadata"] = merged
            if updates:
                await conn.execute(sa_update(_tbl).where(_tbl.c.log_id == log_id).values(**updates))
                row = (
                    (await conn.execute(select(_tbl).where(_tbl.c.log_id == log_id)))
                    .mappings()
                    .first()
                )
        return self._row_to_record(row)

    async def delete_prompt_log_for(self, log_id: str, ctx) -> bool:
        """Delete a log in ``ctx``'s write scope (own rows only). True if one went.

        A project actor cannot delete an org-shared row — the write-scope filter
        excludes inherited globals, so the delete matches zero rows.
        """
        self._check_connected()
        scoping.require_ctx(ctx)
        stmt = sa_delete(_tbl).where(_tbl.c.log_id == log_id, scoping.write_scope_filter(_tbl, ctx))
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount == 1

    async def list_examples_for(
        self,
        ctx,
        *,
        agent_name: str | None = None,
        limit: int = 50,
    ) -> list[PromptLogRecord]:
        """List example logs visible to ``ctx`` (own + shared), optionally one agent.

        Mirrors the single-org route: with no ``agent_name`` it returns every
        in-scope example, not zero.
        """
        self._check_connected()
        scoping.require_ctx(ctx)
        stmt = scoping.apply_scope(select(_tbl), _tbl, ctx).where(
            _tbl.c.is_example == True,  # noqa: E712
        )
        if agent_name:
            stmt = stmt.where(_tbl.c.agent_name == agent_name)
        stmt = stmt.order_by(_tbl.c.created_at.desc()).limit(limit)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [self._row_to_record(row) for row in rows]

    def export_jsonl(self, records: list[PromptLogRecord]) -> str:
        """Export prompt log records as JSONL string."""
        lines = [json.dumps(r.to_dict(), default=str) for r in records]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Event hook for BaseAgent integration
    # ------------------------------------------------------------------

    def create_event_hook(self, source: str = "playground"):
        """Create an event hook that auto-records prompt logs from BaseAgent.

        Parameters
        ----------
        source:
            Origin label stored with each log (``"playground"`` or ``"workflow"``).

        Returns:
            A callable matching ``EventCallback`` (event, data) -> None.
        """
        store = self
        _source = source

        async def hook(event: Any, data: dict[str, Any]) -> None:
            event_value = event.value if hasattr(event, "value") else str(event)

            if event_value == "prompt_logged":
                try:
                    await store.save_prompt_log(
                        run_id=data.get("run_id", ""),
                        agent_name=data.get("agent", "unknown"),
                        step_index=data.get("step_index", 0),
                        model=data.get("model", ""),
                        prompt_messages=data.get("messages", []),
                        response_message=data.get("response", {}),
                        input_tokens=data.get("input_tokens", 0),
                        output_tokens=data.get("output_tokens", 0),
                        cost_usd=data.get("cost_usd", 0.0),
                        duration_ms=int(data.get("duration_ms", 0)),
                        strategy=data.get("strategy", "react"),
                        metadata=data.get("metadata", {}),
                        source=_source,
                    )
                except Exception:
                    logger.exception(
                        "Failed to save prompt log for agent %s",
                        data.get("agent", "unknown"),
                    )

        return hook

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_record(row: Any) -> PromptLogRecord:
        """Convert a SQLAlchemy row mapping to a PromptLogRecord."""
        # Convert datetime created_at to float timestamp for the dataclass
        created_at = row["created_at"]
        if isinstance(created_at, datetime):
            created_at = created_at.timestamp()

        # tags is a Text column storing a JSON string
        tags_raw = row["tags"]
        if isinstance(tags_raw, str):
            tags = json.loads(tags_raw)
        else:
            tags = tags_raw or []

        # JSON columns (prompt_messages, response_message, metadata) are
        # returned as Python objects by SQLAlchemy's JSONType on both dialects.
        # Defensive str fallback handles any legacy string values.
        def _json(val: Any, default: Any) -> Any:
            if val is None:
                return default
            if isinstance(val, str):
                return json.loads(val)
            return val

        return PromptLogRecord(
            log_id=row["log_id"],
            run_id=row["run_id"] or "",
            agent_name=row["agent_name"],
            project_id=row.get("project_id"),
            step_index=row["step_index"],
            model=row["model"],
            prompt_messages=_json(row["prompt_messages"], []),
            response_message=_json(row["response_message"], {}),
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_usd=row["cost_usd"],
            duration_ms=row["duration_ms"],
            strategy=row["strategy"],
            metadata=_json(row["metadata"], {}),
            created_at=created_at,
            is_example=row["is_example"] or False,
            tags=tags,
            source=row["source"] or "playground",
            input_text=row["input_text"] or "",
            output_text=row["output_text"] or "",
        )

    def _check_connected(self) -> None:
        if self._engine is None:
            raise RuntimeError("PromptStore not initialized. Call init() first.")
