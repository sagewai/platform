# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for execution strategies."""

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.strategies import ExecutionStrategy, ReActStrategy
from sagewai.models.message import ChatMessage, ToolCall
from sagewai.models.tool import ToolSpec


class MockAgent(BaseAgent):
    """Test agent that returns predetermined responses."""

    def __init__(self, responses: list[ChatMessage], **kwargs):
        super().__init__(**kwargs)
        self._responses = list(responses)
        self._call_count = 0

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


# ------------------------------------------------------------------
# ReActStrategy tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_react_simple_text():
    """ReActStrategy returns text response immediately."""
    strategy = ReActStrategy()
    agent = MockAgent(
        responses=[ChatMessage.assistant("Hello!")],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Hi")
    assert result == "Hello!"


@pytest.mark.asyncio
async def test_react_tool_then_text():
    """ReActStrategy executes tools and continues the loop."""
    call_log = []

    async def mock_search(query: str) -> str:
        call_log.append(query)
        return "found it"

    tool_spec = ToolSpec(
        name="search",
        description="Search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=mock_search,
    )

    agent = MockAgent(
        responses=[
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="tc1", name="search", arguments={"query": "test"})]
            ),
            ChatMessage.assistant("Result: found it"),
        ],
        name="test",
        model="mock",
        tools=[tool_spec],
        strategy=ReActStrategy(),
    )

    result = await agent.chat("Search for test")
    assert result == "Result: found it"
    assert call_log == ["test"]
    assert agent._call_count == 2


@pytest.mark.asyncio
async def test_react_max_iterations():
    """ReActStrategy respects max_iterations guard."""

    async def loop_tool() -> str:
        return "looping"

    tool_spec = ToolSpec(name="loop", description="Loop", handler=loop_tool)

    responses = [
        ChatMessage.assistant(tool_calls=[ToolCall(id=f"tc{i}", name="loop", arguments={})])
        for i in range(20)
    ]

    agent = MockAgent(
        responses=responses,
        name="looper",
        model="mock",
        tools=[tool_spec],
        max_iterations=3,
        strategy=ReActStrategy(),
    )

    result = await agent.chat("Loop")
    assert "maximum iterations" in result.lower()
    assert agent._call_count == 3


# ------------------------------------------------------------------
# ExecutionStrategy protocol tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_react_satisfies_protocol():
    """ReActStrategy is a valid ExecutionStrategy."""
    assert isinstance(ReActStrategy(), ExecutionStrategy)


@pytest.mark.asyncio
async def test_custom_strategy():
    """A custom strategy can be injected via the strategy parameter."""

    class AlwaysGreetStrategy:
        """Strategy that ignores the conversation and always returns a greeting."""

        async def execute(self, agent, messages, tools, max_iterations):
            return ChatMessage.assistant("Custom strategy says hello!")

    agent = MockAgent(
        responses=[],  # Strategy never calls LLM
        name="custom",
        model="mock",
        strategy=AlwaysGreetStrategy(),
    )

    result = await agent.chat("Anything")
    assert result == "Custom strategy says hello!"
    assert agent._call_count == 0  # LLM never called


@pytest.mark.asyncio
async def test_custom_strategy_with_llm_access():
    """Custom strategy can call agent._call_llm() and agent._execute_tool()."""

    call_log = []

    async def mock_tool(x: str) -> str:
        call_log.append(x)
        return f"processed {x}"

    tool_spec = ToolSpec(
        name="process",
        description="Process",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        },
        handler=mock_tool,
    )

    class SingleShotStrategy:
        """Call LLM once, execute any tools, return a summary."""

        async def execute(self, agent, messages, tools, max_iterations):
            response = await agent._call_llm(messages, tools)
            if response.tool_calls:
                for tc in response.tool_calls:
                    await agent._execute_tool(tc)
            return ChatMessage.assistant("Single-shot done")

    agent = MockAgent(
        responses=[
            ChatMessage.assistant(
                tool_calls=[ToolCall(id="tc1", name="process", arguments={"x": "data"})]
            ),
        ],
        name="test",
        model="mock",
        tools=[tool_spec],
        strategy=SingleShotStrategy(),
    )

    result = await agent.chat("Go")
    assert result == "Single-shot done"
    assert call_log == ["data"]
    assert agent._call_count == 1


@pytest.mark.asyncio
async def test_default_strategy_is_react():
    """When no strategy is provided, BaseAgent uses ReActStrategy."""
    agent = MockAgent(
        responses=[ChatMessage.assistant("OK")],
        name="test",
        model="mock",
    )
    assert isinstance(agent.config.strategy, ReActStrategy)
    result = await agent.chat("Hi")
    assert result == "OK"
