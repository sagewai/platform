# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for BaseAgent event lifecycle hooks."""

from __future__ import annotations

from typing import Any

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage, ToolCall
from sagewai.models.tool import ToolSpec


class MockAgent(BaseAgent):
    """Agent that returns predetermined responses."""

    def __init__(self, responses: list[ChatMessage], **kwargs: Any):
        super().__init__(**kwargs)
        self._responses = list(responses)
        self._call_count = 0

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


# ------------------------------------------------------------------
# Event type enum
# ------------------------------------------------------------------


def test_agent_event_values():
    """AgentEvent has all expected lifecycle events."""
    assert AgentEvent.RUN_STARTED.value == "run_started"
    assert AgentEvent.RUN_FINISHED.value == "run_finished"
    assert AgentEvent.RUN_ERROR.value == "run_error"
    assert AgentEvent.STEP_STARTED.value == "step_started"
    assert AgentEvent.TOOL_CALL_START.value == "tool_call_start"
    assert AgentEvent.TEXT_MESSAGE_CONTENT.value == "text_message_content"
    assert AgentEvent.LLM_CALL_FINISHED.value == "llm_call_finished"


def test_agent_event_count():
    assert len(AgentEvent) == 40


# ------------------------------------------------------------------
# Hook registration
# ------------------------------------------------------------------


def test_on_event_registers_listener():
    agent = MockAgent(responses=[], name="test", model="mock")
    assert len(agent._event_listeners) == 0

    agent.on_event(lambda e, d: None)
    assert len(agent._event_listeners) == 1


def test_multiple_listeners():
    agent = MockAgent(responses=[], name="test", model="mock")
    agent.on_event(lambda e, d: None)
    agent.on_event(lambda e, d: None)
    assert len(agent._event_listeners) == 2


# ------------------------------------------------------------------
# chat() emits RUN_STARTED + RUN_FINISHED
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_emits_run_lifecycle():
    """chat() emits RUN_STARTED and RUN_FINISHED events."""
    events: list[tuple[AgentEvent, dict]] = []

    agent = MockAgent(
        responses=[ChatMessage.assistant("Hello!")],
        name="test",
        model="mock",
    )
    agent.on_event(lambda e, d: events.append((e, d)))

    result = await agent.chat("Hi")
    assert result == "Hello!"

    event_types = [e for e, _ in events]
    assert AgentEvent.RUN_STARTED in event_types
    assert AgentEvent.RUN_FINISHED in event_types

    # RUN_STARTED is first, RUN_FINISHED is last
    assert event_types[0] == AgentEvent.RUN_STARTED
    assert event_types[-1] == AgentEvent.RUN_FINISHED

    # Check payload
    start_data = events[0][1]
    assert start_data["agent"] == "test"
    assert start_data["input"] == "Hi"


@pytest.mark.asyncio
async def test_chat_emits_run_error_on_exception():
    """chat() emits RUN_ERROR when the agent loop raises."""
    events: list[tuple[AgentEvent, dict]] = []

    class FailingAgent(BaseAgent):
        async def _invoke_llm(self, messages, tools, *, model_override=None):
            raise RuntimeError("LLM is down")

    agent = FailingAgent(name="fail", model="mock")
    agent.on_event(lambda e, d: events.append((e, d)))

    with pytest.raises(RuntimeError, match="LLM is down"):
        await agent.chat("Hi")

    event_types = [e for e, _ in events]
    assert AgentEvent.RUN_STARTED in event_types
    assert AgentEvent.RUN_ERROR in event_types
    assert AgentEvent.RUN_FINISHED not in event_types

    error_data = next(d for e, d in events if e == AgentEvent.RUN_ERROR)
    assert "LLM is down" in error_data["error"]


# ------------------------------------------------------------------
# chat_with_history() emits run events
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_with_history_emits_run_lifecycle():
    events: list[tuple[AgentEvent, dict]] = []

    agent = MockAgent(
        responses=[ChatMessage.assistant("OK")],
        name="history-test",
        model="mock",
    )
    agent.on_event(lambda e, d: events.append((e, d)))

    result = await agent.chat_with_history([ChatMessage.user("Hello")])
    assert result.content == "OK"

    event_types = [e for e, _ in events]
    assert event_types[0] == AgentEvent.RUN_STARTED
    assert event_types[-1] == AgentEvent.RUN_FINISHED


# ------------------------------------------------------------------
# Strategy emits STEP + TOOL events
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strategy_emits_step_events():
    """ReActStrategy emits STEP_STARTED and STEP_FINISHED per iteration."""
    events: list[tuple[AgentEvent, dict]] = []

    agent = MockAgent(
        responses=[ChatMessage.assistant("Done")],
        name="step-test",
        model="mock",
    )
    agent.on_event(lambda e, d: events.append((e, d)))

    await agent.chat("Hi")

    event_types = [e for e, _ in events]
    assert AgentEvent.STEP_STARTED in event_types
    assert AgentEvent.STEP_FINISHED in event_types


@pytest.mark.asyncio
async def test_strategy_emits_tool_events():
    """ReActStrategy emits tool events when agent uses tools."""
    events: list[tuple[AgentEvent, dict]] = []

    async def mock_tool(query: str) -> str:
        return "result"

    tool_spec = ToolSpec(
        name="search",
        description="Search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=mock_tool,
    )

    agent = MockAgent(
        responses=[
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="tc1", name="search", arguments={"query": "test"})]
            ),
            ChatMessage.assistant("Found it"),
        ],
        name="tool-test",
        model="mock",
        tools=[tool_spec],
    )
    agent.on_event(lambda e, d: events.append((e, d)))

    await agent.chat("Search for test")

    event_types = [e for e, _ in events]
    assert AgentEvent.TOOL_CALL_START in event_types
    assert AgentEvent.TOOL_CALL_END in event_types
    assert AgentEvent.TOOL_CALL_RESULT in event_types

    # Check tool call payload
    start_data = next(d for e, d in events if e == AgentEvent.TOOL_CALL_START)
    assert start_data["tool_call_id"] == "tc1"
    assert start_data["tool_name"] == "search"
    assert start_data["arguments"] == {"query": "test"}

    result_data = next(d for e, d in events if e == AgentEvent.TOOL_CALL_RESULT)
    assert result_data["tool_name"] == "search"
    assert result_data["content"] == "result"


@pytest.mark.asyncio
async def test_strategy_emits_text_content():
    """ReActStrategy emits TEXT_MESSAGE_CONTENT for text responses."""
    events: list[tuple[AgentEvent, dict]] = []

    agent = MockAgent(
        responses=[ChatMessage.assistant("Hello world")],
        name="text-test",
        model="mock",
    )
    agent.on_event(lambda e, d: events.append((e, d)))

    await agent.chat("Hi")

    event_types = [e for e, _ in events]
    assert AgentEvent.TEXT_MESSAGE_CONTENT in event_types

    text_data = next(d for e, d in events if e == AgentEvent.TEXT_MESSAGE_CONTENT)
    assert text_data["delta"] == "Hello world"


# ------------------------------------------------------------------
# Streaming emits events
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_emits_text_events():
    """chat_stream() emits text message lifecycle events."""
    events: list[tuple[AgentEvent, dict]] = []

    agent = MockAgent(
        responses=[ChatMessage.assistant("Hello!")],
        name="stream-test",
        model="mock",
    )
    agent.on_event(lambda e, d: events.append((e, d)))

    chunks = []
    async for chunk in agent.chat_stream("Hi"):
        chunks.append(chunk)

    assert "".join(chunks) == "Hello!"

    event_types = [e for e, _ in events]
    assert AgentEvent.RUN_STARTED in event_types
    assert AgentEvent.STEP_STARTED in event_types
    assert AgentEvent.TEXT_MESSAGE_START in event_types
    assert AgentEvent.TEXT_MESSAGE_CONTENT in event_types
    assert AgentEvent.TEXT_MESSAGE_END in event_types
    assert AgentEvent.STEP_FINISHED in event_types
    assert AgentEvent.RUN_FINISHED in event_types


@pytest.mark.asyncio
async def test_stream_emits_tool_events():
    """chat_stream() emits tool events during tool execution."""
    events: list[tuple[AgentEvent, dict]] = []

    async def mock_tool(query: str) -> str:
        return "found"

    tool_spec = ToolSpec(
        name="search",
        description="Search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=mock_tool,
    )

    agent = MockAgent(
        responses=[
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="tc1", name="search", arguments={"query": "x"})]
            ),
            ChatMessage.assistant("Done"),
        ],
        name="stream-tool-test",
        model="mock",
        tools=[tool_spec],
    )
    agent.on_event(lambda e, d: events.append((e, d)))

    chunks = []
    async for chunk in agent.chat_stream("Search"):
        chunks.append(chunk)

    event_types = [e for e, _ in events]
    assert AgentEvent.TOOL_CALL_START in event_types
    assert AgentEvent.TOOL_CALL_END in event_types
    assert AgentEvent.TOOL_CALL_RESULT in event_types


# ------------------------------------------------------------------
# Async listener support
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_listener():
    """Async callbacks are awaited properly."""
    events: list[AgentEvent] = []

    async def async_listener(event: AgentEvent, data: dict) -> None:
        events.append(event)

    agent = MockAgent(
        responses=[ChatMessage.assistant("OK")],
        name="async-test",
        model="mock",
    )
    agent.on_event(async_listener)

    await agent.chat("Hi")
    assert AgentEvent.RUN_STARTED in events
    assert AgentEvent.RUN_FINISHED in events


# ------------------------------------------------------------------
# Error resilience
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failing_listener_does_not_break_agent():
    """A listener that raises does not prevent the agent from running."""
    good_events: list[AgentEvent] = []

    def failing_listener(event: AgentEvent, data: dict) -> None:
        raise ValueError("Listener crash")

    def good_listener(event: AgentEvent, data: dict) -> None:
        good_events.append(event)

    agent = MockAgent(
        responses=[ChatMessage.assistant("OK")],
        name="resilience-test",
        model="mock",
    )
    agent.on_event(failing_listener)
    agent.on_event(good_listener)

    result = await agent.chat("Hi")
    assert result == "OK"
    assert AgentEvent.RUN_STARTED in good_events


# ------------------------------------------------------------------
# No listeners = zero overhead
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_listeners_no_overhead():
    """Agent works fine with no listeners registered."""
    agent = MockAgent(
        responses=[ChatMessage.assistant("OK")],
        name="no-listeners",
        model="mock",
    )
    # No on_event() call
    result = await agent.chat("Hi")
    assert result == "OK"
