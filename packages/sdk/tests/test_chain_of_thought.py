# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for ChainOfThoughtStrategy."""

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.chain_of_thought import ChainOfThoughtStrategy
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec


class MockAgent(BaseAgent):
    """Test agent that returns predetermined responses."""

    def __init__(self, responses: list[ChatMessage], **kwargs):
        super().__init__(**kwargs)
        self._responses = list(responses)
        self._call_count = 0

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        self._last_messages = messages
        self._last_tools = tools
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


class TestChainOfThoughtInit:
    """Constructor defaults and configuration."""

    def test_defaults(self):
        s = ChainOfThoughtStrategy()
        assert "step by step" in s.cot_prompt.lower()
        assert s.include_tools is False

    def test_custom_prompt(self):
        s = ChainOfThoughtStrategy(cot_prompt="Reason carefully.")
        assert s.cot_prompt == "Reason carefully."

    def test_include_tools(self):
        s = ChainOfThoughtStrategy(include_tools=True)
        assert s.include_tools is True


@pytest.mark.asyncio
async def test_cot_single_call():
    """CoT makes exactly one LLM call and returns the response."""
    strategy = ChainOfThoughtStrategy()
    agent = MockAgent(
        responses=[ChatMessage.assistant("Step 1: ... Step 2: ... Answer: 42")],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("What is 6 * 7?")
    assert "42" in result
    assert agent._call_count == 1


@pytest.mark.asyncio
async def test_cot_prepends_system_prompt():
    """CoT prepends the reasoning instruction as a system message."""
    strategy = ChainOfThoughtStrategy(cot_prompt="Think carefully.")
    agent = MockAgent(
        responses=[ChatMessage.assistant("Done")],
        name="test",
        model="mock",
        strategy=strategy,
    )
    await agent.chat("Test")
    # The strategy prepends a system message with the CoT prompt
    # Check that the messages passed to _call_llm include it
    cot_msgs = [m for m in agent._last_messages if m.role.value == "system"]
    assert any("Think carefully" in (m.content or "") for m in cot_msgs)


@pytest.mark.asyncio
async def test_cot_no_tools_by_default():
    """CoT does not pass tools to the LLM by default."""
    tool = ToolSpec(
        name="search",
        description="Search",
        parameters={"type": "object", "properties": {}},
    )
    strategy = ChainOfThoughtStrategy()
    agent = MockAgent(
        responses=[ChatMessage.assistant("Answer")],
        name="test",
        model="mock",
        strategy=strategy,
        tools=[tool],
    )
    await agent.chat("Test")
    assert agent._last_tools == []


@pytest.mark.asyncio
async def test_cot_with_tools():
    """CoT passes tools when include_tools=True."""
    tool = ToolSpec(
        name="calc",
        description="Calculator",
        parameters={"type": "object", "properties": {}},
    )
    strategy = ChainOfThoughtStrategy(include_tools=True)
    agent = MockAgent(
        responses=[ChatMessage.assistant("Result")],
        name="test",
        model="mock",
        strategy=strategy,
        tools=[tool],
    )
    await agent.chat("Compute something")
    assert len(agent._last_tools) == 1
    assert agent._last_tools[0].name == "calc"
