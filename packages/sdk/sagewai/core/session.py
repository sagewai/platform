# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Session persistence — save and resume agent conversations.

Provides a SessionStore protocol for pluggable backends, a SessionRecord
model for serialization, and an InMemorySessionStore for testing.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class SessionRecord(BaseModel):
    """Serializable snapshot of a conversation session."""

    session_id: str
    project_id: str | None = None
    agent_name: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    memory_keys: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


@runtime_checkable
class SessionStore(Protocol):
    """Protocol for session persistence backends."""

    async def save(self, record: SessionRecord) -> None: ...

    async def load(self, session_id: str, project_id: str | None = None) -> SessionRecord | None: ...

    async def list_sessions(
        self, project_id: str | None = None, limit: int = 20
    ) -> list[SessionRecord]: ...

    async def delete(self, session_id: str, project_id: str | None = None) -> None: ...


class InMemorySessionStore:
    """In-memory SessionStore for testing."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str | None], SessionRecord] = {}

    async def save(self, record: SessionRecord) -> None:
        record.updated_at = time.time()
        key = (record.session_id, record.project_id)
        self._records[key] = record

    async def load(self, session_id: str, project_id: str | None = None) -> SessionRecord | None:
        return self._records.get((session_id, project_id))

    async def list_sessions(
        self, project_id: str | None = None, limit: int = 20
    ) -> list[SessionRecord]:
        results = [r for r in self._records.values() if r.project_id == project_id]
        results.sort(key=lambda r: r.updated_at, reverse=True)
        return results[:limit]

    async def delete(self, session_id: str, project_id: str | None = None) -> None:
        self._records.pop((session_id, project_id), None)
