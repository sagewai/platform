# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for memory integration with BaseAgent."""

from __future__ import annotations

from typing import Any

import pytest

from sagewai.core.base import BaseAgent
from sagewai.memory import MemoryProvider
from sagewai.models.message import ChatMessage, Role
from sagewai.models.tool import ToolSpec

# ------------------------------------------------------------------
# Mock implementations
# ------------------------------------------------------------------


class MockMemory:
    """In-memory MemoryProvider for testing."""

    def __init__(self, items: list[str] | None = None) -> None:
        self.items = items or []
        self.stored: list[tuple[str, dict[str, Any] | None]] = []
        self.retrieve_calls: list[str] = []

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        self.retrieve_calls.append(query)
        return self.items[:top_k]

    async def store(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        self.stored.append((content, metadata))


class CapturingAgent(BaseAgent):
    """Agent that captures messages sent to LLM."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.captured_messages: list[list[ChatMessage]] = []

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        self.captured_messages.append(list(messages))
        return ChatMessage.assistant("OK")


# ------------------------------------------------------------------
# MemoryProvider protocol
# ------------------------------------------------------------------


def test_mock_memory_is_provider():
    """MockMemory satisfies the MemoryProvider protocol."""
    mem = MockMemory()
    assert isinstance(mem, MemoryProvider)


# ------------------------------------------------------------------
# chat() with memory
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_injects_memory_context():
    """Memory context is injected into messages before LLM call."""
    memory = MockMemory(items=["Paris is the capital of France", "France is in Europe"])
    agent = CapturingAgent(
        name="mem-test",
        model="mock",
        system_prompt="You are helpful.",
        memory=memory,
    )

    await agent.chat("Tell me about France")

    # Memory was queried with the user message
    assert memory.retrieve_calls == ["Tell me about France"]

    # Messages sent to LLM should include memory context
    msgs = agent.captured_messages[0]
    assert len(msgs) == 3  # system + memory context + user

    # First is original system prompt
    assert msgs[0].role == Role.system
    assert msgs[0].content == "You are helpful."

    # Second is memory context (inserted after system, before user)
    assert msgs[1].role == Role.system
    assert "[Relevant context from memory]" in msgs[1].content
    assert "Paris is the capital of France" in msgs[1].content
    assert "France is in Europe" in msgs[1].content

    # Third is the user message
    assert msgs[2].role == Role.user
    assert msgs[2].content == "Tell me about France"


@pytest.mark.asyncio
async def test_chat_without_memory():
    """When no memory is configured, messages are unchanged."""
    agent = CapturingAgent(
        name="no-mem",
        model="mock",
        system_prompt="You are helpful.",
    )

    await agent.chat("Hello")

    msgs = agent.captured_messages[0]
    assert len(msgs) == 2  # system + user only
    assert msgs[0].role == Role.system
    assert msgs[1].role == Role.user


@pytest.mark.asyncio
async def test_chat_with_empty_memory():
    """When memory returns no results, no context is injected."""
    memory = MockMemory(items=[])
    agent = CapturingAgent(
        name="empty-mem",
        model="mock",
        system_prompt="You are helpful.",
        memory=memory,
    )

    await agent.chat("Hello")

    msgs = agent.captured_messages[0]
    assert len(msgs) == 2  # system + user (no memory context)


@pytest.mark.asyncio
async def test_chat_no_system_prompt_with_memory():
    """Memory works even without a system prompt."""
    memory = MockMemory(items=["Some context"])
    agent = CapturingAgent(
        name="no-sys",
        model="mock",
        memory=memory,
    )

    await agent.chat("Question")

    msgs = agent.captured_messages[0]
    assert len(msgs) == 2  # memory context + user
    assert msgs[0].role == Role.system
    assert "[Relevant context from memory]" in msgs[0].content
    assert msgs[1].role == Role.user


# ------------------------------------------------------------------
# chat_with_history() with memory
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_with_history_injects_memory():
    """chat_with_history also injects memory context."""
    memory = MockMemory(items=["Historical context"])
    agent = CapturingAgent(
        name="history-mem",
        model="mock",
        memory=memory,
    )

    messages = [
        ChatMessage.system("You are a historian."),
        ChatMessage.user("Previous question"),
        ChatMessage.assistant("Previous answer"),
        ChatMessage.user("New question about history"),
    ]

    await agent.chat_with_history(messages)

    # Memory queried with last user message
    assert memory.retrieve_calls == ["New question about history"]

    msgs = agent.captured_messages[0]
    # system + memory context + user + assistant + user
    assert any("[Relevant context from memory]" in (m.content or "") for m in msgs)


# ------------------------------------------------------------------
# chat_stream() with memory
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_stream_injects_memory():
    """chat_stream also injects memory context."""
    memory = MockMemory(items=["Streaming context"])
    agent = CapturingAgent(
        name="stream-mem",
        model="mock",
        memory=memory,
    )

    chunks = []
    async for chunk in agent.chat_stream("Question"):
        chunks.append(chunk)

    assert memory.retrieve_calls == ["Question"]
    msgs = agent.captured_messages[0]
    assert any("[Relevant context from memory]" in (m.content or "") for m in msgs)


# ------------------------------------------------------------------
# Memory error resilience
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_error_does_not_break_agent():
    """If memory retrieval fails, the agent still works."""

    class FailingMemory:
        async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
            raise ConnectionError("Memory DB unavailable")

        async def store(self, content: str, metadata: dict[str, Any] | None = None) -> None:
            pass

    agent = CapturingAgent(
        name="fail-mem",
        model="mock",
        system_prompt="Be helpful.",
        memory=FailingMemory(),
    )

    result = await agent.chat("Hello")
    assert result == "OK"

    # Messages should still be valid (system + user, no memory)
    msgs = agent.captured_messages[0]
    assert len(msgs) == 2


# ------------------------------------------------------------------
# Memory store (for future use)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_store():
    """MockMemory store works for future auto-store functionality."""
    memory = MockMemory()
    await memory.store("Important info", {"source": "agent"})
    assert len(memory.stored) == 1
    assert memory.stored[0] == ("Important info", {"source": "agent"})


# ------------------------------------------------------------------
# Top-k parameter
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_respects_top_k():
    """Memory retrieve returns at most top_k items."""
    memory = MockMemory(items=["a", "b", "c", "d", "e", "f"])
    results = await memory.retrieve("query", top_k=3)
    assert len(results) == 3
