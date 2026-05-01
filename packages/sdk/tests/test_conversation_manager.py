# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for ConversationManager — multi-turn chat with memory and sessions."""

import pytest

from sagewai.core.conversation import ConversationManager
from sagewai.core.session import InMemorySessionStore
from sagewai.core.context import ProjectContext
from sagewai.memory.vector import VectorMemory
from sagewai.models.message import ChatMessage


class FakeAgent:
    """Minimal agent that echoes input for testing."""

    def __init__(self, name: str = "test-agent"):
        self.config = type(
            "Config", (), {"name": name, "system_prompt": "You are helpful.", "memory": None}
        )()
        self._call_count = 0

    async def chat_with_history(self, messages: list[ChatMessage]) -> ChatMessage:
        self._call_count += 1
        last_user = next(
            (m.content for m in reversed(messages) if m.role.value == "user"),
            "no input",
        )
        return ChatMessage.assistant(content=f"Echo: {last_user}")


class TestConversationManager:
    @pytest.mark.asyncio
    async def test_send_returns_response(self):
        agent = FakeAgent()
        mgr = ConversationManager(agent=agent)
        response = await mgr.send("hello")
        assert response == "Echo: hello"

    @pytest.mark.asyncio
    async def test_accumulates_messages(self):
        agent = FakeAgent()
        mgr = ConversationManager(agent=agent)
        await mgr.send("first")
        await mgr.send("second")
        # System + user1 + assistant1 + user2 + assistant2 = 5
        assert len(mgr.messages) == 5

    @pytest.mark.asyncio
    async def test_session_save_and_resume(self):
        store = InMemorySessionStore()
        agent = FakeAgent()

        # First session
        mgr1 = ConversationManager(agent=agent, session_id="sess-1", session_store=store)
        await mgr1.send("hello")

        # Second session — resume
        mgr2 = ConversationManager(agent=agent, session_id="sess-1", session_store=store)
        await mgr2.resume()
        assert len(mgr2.messages) >= 2  # at least system + restored messages

        await mgr2.send("continue")
        response = await mgr2.send("still here?")
        assert "still here" in response

    @pytest.mark.asyncio
    async def test_project_scoped_sessions(self):
        store = InMemorySessionStore()
        agent = FakeAgent()

        # Project A session
        async with ProjectContext(project_id="acme"):
            mgr_a = ConversationManager(agent=agent, session_id="shared-id", session_store=store)
            await mgr_a.send("acme message")

        # Project B session — same session_id but different project
        async with ProjectContext(project_id="beta"):
            mgr_b = ConversationManager(agent=agent, session_id="shared-id", session_store=store)
            await mgr_b.resume()
            assert len(mgr_b.messages) == 1  # only system prompt, no acme data

    @pytest.mark.asyncio
    async def test_memory_injection(self):
        memory = VectorMemory()
        await memory.store("User prefers Python for all code examples.")
        agent = FakeAgent()
        agent.config.memory = memory

        mgr = ConversationManager(agent=agent, memory=memory)
        await mgr.send("write some code")

        # Memory context should be injected into messages
        system_msgs = [m for m in mgr.messages if m.role.value == "system"]
        assert len(system_msgs) >= 1

    @pytest.mark.asyncio
    async def test_reset_clears_state(self):
        agent = FakeAgent()
        mgr = ConversationManager(agent=agent)
        await mgr.send("hello")
        assert len(mgr.messages) > 1
        mgr.reset()
        assert len(mgr.messages) == 1  # only system prompt

    @pytest.mark.asyncio
    async def test_auto_generates_session_id(self):
        agent = FakeAgent()
        mgr = ConversationManager(agent=agent)
        assert mgr.session_id is not None
        assert len(mgr.session_id) > 0

    @pytest.mark.asyncio
    async def test_turn_count_tracks_exchanges(self):
        agent = FakeAgent()
        mgr = ConversationManager(agent=agent)
        assert mgr.turn_count == 0
        await mgr.send("first")
        assert mgr.turn_count == 1
        await mgr.send("second")
        assert mgr.turn_count == 2

    @pytest.mark.asyncio
    async def test_broken_memory_does_not_crash(self):
        """ConversationManager should handle memory failures gracefully."""

        class BrokenMemory:
            async def retrieve(self, query, top_k=5):
                raise ConnectionError("Memory is down")

            async def store(self, content, metadata=None):
                raise ConnectionError("Memory is down")

        agent = FakeAgent()
        mgr = ConversationManager(agent=agent, memory=BrokenMemory())
        # Should not raise, should still get a response
        response = await mgr.send("hello despite broken memory")
        assert "hello despite broken memory" in response

    @pytest.mark.asyncio
    async def test_multiple_resets_stable(self):
        """Calling reset() multiple times should not corrupt state."""
        agent = FakeAgent()
        mgr = ConversationManager(agent=agent)
        await mgr.send("hello")
        assert mgr.turn_count == 1

        mgr.reset()
        assert mgr.turn_count == 0
        assert len(mgr.messages) == 1  # system prompt only

        mgr.reset()
        assert mgr.turn_count == 0
        assert len(mgr.messages) == 1

        response = await mgr.send("after resets")
        assert mgr.turn_count == 1
        assert "after resets" in response
