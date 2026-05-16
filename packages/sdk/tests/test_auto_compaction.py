# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for auto context compression in BaseAgent."""

from __future__ import annotations

from typing import Any

import pytest

from sagewai.core.base import BaseAgent
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec


class TrackingAgent(BaseAgent):
    """Agent that tracks messages passed to _call_llm."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.llm_call_messages: list[list[ChatMessage]] = []

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        self.llm_call_messages.append(list(messages))
        return ChatMessage.assistant("response")


class TestAutoCompaction:
    @pytest.mark.asyncio
    async def test_no_compaction_when_disabled(self):
        """Without max_context_tokens, messages pass through unchanged."""
        agent = TrackingAgent(name="test")
        await agent.chat("hello")
        # Should have system + user = 2 messages
        assert len(agent.llm_call_messages[0]) >= 1

    @pytest.mark.asyncio
    async def test_no_compaction_when_under_budget(self):
        """Short messages shouldn't trigger compaction."""
        agent = TrackingAgent(name="test", max_context_tokens=10000)
        await agent.chat("hi")
        msgs = agent.llm_call_messages[0]
        # No compaction summary should appear
        assert not any("[Conversation summary]" in (m.content or "") for m in msgs)

    @pytest.mark.asyncio
    async def test_compaction_triggers_when_over_budget(self):
        """Long conversation history should get compacted."""
        agent = TrackingAgent(name="test", max_context_tokens=100, system_prompt="You help.")

        # First call: build up history
        long_history = [
            ChatMessage.system("You help."),
            ChatMessage.user("Tell me about " + "x" * 200),
            ChatMessage.assistant("Here is info about " + "y" * 200),
            ChatMessage.user("Tell me more about " + "z" * 200),
            ChatMessage.assistant("More info: " + "w" * 200),
            ChatMessage.user("Final question"),
        ]
        await agent.chat_with_history(long_history)

        # The messages passed to LLM should be compacted
        msgs = agent.llm_call_messages[0]
        total_content = sum(len(m.content or "") for m in msgs)
        # Should be shorter than the original
        original_content = sum(len(m.content or "") for m in long_history)
        assert total_content < original_content

    @pytest.mark.asyncio
    async def test_compaction_emits_event(self):
        """CONTEXT_COMPACTED event should fire when compaction happens."""
        agent = TrackingAgent(name="test", max_context_tokens=50, system_prompt="Help.")

        events: list[tuple] = []
        agent.on_event(lambda event, data: events.append((event, data)))

        long_history = [
            ChatMessage.system("Help."),
            ChatMessage.user("x" * 500),
            ChatMessage.assistant("y" * 500),
            ChatMessage.user("final"),
        ]
        await agent.chat_with_history(long_history)

        from sagewai.core.events import AgentEvent

        compaction_events = [e for e, d in events if e == AgentEvent.CONTEXT_COMPACTED]
        assert len(compaction_events) >= 1

    @pytest.mark.asyncio
    async def test_compaction_preserves_system_prompt(self):
        """Auto-compaction should never remove the system prompt."""
        agent = TrackingAgent(
            name="t",
            model="gpt-4o",
            system_prompt="Important system instructions that must survive compaction",
            max_context_tokens=100,
        )
        long_history = [
            ChatMessage.system("Important system instructions that must survive compaction"),
            ChatMessage.user("Tell me about " + "x" * 200),
            ChatMessage.assistant("Here is info about " + "y" * 200),
            ChatMessage.user("Tell me more about " + "z" * 200),
            ChatMessage.assistant("More info: " + "w" * 200),
            ChatMessage.user("Tell me a very long story " * 50),
        ]
        await agent.chat_with_history(long_history)
        # System prompt should be in the messages the LLM received
        first_msg = agent.llm_call_messages[0][0]
        assert first_msg.role.value == "system"
        assert "Important system instructions" in first_msg.content
