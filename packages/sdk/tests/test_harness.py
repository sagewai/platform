# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the agent test harness (sagewai.testing)."""

import pytest

from sagewai.models.message import ChatMessage, ToolCall
from sagewai.models.tool import ToolSpec
from sagewai.testing import AgentTestHarness

# ------------------------------------------------------------------
# Basic usage
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_chat():
    """Harness returns mock text response."""
    harness = AgentTestHarness(
        responses=[ChatMessage.assistant("Hello there!")],
    )
    result = await harness.chat("Hi")
    assert result == "Hello there!"
    harness.assert_call_count(1)
    harness.assert_no_tool_calls()


@pytest.mark.asyncio
async def test_tool_call_flow():
    """Harness runs a full tool call flow."""
    call_log = []

    async def search(query: str) -> str:
        call_log.append(query)
        return f"Results for: {query}"

    tool = ToolSpec(
        name="search",
        description="Search for info",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=search,
    )

    harness = AgentTestHarness(
        responses=[
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="tc1", name="search", arguments={"query": "python"})]
            ),
            ChatMessage.assistant("Found Python info!"),
        ],
        tools=[tool],
    )

    result = await harness.chat("Search python")
    assert result == "Found Python info!"
    harness.assert_call_count(2)
    harness.assert_tool_called("search", times=1)
    harness.assert_tool_called_with("search", query="python")
    assert call_log == ["python"]


@pytest.mark.asyncio
async def test_multi_tool_calls():
    """Harness tracks multiple tool calls across iterations."""

    async def tool_a() -> str:
        return "a"

    async def tool_b() -> str:
        return "b"

    harness = AgentTestHarness(
        responses=[
            ChatMessage.assistant(tool_calls=[ToolCall(id="tc1", name="tool_a", arguments={})]),
            ChatMessage.assistant(tool_calls=[ToolCall(id="tc2", name="tool_b", arguments={})]),
            ChatMessage.assistant("Done with both!"),
        ],
        tools=[
            ToolSpec(name="tool_a", description="A", handler=tool_a),
            ToolSpec(name="tool_b", description="B", handler=tool_b),
        ],
    )

    result = await harness.chat("Use both tools")
    assert result == "Done with both!"
    harness.assert_call_count(3)
    harness.assert_tool_called("tool_a", times=1)
    harness.assert_tool_called("tool_b", times=1)


# ------------------------------------------------------------------
# Assertion helpers
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assert_no_tool_calls_fails():
    """assert_no_tool_calls raises when tools were called."""
    harness = AgentTestHarness(
        responses=[
            ChatMessage.assistant(tool_calls=[ToolCall(id="tc1", name="x", arguments={})]),
            ChatMessage.assistant("ok"),
        ],
        tools=[ToolSpec(name="x", description="x", handler=lambda: "y")],
    )
    await harness.chat("go")
    with pytest.raises(AssertionError, match="Expected no tool calls"):
        harness.assert_no_tool_calls()


@pytest.mark.asyncio
async def test_assert_tool_called_fails_for_uncalled():
    """assert_tool_called raises for tools that were never called."""
    harness = AgentTestHarness(
        responses=[ChatMessage.assistant("No tools needed")],
    )
    await harness.chat("Hello")
    with pytest.raises(AssertionError, match="Expected tool 'missing'"):
        harness.assert_tool_called("missing")


@pytest.mark.asyncio
async def test_assert_tool_called_with_wrong_args():
    """assert_tool_called_with raises when args don't match."""
    harness = AgentTestHarness(
        responses=[
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="tc1", name="search", arguments={"query": "actual"})]
            ),
            ChatMessage.assistant("done"),
        ],
        tools=[ToolSpec(name="search", description="s", handler=lambda query: query)],
    )
    await harness.chat("go")
    with pytest.raises(AssertionError, match="never with args"):
        harness.assert_tool_called_with("search", query="expected")


@pytest.mark.asyncio
async def test_assert_final_response_contains():
    """assert_final_response_contains checks substring presence."""
    harness = AgentTestHarness(
        responses=[ChatMessage.assistant("The answer is 42")],
    )
    await harness.chat("What's the answer?")
    harness.assert_final_response_contains("42")

    with pytest.raises(AssertionError, match="Expected final response to contain"):
        harness.assert_final_response_contains("99")


@pytest.mark.asyncio
async def test_assert_call_count_fails():
    """assert_call_count raises on mismatch."""
    harness = AgentTestHarness(
        responses=[ChatMessage.assistant("ok")],
    )
    await harness.chat("Hi")
    with pytest.raises(AssertionError, match="Expected 5 LLM calls"):
        harness.assert_call_count(5)


# ------------------------------------------------------------------
# Advanced usage
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_with_history():
    """Harness supports explicit message history."""
    harness = AgentTestHarness(
        responses=[ChatMessage.assistant("Continuing conversation")],
    )
    result = await harness.chat_with_history(
        [
            ChatMessage.system("Be helpful."),
            ChatMessage.user("Earlier message"),
            ChatMessage.assistant("Earlier response"),
            ChatMessage.user("New message"),
        ]
    )
    assert result.content == "Continuing conversation"
    harness.assert_call_count(1)


@pytest.mark.asyncio
async def test_messages_sent_inspection():
    """Harness exposes the messages sent to _call_llm for inspection."""
    harness = AgentTestHarness(
        responses=[ChatMessage.assistant("Hi!")],
        system_prompt="You are helpful.",
    )
    await harness.chat("Hello")

    sent = harness.messages_sent
    assert len(sent) == 1
    # First call should have system + user
    assert len(sent[0]) == 2
    assert sent[0][0].content == "You are helpful."
    assert sent[0][1].content == "Hello"


@pytest.mark.asyncio
async def test_exhausted_responses_raises():
    """MockAgent raises IndexError when responses are exhausted."""
    harness = AgentTestHarness(
        responses=[ChatMessage.assistant("Only one response")],
    )
    await harness.chat("First")
    with pytest.raises(IndexError, match="exhausted all 1 responses"):
        await harness.chat("Second")
