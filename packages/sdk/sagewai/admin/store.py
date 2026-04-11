# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Agent run store — persistent storage for run records.

Provides a PostgreSQL-backed store for agent run records with CRUD
operations, filtering, and auto-recording from BaseAgent events.

Usage::

    from sagewai.admin.store import RunStore

    store = RunStore("postgresql://user:pass@host/db")
    await store.init()

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

from sagewai.core.context import get_current_project

logger = logging.getLogger(__name__)


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
    """PostgreSQL-backed agent run store.

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
                "asyncpg is required for PostgreSQL. " "Install with: uv add 'sagewai[postgres]'"
            ) from exc

        self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=5)

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

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

        sql = """
        INSERT INTO agent_runs
        (run_id, agent_name, project_id, status, input_text, output_text,
         total_tokens, input_tokens, output_tokens, cost_usd, model,
         duration_ms, tool_calls, metadata, started_at, completed_at, error,
         run_type, parent_workflow_run_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                sql,
                run_id,
                agent_name,
                resolved_project,
                status,
                input_text,
                output_text,
                total_tokens,
                input_tokens,
                output_tokens,
                cost_usd,
                model,
                duration_ms,
                json.dumps(tool_calls or []),
                json.dumps(metadata or {}),
                started_at or now,
                completed_at or now,
                error,
                run_type,
                parent_workflow_run_id,
            )
        return run_id

    async def get_run(self, run_id: str, *, project_id: str | None = None) -> RunRecord | None:
        """Get a run by ID, scoped to project."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM agent_runs WHERE run_id = $1 AND project_id = $2",
                run_id,
                resolved_project,
            )
        if not row:
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
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        conditions.append(f"project_id = ${idx}")
        params.append(resolved_project)
        idx += 1

        if agent_name:
            conditions.append(f"agent_name ILIKE ${idx}")
            params.append(f"%{agent_name}%")
            idx += 1
        if status:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1
        if model:
            conditions.append(f"model = ${idx}")
            params.append(model)
            idx += 1
        if since:
            conditions.append(f"started_at >= ${idx}")
            params.append(since)
            idx += 1
        if until:
            conditions.append(f"started_at <= ${idx}")
            params.append(until)
            idx += 1
        if run_type:
            conditions.append(f"run_type = ${idx}")
            params.append(run_type)
            idx += 1
        if exclude_run_types:
            placeholders = ", ".join(f"${idx + i}" for i in range(len(exclude_run_types)))
            conditions.append(f"run_type NOT IN ({placeholders})")
            params.extend(exclude_run_types)
            idx += len(exclude_run_types)

        where = " AND ".join(conditions)
        sql = (
            f"SELECT * FROM agent_runs WHERE {where} "
            f"ORDER BY started_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
        )
        params.extend([limit, offset])

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [self._row_to_record(row) for row in rows]

    async def delete_run(self, run_id: str, *, project_id: str | None = None) -> bool:
        """Delete a run by ID, scoped to project. Returns True if deleted."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM agent_runs WHERE run_id = $1 AND project_id = $2",
                run_id,
                resolved_project,
            )
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
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        conditions.append(f"project_id = ${idx}")
        params.append(resolved_project)
        idx += 1

        if agent_name:
            conditions.append(f"agent_name ILIKE ${idx}")
            params.append(f"%{agent_name}%")
            idx += 1
        if status:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1

        where = " AND ".join(conditions)
        sql = f"SELECT COUNT(*) as cnt FROM agent_runs WHERE {where}"

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
        return row["cnt"] if row else 0

    async def clear(self) -> None:
        """Delete all runs."""
        self._check_connected()
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM agent_runs")

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
        """Convert an asyncpg Record to a RunRecord."""
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
            tool_calls=(
                json.loads(row["tool_calls"])
                if isinstance(row["tool_calls"], str)
                else row["tool_calls"]
            ),
            metadata=(
                json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
            ),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            error=row["error"],
            run_type=row.get("run_type", "standalone"),
            parent_workflow_run_id=row.get("parent_workflow_run_id"),
        )

    def _check_connected(self) -> None:
        if self._pool is None:
            raise RuntimeError("RunStore not initialized. Call init() first.")
