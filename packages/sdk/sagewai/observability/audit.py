# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Structured audit logging for sensitive operations.

Records who did what, when, with what credentials, and what happened.
Designed for compliance, forensics, and operational visibility.

Usage::

    from sagewai.observability.audit import AuditLogger, AuditEvent

    audit = AuditLogger()

    # Log an LLM call
    audit.log(AuditEvent(
        action="llm_call",
        agent_name="researcher",
        model="gpt-4o",
        input_summary="Search for quantum computing papers",
        output_summary="Found 5 relevant papers...",
        tokens_used=1500,
        cost_usd=0.045,
        project_id="acme-corp",
    ))

    # Log a tool execution
    audit.log(AuditEvent(
        action="tool_call",
        agent_name="researcher",
        tool_name="web_search",
        input_summary="query: quantum computing",
        output_summary="5 results returned",
        project_id="acme-corp",
    ))

    # Log a workflow event
    audit.log(AuditEvent(
        action="workflow_started",
        agent_name="pipeline",
        workflow_name="article-pipeline",
        run_id="run-abc123",
        project_id="acme-corp",
    ))

    # Export to JSONL
    audit.export_jsonl("audit_log.jsonl")

    # Create BaseAgent event hook
    agent.on_event(audit.create_event_hook(project_id="acme-corp"))
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from sagewai.core.events import AgentEvent

logger = logging.getLogger(__name__)


class AuditEvent(BaseModel):
    """A structured audit event."""

    timestamp: float = Field(default_factory=time.time)
    action: str  # llm_call, tool_call, workflow_started, workflow_completed, etc.
    agent_name: str = ""
    model: str = ""
    tool_name: str = ""
    workflow_name: str = ""
    run_id: str = ""
    project_id: str = ""
    user_id: str = ""
    input_summary: str = ""
    output_summary: str = ""
    tokens_used: int = 0
    cost_usd: float = 0.0
    duration_ms: float = 0.0
    status: str = "success"  # success, error, denied
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditBackend:
    """Protocol for audit event persistence."""

    async def write(self, events: list[AuditEvent]) -> None:
        """Write a batch of audit events to the backend."""
        raise NotImplementedError


class InMemoryAuditBackend(AuditBackend):
    """In-memory backend for testing."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def write(self, events: list[AuditEvent]) -> None:
        """Append events to the in-memory list."""
        self.events.extend(events)

    def query(
        self,
        *,
        project_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Query stored events with optional filters.

        Parameters
        ----------
        project_id:
            If provided, only return events for this project.
        action:
            If provided, only return events with this action type.
        limit:
            Maximum number of events to return.
        """
        results = self.events
        if project_id is not None:
            results = [e for e in results if e.project_id == project_id]
        if action is not None:
            results = [e for e in results if e.action == action]
        return results[:limit]


class FileAuditBackend(AuditBackend):
    """JSONL file backend.

    Parameters
    ----------
    base_dir:
        Base directory for JSONL output files. When events carry a
        ``project_id``, they are written to ``{base_dir}/{project_id}/audit.jsonl``.
        Events without a ``project_id`` go to ``{base_dir}/audit.jsonl``.
    """

    def __init__(self, path: str) -> None:
        self._path = path

    def _resolve_path(self, project_id: str) -> str:
        """Return the JSONL file path, scoped by project_id when present."""
        import os

        if project_id:
            directory = os.path.join(self._path, project_id)
            os.makedirs(directory, exist_ok=True)
            return os.path.join(directory, "audit.jsonl")
        return self._path if self._path.endswith(".jsonl") else os.path.join(self._path, "audit.jsonl")

    async def write(self, events: list[AuditEvent]) -> None:
        """Write events as newline-delimited JSON.

        Events are grouped by ``project_id`` and written to separate files.
        Uses ``aiofiles`` for async I/O when available, falls back to
        synchronous writes otherwise.
        """
        # Group events by project_id for file routing
        by_project: dict[str, list[AuditEvent]] = {}
        for event in events:
            by_project.setdefault(event.project_id, []).append(event)

        for pid, group in by_project.items():
            path = self._resolve_path(pid)
            try:
                import aiofiles

                async with aiofiles.open(path, "a") as f:
                    for event in group:
                        await f.write(event.model_dump_json() + "\n")
            except ImportError:
                import os

                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a") as f:
                    for event in group:
                        f.write(event.model_dump_json() + "\n")


# ------------------------------------------------------------------
# Event-type mapping for the BaseAgent hook
# ------------------------------------------------------------------

_EVENT_ACTION_MAP: dict[AgentEvent, str] = {
    AgentEvent.LLM_CALL_FINISHED: "llm_call",
    AgentEvent.TOOL_CALL_RESULT: "tool_call",
    AgentEvent.RUN_STARTED: "agent_run_started",
    AgentEvent.RUN_FINISHED: "agent_run_finished",
    AgentEvent.RUN_ERROR: "agent_run_error",
}


class AuditLogger:
    """Structured audit logger with pluggable backends.

    Parameters
    ----------
    max_buffer:
        Max events to buffer in memory before auto-flush.
    backends:
        List of AuditBackend instances for persisting events.
    """

    def __init__(
        self,
        *,
        max_buffer: int = 1000,
        backends: list[AuditBackend] | None = None,
    ) -> None:
        self._buffer: list[AuditEvent] = []
        self._max_buffer = max_buffer
        self._backends: list[AuditBackend] = backends or []

    def log(self, event: AuditEvent) -> None:
        """Record an audit event.

        Appends the event to the internal buffer and triggers an
        auto-flush when the buffer exceeds ``max_buffer``.
        """
        self._buffer.append(event)
        if len(self._buffer) >= self._max_buffer:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.flush())
            except RuntimeError:
                # No running event loop — skip auto-flush; caller can
                # flush manually or via export_jsonl.
                pass

    async def flush(self) -> None:
        """Flush buffered events to all backends."""
        if not self._buffer or not self._backends:
            return
        batch = list(self._buffer)
        self._buffer.clear()
        for backend in self._backends:
            try:
                await backend.write(batch)
            except Exception:
                logger.exception(
                    "AuditBackend %s failed to write %d events",
                    type(backend).__name__,
                    len(batch),
                )
                # Re-add events so they aren't silently lost
                self._buffer.extend(batch)

    def create_event_hook(
        self, *, project_id: str = "", user_id: str = ""
    ) -> Callable:
        """Create a BaseAgent event hook that auto-logs audit events.

        Returns a callback suitable for ``agent.on_event()``.

        Maps AgentEvent types to AuditEvent actions:
        - ``LLM_CALL_FINISHED`` -> ``action="llm_call"``
        - ``TOOL_CALL_RESULT`` -> ``action="tool_call"``
        - ``RUN_STARTED`` -> ``action="agent_run_started"``
        - ``RUN_FINISHED`` -> ``action="agent_run_finished"``
        - ``RUN_ERROR`` -> ``action="agent_run_error"``
        """

        async def _hook(event: AgentEvent, data: dict[str, Any]) -> None:
            action = _EVENT_ACTION_MAP.get(event)
            if action is None:
                return

            audit_event = AuditEvent(
                action=action,
                project_id=project_id,
                user_id=user_id,
                agent_name=data.get("agent", ""),
            )

            if event == AgentEvent.LLM_CALL_FINISHED:
                audit_event.model = data.get("model", "")
                audit_event.tokens_used = (
                    data.get("input_tokens", 0) + data.get("output_tokens", 0)
                )
                audit_event.cost_usd = data.get("cost_usd", 0.0)
                audit_event.duration_ms = data.get("duration_ms", 0.0)

            elif event == AgentEvent.TOOL_CALL_RESULT:
                audit_event.tool_name = data.get("tool_name", "")
                audit_event.output_summary = str(data.get("content", ""))[:200]
                if data.get("error"):
                    audit_event.status = "error"
                    audit_event.error = str(data["error"])

            elif event == AgentEvent.RUN_ERROR:
                audit_event.status = "error"
                audit_event.error = str(data.get("error", ""))

            self.log(audit_event)

        return _hook

    def export_jsonl(self, path: str) -> int:
        """Export buffer to JSONL file.

        Returns the number of events written.
        """
        count = len(self._buffer)
        with open(path, "a") as f:
            for event in self._buffer:
                f.write(event.model_dump_json() + "\n")
        return count

    @property
    def events(self) -> list[AuditEvent]:
        """Current buffered events."""
        return list(self._buffer)
