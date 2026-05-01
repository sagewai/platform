# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for MajorityVoteStrategy."""

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.majority_vote import MajorityVoteStrategy
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


class TestMajorityVoteInit:
    """Constructor defaults and clamping."""

    def test_defaults(self):
        s = MajorityVoteStrategy()
        assert s.n_samples == 3
        assert s.aggregation == "llm"

    def test_min_samples(self):
        s = MajorityVoteStrategy(n_samples=1)
        assert s.n_samples == 2  # clamped to minimum 2

    def test_first_aggregation(self):
        s = MajorityVoteStrategy(aggregation="first")
        assert s.aggregation == "first"


@pytest.mark.asyncio
async def test_majority_vote_all_agree():
    """When all samples agree, LLM aggregation returns the consensus."""
    strategy = MajorityVoteStrategy(n_samples=3)
    agent = MockAgent(
        responses=[
            # 3 samples (generated in parallel, consumed sequentially by mock)
            ChatMessage.assistant("42"),
            ChatMessage.assistant("42"),
            ChatMessage.assistant("42"),
            # Aggregation LLM call
            ChatMessage.assistant("42"),
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("What is 6*7?")
    assert "42" in result
    assert agent._call_count == 4  # 3 samples + 1 aggregation


@pytest.mark.asyncio
async def test_majority_vote_mixed_answers():
    """LLM aggregation picks the most common answer from mixed responses."""
    strategy = MajorityVoteStrategy(n_samples=3)
    agent = MockAgent(
        responses=[
            ChatMessage.assistant("Paris"),
            ChatMessage.assistant("London"),
            ChatMessage.assistant("Paris"),
            # Aggregation picks Paris (majority)
            ChatMessage.assistant("Paris"),
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Capital of France?")
    assert "Paris" in result


@pytest.mark.asyncio
async def test_majority_vote_first_mode():
    """Aggregation='first' returns the first response without extra LLM call."""
    strategy = MajorityVoteStrategy(n_samples=2, aggregation="first")
    agent = MockAgent(
        responses=[
            ChatMessage.assistant("Answer A"),
            ChatMessage.assistant("Answer B"),
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Question")
    assert result == "Answer A"
    assert agent._call_count == 2  # just the 2 samples, no aggregation


@pytest.mark.asyncio
async def test_majority_vote_two_samples():
    """Minimum n_samples=2 works correctly."""
    strategy = MajorityVoteStrategy(n_samples=2)
    agent = MockAgent(
        responses=[
            ChatMessage.assistant("Yes"),
            ChatMessage.assistant("Yes"),
            ChatMessage.assistant("Yes"),  # aggregation
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Is the sky blue?")
    assert result == "Yes"
