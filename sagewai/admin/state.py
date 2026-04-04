# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Admin state — in-memory store for runs and sessions.

Provides the state backend for the admin API.  When a
:class:`~sagewai.admin.store.RunStore` is attached, runs are
persisted to PostgreSQL and survive restarts.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from sagewai.admin.models import (
    RunDetail,
    RunSummary,
    SessionInfo,
    StepInfo,
    ToolCallRecord,
)

if TYPE_CHECKING:
    from sagewai.admin.store import RunStore

logger = logging.getLogger(__name__)


class AdminState:
    """In-memory state for admin API, optionally backed by RunStore (Postgres).

    Tracks agent runs, sessions, and provides query methods.
    """

    def __init__(self, max_runs: int = 1000, run_store: RunStore | None = None) -> None:
        self._runs: dict[str, RunDetail] = {}
        self._sessions: dict[str, SessionInfo] = {}
        self._agent_run_counts: dict[str, int] = {}
        self._max_runs = max_runs
        self._run_store = run_store

    # ------------------------------------------------------------------
    # Run tracking
    # ------------------------------------------------------------------

    def record_run(
        self,
        *,
        agent_name: str,
        input_text: str = "",
        output_text: str = "",
        status: str = "completed",
        total_tokens: int = 0,
        tool_calls: list[dict[str, Any]] | None = None,
        steps: list[dict[str, Any]] | None = None,
        started_at: float | None = None,
        completed_at: float | None = None,
    ) -> str:
        """Record a completed agent run.

        Returns:
            The generated run_id.
        """
        run_id = uuid.uuid4().hex[:12]
        now = time.time()

        tc_records = []
        if tool_calls:
            for tc in tool_calls:
                tc_records.append(
                    ToolCallRecord(
                        tool_name=tc.get("tool_name", ""),
                        arguments=tc.get("arguments", ""),
                        result_preview=tc.get("result_preview", "")[:200],
                        duration_ms=tc.get("duration_ms", 0),
                    )
                )

        step_records = []
        if steps:
            for s in steps:
                step_records.append(
                    StepInfo(
                        step_type=s.get("step_type", ""),
                        detail=s.get("detail", ""),
                        duration_ms=s.get("duration_ms", 0),
                    )
                )

        run = RunDetail(
            run_id=run_id,
            agent_name=agent_name,
            status=status,
            input_text=input_text,
            output_text=output_text,
            started_at=started_at or now,
            completed_at=completed_at or now,
            total_tokens=total_tokens,
            tool_calls=tc_records,
            steps=step_records,
        )

        self._runs[run_id] = run
        self._agent_run_counts[agent_name] = self._agent_run_counts.get(agent_name, 0) + 1

        # Evict oldest runs if over limit
        if len(self._runs) > self._max_runs:
            oldest_key = next(iter(self._runs))
            del self._runs[oldest_key]

        # Persist to RunStore (fire-and-forget)
        if self._run_store is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._persist_run(run, tool_calls))
            except RuntimeError:
                pass

        return run_id

    async def _persist_run(
        self, run: RunDetail, tool_calls: list[dict[str, Any]] | None
    ) -> None:
        """Persist a run record to the RunStore."""
        if self._run_store is None:
            return
        try:
            duration_ms = int((run.completed_at - run.started_at) * 1000) if run.completed_at and run.started_at else 0
            await self._run_store.save_run(
                agent_name=run.agent_name,
                input_text=run.input_text,
                output_text=run.output_text,
                status=run.status,
                total_tokens=run.total_tokens,
                duration_ms=duration_ms,
                tool_calls=tool_calls,
                started_at=run.started_at,
                completed_at=run.completed_at,
            )
        except Exception:
            logger.exception("Failed to persist run for agent %s", run.agent_name)

    def get_run(self, run_id: str) -> RunDetail | None:
        """Get a run by ID."""
        return self._runs.get(run_id)

    def list_runs(
        self,
        *,
        agent_name: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunSummary]:
        """List runs with optional filtering."""
        runs = list(self._runs.values())

        if agent_name:
            _q = agent_name.lower()
            runs = [r for r in runs if _q in r.agent_name.lower()]
        if status:
            runs = [r for r in runs if r.status == status]

        # Sort by started_at descending (most recent first)
        runs.sort(key=lambda r: r.started_at or 0, reverse=True)

        # Apply pagination
        runs = runs[offset : offset + limit]

        return [
            RunSummary(
                run_id=r.run_id,
                agent_name=r.agent_name,
                status=r.status,
                input_preview=r.input_text[:100],
                output_preview=r.output_text[:100],
                started_at=r.started_at,
                completed_at=r.completed_at,
                total_tokens=r.total_tokens,
            )
            for r in runs
        ]

    def get_agent_run_count(self, agent_name: str) -> int:
        """Get total number of runs for an agent."""
        return self._agent_run_counts.get(agent_name, 0)

    # ------------------------------------------------------------------
    # Session tracking
    # ------------------------------------------------------------------

    def start_session(self, agent_name: str) -> str:
        """Start tracking a new active session.

        Returns:
            The generated session_id.
        """
        session_id = uuid.uuid4().hex[:12]
        self._sessions[session_id] = SessionInfo(
            session_id=session_id,
            agent_name=agent_name,
            started_at=time.time(),
        )
        return session_id

    def update_session(self, session_id: str, *, message_count: int | None = None) -> None:
        """Update session metadata."""
        session = self._sessions.get(session_id)
        if session and message_count is not None:
            session.message_count = message_count

    def end_session(self, session_id: str) -> None:
        """End and remove an active session."""
        self._sessions.pop(session_id, None)

    def get_session(self, session_id: str) -> SessionInfo | None:
        """Get session by ID."""
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[SessionInfo]:
        """List all active sessions."""
        return list(self._sessions.values())

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def total_runs(self) -> int:
        return len(self._runs)

    @property
    def active_sessions(self) -> int:
        return len(self._sessions)

    def clear(self) -> None:
        """Clear all state."""
        self._runs.clear()
        self._sessions.clear()
        self._agent_run_counts.clear()
