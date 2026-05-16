# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Session checkpoint and restore for agent conversations.

Provides lightweight JSON-based session persistence for saving and
resuming agent conversations. No database required — works with
file storage or any dict-compatible backend.

Usage::

    from sagewai.core.session_store import SessionStore, SessionCheckpoint

    store = SessionStore(path="/tmp/sessions")

    # Save after a conversation
    checkpoint = SessionCheckpoint.create(
        agent_name="my-agent",
        model="gpt-4o",
        messages=[{"role": "user", "content": "hello"}],
        session_id="abc",
    )
    await store.save(checkpoint)

    # Restore later
    checkpoint = await store.load("abc")
    messages = checkpoint.messages
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionCheckpoint:
    """Serializable snapshot of an agent conversation session."""

    session_id: str = ""
    agent_name: str = ""
    model: str = ""
    system_prompt: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    token_count: int = 0
    turn_count: int = 0
    accumulated_cost: float = 0.0
    stop_reason: str = ""  # completed, max_turns, budget_exceeded, error
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        agent_name: str = "",
        model: str = "",
        system_prompt: str = "",
        messages: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> SessionCheckpoint:
        """Create a new checkpoint with a generated session ID."""
        return cls(
            session_id=session_id or uuid.uuid4().hex[:16],
            agent_name=agent_name,
            model=model,
            system_prompt=system_prompt,
            messages=messages or [],
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionCheckpoint:
        """Reconstruct from a dictionary, ignoring unknown keys."""
        return cls(
            **{
                k: v
                for k, v in data.items()
                if k in cls.__dataclass_fields__
            }
        )

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), indent=2, default=str)

    @classmethod
    def from_json(cls, text: str) -> SessionCheckpoint:
        """Deserialize from a JSON string."""
        return cls.from_dict(json.loads(text))


class SessionStore:
    """File-based session checkpoint storage.

    Parameters
    ----------
    path:
        Directory to store session files. Created if it doesn't exist.
    """

    def __init__(self, path: str | Path = ".sagewai/sessions") -> None:
        self._path = Path(path)

    async def save(self, checkpoint: SessionCheckpoint) -> str:
        """Save a checkpoint to disk. Returns the session ID."""
        import asyncio

        checkpoint.updated_at = time.time()
        self._path.mkdir(parents=True, exist_ok=True)
        file_path = self._path / f"{checkpoint.session_id}.json"

        def _write() -> None:
            file_path.write_text(
                checkpoint.to_json(), encoding="utf-8"
            )

        await asyncio.to_thread(_write)
        logger.debug("Session saved: %s", file_path)
        return checkpoint.session_id

    def get_path(self, session_id: str) -> Path:
        """Get the file path for a session (for external use)."""
        return self._path / f"{session_id}.json"

    async def load(self, session_id: str) -> SessionCheckpoint | None:
        """Load a checkpoint by session ID. Returns None if not found."""
        import asyncio

        file_path = self._path / f"{session_id}.json"
        if not file_path.exists():
            return None

        def _read() -> str:
            return file_path.read_text(encoding="utf-8")

        text = await asyncio.to_thread(_read)
        return SessionCheckpoint.from_json(text)

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all saved sessions with basic metadata."""
        import asyncio

        if not self._path.exists():
            return []

        def _read_all() -> list[dict[str, Any]]:
            sessions: list[dict[str, Any]] = []
            for file_path in sorted(
                self._path.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ):
                try:
                    data = json.loads(
                        file_path.read_text(encoding="utf-8")
                    )
                    sessions.append({
                        "session_id": data.get(
                            "session_id", file_path.stem
                        ),
                        "agent_name": data.get("agent_name", ""),
                        "model": data.get("model", ""),
                        "turn_count": data.get("turn_count", 0),
                        "stop_reason": data.get("stop_reason", ""),
                        "created_at": data.get("created_at", 0),
                        "updated_at": data.get("updated_at", 0),
                    })
                except (json.JSONDecodeError, OSError):
                    continue
            return sessions

        return await asyncio.to_thread(_read_all)

    async def delete(self, session_id: str) -> bool:
        """Delete a session checkpoint."""
        file_path = self._path / f"{session_id}.json"
        if file_path.exists():
            file_path.unlink()
            return True
        return False


class InMemorySessionStore:
    """In-memory session store for testing."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionCheckpoint] = {}

    async def save(self, checkpoint: SessionCheckpoint) -> str:
        """Save a checkpoint in memory. Returns the session ID."""
        checkpoint.updated_at = time.time()
        self._sessions[checkpoint.session_id] = checkpoint
        return checkpoint.session_id

    async def load(
        self, session_id: str
    ) -> SessionCheckpoint | None:
        """Load a checkpoint by session ID."""
        return self._sessions.get(session_id)

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all saved sessions with basic metadata."""
        return [
            {
                "session_id": cp.session_id,
                "agent_name": cp.agent_name,
                "model": cp.model,
                "turn_count": cp.turn_count,
                "stop_reason": cp.stop_reason,
                "created_at": cp.created_at,
                "updated_at": cp.updated_at,
            }
            for cp in sorted(
                self._sessions.values(),
                key=lambda c: c.updated_at,
                reverse=True,
            )
        ]

    async def delete(self, session_id: str) -> bool:
        """Delete a session checkpoint."""
        return self._sessions.pop(session_id, None) is not None
