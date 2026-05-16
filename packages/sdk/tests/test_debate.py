# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for DebateStrategy."""

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.debate import DebateStrategy
from sagewai.models.message import ChatMessage


class MockAgent(BaseAgent):
    """Test agent with predetermined response sequence."""

    def __init__(self, responses: list[ChatMessage], **kwargs):
        super().__init__(**kwargs)
        self._responses = list(responses)
        self._call_count = 0

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


class TestDebateInit:
    """Constructor defaults and clamping."""

    def test_defaults(self):
        s = DebateStrategy()
        assert s.n_debaters == 3
        assert s.max_rounds == 2

    def test_min_debaters(self):
        s = DebateStrategy(n_debaters=1)
        assert s.n_debaters == 2  # clamped

    def test_min_rounds(self):
        s = DebateStrategy(max_rounds=0)
        assert s.max_rounds == 1  # clamped


@pytest.mark.asyncio
async def test_debate_single_round():
    """Single round: 2 debaters + judge = 3 LLM calls."""
    strategy = DebateStrategy(n_debaters=2, max_rounds=1)
    agent = MockAgent(
        responses=[
            # Round 0: debater 1
            ChatMessage.assistant("Argument A"),
            # Round 0: debater 2
            ChatMessage.assistant("Argument B"),
            # Judge
            ChatMessage.assistant("After considering both perspectives, A is stronger."),
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Analyze this topic")
    assert "A is stronger" in result
    assert agent._call_count == 3  # 2 debaters + 1 judge


@pytest.mark.asyncio
async def test_debate_two_rounds():
    """Two rounds: debaters refine arguments before judging."""
    strategy = DebateStrategy(n_debaters=2, max_rounds=2)
    agent = MockAgent(
        responses=[
            # Round 0: debater 1 & 2
            ChatMessage.assistant("Initial position A"),
            ChatMessage.assistant("Initial position B"),
            # Round 1: debater 1 & 2 (with context from round 0)
            ChatMessage.assistant("Refined position A"),
            ChatMessage.assistant("Refined position B"),
            # Judge
            ChatMessage.assistant("Synthesis of refined positions"),
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Topic")
    assert result == "Synthesis of refined positions"
    assert agent._call_count == 5  # 2*2 debaters + 1 judge


@pytest.mark.asyncio
async def test_debate_three_debaters():
    """Three debaters in one round + judge = 4 calls."""
    strategy = DebateStrategy(n_debaters=3, max_rounds=1)
    agent = MockAgent(
        responses=[
            ChatMessage.assistant("View 1"),
            ChatMessage.assistant("View 2"),
            ChatMessage.assistant("View 3"),
            ChatMessage.assistant("Best view is 2"),
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Question")
    assert "Best view" in result
    assert agent._call_count == 4


@pytest.mark.asyncio
async def test_debate_total_calls():
    """Verify total LLM call count: n_debaters * max_rounds + 1 judge."""
    n, r = 3, 2
    strategy = DebateStrategy(n_debaters=n, max_rounds=r)
    total_expected = n * r + 1  # 7
    agent = MockAgent(
        responses=[ChatMessage.assistant(f"resp_{i}") for i in range(total_expected)],
        name="test",
        model="mock",
        strategy=strategy,
    )
    await agent.chat("Topic")
    assert agent._call_count == total_expected
