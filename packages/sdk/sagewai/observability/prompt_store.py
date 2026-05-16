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

Mirrors the :class:`~sagewai.admin.store.RunStore` pattern: PostgreSQL-backed
with Alembic-managed schema, event-hook integration, and JSONL export.

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

from sagewai.core.context import resolve_project_id

logger = logging.getLogger(__name__)


@dataclass
class PromptLogRecord:
    """A single prompt-level log entry."""

    log_id: str
    run_id: str
    agent_name: str
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
            "created_at": self.created_at,
            "is_example": self.is_example,
            "tags": self.tags,
            "source": self.source,
            "input_text": self.input_text,
            "output_text": self.output_text,
        }


class PromptStore:
    """PostgreSQL-backed per-step prompt log store.

    Schema is managed by Alembic migrations. Run ``alembic upgrade head``
    before starting the application.

    Parameters
    ----------
    database_url:
        PostgreSQL connection string: ``"postgresql://user:pass@host/db"``.
    """

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._pool: Any = None

    @property
    def is_connected(self) -> bool:
        return self._pool is not None

    async def init(self) -> None:
        """Initialize the connection pool."""
        try:
            import asyncpg
        except ImportError as exc:
            raise ImportError(
                "asyncpg is required for PostgreSQL. "
                "Install with: uv add 'sagewai[postgres]'"
            ) from exc

        self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=5)

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

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

        sql = """
        INSERT INTO prompt_logs
        (log_id, run_id, agent_name, step_index, model,
         prompt_messages, response_message, input_tokens, output_tokens,
         cost_usd, duration_ms, strategy, metadata, created_at,
         is_example, tags, source, input_text, output_text, project_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                $15, $16, $17, $18, $19, $20)
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                sql,
                log_id,
                effective_run_id,
                agent_name,
                step_index,
                model,
                json.dumps(prompt_messages or []),
                json.dumps(response_message or {}),
                input_tokens,
                output_tokens,
                cost_usd,
                duration_ms,
                strategy,
                json.dumps(metadata or {}),
                now,
                is_example,
                json.dumps(tags or []),
                source,
                input_text,
                output_text,
                pid,
            )
        return log_id

    async def get_prompt_log(self, log_id: str) -> PromptLogRecord | None:
        """Get a prompt log by ID."""
        self._check_connected()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM prompt_logs WHERE log_id = $1", log_id
            )
        if not row:
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
        conditions: list[str] = [f"project_id = $1"]
        params: list[Any] = [pid]
        idx = 2

        if run_id:
            conditions.append(f"run_id = ${idx}")
            params.append(run_id)
            idx += 1
        if agent_name:
            conditions.append(f"agent_name ILIKE ${idx}")
            params.append(f"%{agent_name}%")
            idx += 1
        if model:
            conditions.append(f"model ILIKE ${idx}")
            params.append(f"%{model}%")
            idx += 1

        where = " AND ".join(conditions)
        sql = (
            f"SELECT * FROM prompt_logs WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
        )
        params.extend([limit, offset])

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
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
        sets: list[str] = []
        params: list[Any] = []
        idx = 1

        if tags is not None:
            sets.append(f"tags = ${idx}")
            params.append(json.dumps(tags))
            idx += 1
        if is_example is not None:
            sets.append(f"is_example = ${idx}")
            params.append(is_example)
            idx += 1

        if not sets:
            return await self.get_prompt_log(log_id)

        sql = (
            f"UPDATE prompt_logs SET {', '.join(sets)} "
            f"WHERE log_id = ${idx} RETURNING *"
        )
        params.append(log_id)

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
        if not row:
            return None
        return self._row_to_record(row)

    async def delete_prompt_log(self, log_id: str) -> bool:
        """Delete a prompt log record. Returns True if deleted."""
        self._check_connected()
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM prompt_logs WHERE log_id = $1", log_id
            )
        return result == "DELETE 1"

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
        sql = (
            "SELECT * FROM prompt_logs "
            "WHERE project_id = $1 AND agent_name = $2 AND is_example = true "
            "ORDER BY created_at DESC LIMIT $3"
        )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, pid, agent_name, limit)
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

    def _row_to_record(self, row: Any) -> PromptLogRecord:
        """Convert an asyncpg Record to a PromptLogRecord."""
        # Convert datetime created_at to float timestamp for the dataclass
        created_at = row["created_at"]
        if isinstance(created_at, datetime):
            created_at = created_at.timestamp()

        tags_raw = row.get("tags", "[]")
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])

        return PromptLogRecord(
            log_id=row["log_id"],
            run_id=row["run_id"] or "",
            agent_name=row["agent_name"],
            step_index=row["step_index"],
            model=row["model"],
            prompt_messages=(
                json.loads(row["prompt_messages"])
                if isinstance(row["prompt_messages"], str)
                else row["prompt_messages"]
            ),
            response_message=(
                json.loads(row["response_message"])
                if isinstance(row["response_message"], str)
                else row["response_message"]
            ),
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_usd=row["cost_usd"],
            duration_ms=row["duration_ms"],
            strategy=row["strategy"],
            metadata=(
                json.loads(row["metadata"])
                if isinstance(row["metadata"], str)
                else row["metadata"]
            ),
            created_at=created_at,
            is_example=row.get("is_example", False),
            tags=tags,
            source=row.get("source", "playground"),
            input_text=row.get("input_text", ""),
            output_text=row.get("output_text", ""),
        )

    def _check_connected(self) -> None:
        if self._pool is None:
            raise RuntimeError("PromptStore not initialized. Call init() first.")
