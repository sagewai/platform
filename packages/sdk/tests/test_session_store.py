"""Tests for session checkpoint and restore."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.core.session_store import (
    InMemorySessionStore,
    SessionCheckpoint,
    SessionStore,
)


# -- SessionCheckpoint -------------------------------------------------------


class TestSessionCheckpoint:
    def test_create_generates_session_id(self) -> None:
        cp = SessionCheckpoint.create(
            agent_name="test-agent",
            model="gpt-4o",
        )
        assert len(cp.session_id) == 16
        assert cp.agent_name == "test-agent"
        assert cp.model == "gpt-4o"
        assert cp.messages == []
        assert cp.turn_count == 0

    def test_create_with_explicit_id(self) -> None:
        cp = SessionCheckpoint.create(
            agent_name="a",
            session_id="my-id",
        )
        assert cp.session_id == "my-id"

    def test_create_with_messages(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        cp = SessionCheckpoint.create(
            agent_name="a", messages=msgs
        )
        assert cp.messages == msgs

    def test_create_with_kwargs(self) -> None:
        cp = SessionCheckpoint.create(
            agent_name="a",
            turn_count=5,
            accumulated_cost=0.03,
            stop_reason="max_turns",
        )
        assert cp.turn_count == 5
        assert cp.accumulated_cost == 0.03
        assert cp.stop_reason == "max_turns"

    def test_to_dict_roundtrip(self) -> None:
        cp = SessionCheckpoint.create(
            agent_name="test",
            model="claude-3",
            system_prompt="You are helpful.",
            messages=[{"role": "user", "content": "hi"}],
            turn_count=3,
        )
        d = cp.to_dict()
        restored = SessionCheckpoint.from_dict(d)
        assert restored.session_id == cp.session_id
        assert restored.agent_name == "test"
        assert restored.model == "claude-3"
        assert restored.system_prompt == "You are helpful."
        assert restored.messages == cp.messages
        assert restored.turn_count == 3

    def test_from_dict_ignores_unknown_keys(self) -> None:
        d = {
            "session_id": "x",
            "agent_name": "a",
            "unknown_field": 42,
        }
        cp = SessionCheckpoint.from_dict(d)
        assert cp.session_id == "x"
        assert cp.agent_name == "a"

    def test_to_json_roundtrip(self) -> None:
        cp = SessionCheckpoint.create(
            agent_name="json-test",
            model="gpt-4o",
            messages=[
                {"role": "user", "content": "ping"},
                {"role": "assistant", "content": "pong"},
            ],
            accumulated_cost=0.01,
        )
        text = cp.to_json()
        parsed = json.loads(text)
        assert parsed["agent_name"] == "json-test"
        assert len(parsed["messages"]) == 2

        restored = SessionCheckpoint.from_json(text)
        assert restored.session_id == cp.session_id
        assert restored.accumulated_cost == 0.01
        assert restored.messages == cp.messages


# -- SessionStore (file-based) -----------------------------------------------


class TestSessionStore:
    @pytest.mark.asyncio
    async def test_save_creates_file(self, tmp_path) -> None:
        store = SessionStore(path=tmp_path / "sessions")
        cp = SessionCheckpoint.create(
            agent_name="a", session_id="s1"
        )
        result = await store.save(cp)
        assert result == "s1"
        assert store.get_path("s1").exists()

    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(
        self, tmp_path
    ) -> None:
        store = SessionStore(path=tmp_path / "sessions")
        cp = SessionCheckpoint.create(
            agent_name="roundtrip",
            model="gpt-4o",
            messages=[{"role": "user", "content": "test"}],
            session_id="rt1",
            turn_count=2,
            accumulated_cost=0.05,
        )
        await store.save(cp)

        loaded = await store.load("rt1")
        assert loaded is not None
        assert loaded.agent_name == "roundtrip"
        assert loaded.model == "gpt-4o"
        assert loaded.turn_count == 2
        assert loaded.accumulated_cost == 0.05
        assert len(loaded.messages) == 1

    @pytest.mark.asyncio
    async def test_load_returns_none_for_missing(
        self, tmp_path
    ) -> None:
        store = SessionStore(path=tmp_path / "sessions")
        result = await store.load("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_sessions(self, tmp_path) -> None:
        store = SessionStore(path=tmp_path / "sessions")
        for i in range(3):
            cp = SessionCheckpoint.create(
                agent_name=f"agent-{i}",
                session_id=f"s{i}",
                turn_count=i,
            )
            await store.save(cp)

        sessions = await store.list_sessions()
        assert len(sessions) == 3
        # Most recently saved should be first
        ids = [s["session_id"] for s in sessions]
        assert "s0" in ids
        assert "s1" in ids
        assert "s2" in ids

    @pytest.mark.asyncio
    async def test_list_sessions_empty_dir(
        self, tmp_path
    ) -> None:
        store = SessionStore(path=tmp_path / "no-such-dir")
        sessions = await store.list_sessions()
        assert sessions == []

    @pytest.mark.asyncio
    async def test_delete(self, tmp_path) -> None:
        store = SessionStore(path=tmp_path / "sessions")
        cp = SessionCheckpoint.create(session_id="del1")
        await store.save(cp)

        assert await store.delete("del1") is True
        assert await store.load("del1") is None

    @pytest.mark.asyncio
    async def test_delete_missing(self, tmp_path) -> None:
        store = SessionStore(path=tmp_path / "sessions")
        assert await store.delete("nope") is False


# -- InMemorySessionStore ----------------------------------------------------


class TestInMemorySessionStore:
    @pytest.mark.asyncio
    async def test_save_and_load(self) -> None:
        store = InMemorySessionStore()
        cp = SessionCheckpoint.create(
            agent_name="mem-test",
            session_id="m1",
            turn_count=4,
        )
        await store.save(cp)

        loaded = await store.load("m1")
        assert loaded is not None
        assert loaded.agent_name == "mem-test"
        assert loaded.turn_count == 4

    @pytest.mark.asyncio
    async def test_load_missing(self) -> None:
        store = InMemorySessionStore()
        assert await store.load("nope") is None

    @pytest.mark.asyncio
    async def test_list_sessions(self) -> None:
        store = InMemorySessionStore()
        for i in range(3):
            cp = SessionCheckpoint.create(
                agent_name=f"a{i}",
                session_id=f"m{i}",
            )
            await store.save(cp)

        sessions = await store.list_sessions()
        assert len(sessions) == 3

    @pytest.mark.asyncio
    async def test_delete(self) -> None:
        store = InMemorySessionStore()
        cp = SessionCheckpoint.create(session_id="d1")
        await store.save(cp)

        assert await store.delete("d1") is True
        assert await store.load("d1") is None

    @pytest.mark.asyncio
    async def test_delete_missing(self) -> None:
        store = InMemorySessionStore()
        assert await store.delete("nope") is False


# -- BaseAgent integration ---------------------------------------------------


class TestBaseAgentSession:
    """Test save_session / restore_session on BaseAgent."""

    @pytest.mark.asyncio
    async def test_save_session(self) -> None:
        from sagewai.models.message import ChatMessage

        store = InMemorySessionStore()

        # Build a minimal mock agent with required attributes
        agent = MagicMock()
        agent.config.name = "test-agent"
        agent.config.model = "gpt-4o"
        agent.config.system_prompt = "Be helpful."
        agent._turn_count = 3
        agent._accumulated_cost = 0.02
        agent._event_listeners = []
        agent._emit = AsyncMock()

        from sagewai.core.base import BaseAgent

        messages = [
            ChatMessage.user("hello"),
            ChatMessage.assistant("hi there"),
        ]

        sid = await BaseAgent.save_session(
            agent,
            messages=messages,
            session_id="save-test",
            stop_reason="completed",
            session_store=store,
        )

        assert sid == "save-test"
        loaded = await store.load("save-test")
        assert loaded is not None
        assert loaded.agent_name == "test-agent"
        assert loaded.turn_count == 3
        assert loaded.accumulated_cost == 0.02
        assert len(loaded.messages) == 2

    @pytest.mark.asyncio
    async def test_restore_session(self) -> None:
        from sagewai.models.message import ChatMessage

        store = InMemorySessionStore()

        # Pre-populate store
        cp = SessionCheckpoint.create(
            agent_name="test-agent",
            model="gpt-4o",
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            session_id="restore-test",
            turn_count=5,
            accumulated_cost=0.1,
        )
        await store.save(cp)

        agent = MagicMock()
        agent._turn_count = 0
        agent._accumulated_cost = 0.0
        agent._event_listeners = []
        agent._emit = AsyncMock()

        from sagewai.core.base import BaseAgent

        messages = await BaseAgent.restore_session(
            agent,
            session_id="restore-test",
            session_store=store,
        )

        assert messages is not None
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "hello"
        assert agent._turn_count == 5
        assert agent._accumulated_cost == 0.1

    @pytest.mark.asyncio
    async def test_restore_session_not_found(self) -> None:
        store = InMemorySessionStore()

        agent = MagicMock()
        agent._event_listeners = []

        from sagewai.core.base import BaseAgent

        result = await BaseAgent.restore_session(
            agent,
            session_id="missing",
            session_store=store,
        )
        assert result is None
