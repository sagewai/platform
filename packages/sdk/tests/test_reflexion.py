# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for ReflexionStrategy."""

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.reflexion import ReflexionStrategy
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


class TestReflexionInit:
    """Constructor defaults and clamping."""

    def test_defaults(self):
        s = ReflexionStrategy()
        assert s.max_attempts == 3
        assert s.score_threshold == 7.0

    def test_clamping(self):
        s = ReflexionStrategy(max_attempts=0, score_threshold=15.0)
        assert s.max_attempts == 1
        assert s.score_threshold == 10.0

    def test_custom_threshold(self):
        s = ReflexionStrategy(score_threshold=5.0)
        assert s.score_threshold == 5.0


@pytest.mark.asyncio
async def test_reflexion_immediate_accept():
    """If the first attempt scores above threshold, return immediately."""
    strategy = ReflexionStrategy(score_threshold=7.0)
    agent = MockAgent(
        responses=[
            # Inner ReAct: text response (no tools)
            ChatMessage.assistant("Great answer"),
            # Evaluation: high score
            ChatMessage.assistant("9"),
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Question")
    assert result == "Great answer"
    assert agent._call_count == 2  # 1 generate + 1 evaluate


@pytest.mark.asyncio
async def test_reflexion_retry_then_accept():
    """Low score triggers reflection, second attempt succeeds."""
    strategy = ReflexionStrategy(max_attempts=2, score_threshold=7.0)
    agent = MockAgent(
        responses=[
            # Attempt 1: generate
            ChatMessage.assistant("Bad answer"),
            # Attempt 1: evaluate (low score)
            ChatMessage.assistant("3"),
            # Attempt 1: reflect
            ChatMessage.assistant("The answer was incomplete. Add more detail."),
            # Attempt 2: generate (improved)
            ChatMessage.assistant("Better answer with detail"),
            # Attempt 2: evaluate (high score)
            ChatMessage.assistant("8"),
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Question")
    assert result == "Better answer with detail"
    assert agent._call_count == 5


@pytest.mark.asyncio
async def test_reflexion_exhausted_returns_best():
    """When all attempts fail threshold, return the best-scoring response."""
    strategy = ReflexionStrategy(max_attempts=2, score_threshold=9.0)
    agent = MockAgent(
        responses=[
            # Attempt 1: generate
            ChatMessage.assistant("Okay answer"),
            # Attempt 1: evaluate
            ChatMessage.assistant("5"),
            # Attempt 1: reflect
            ChatMessage.assistant("Needs improvement"),
            # Attempt 2: generate
            ChatMessage.assistant("Better answer"),
            # Attempt 2: evaluate
            ChatMessage.assistant("6"),
            # No reflect on last attempt
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Question")
    # Should return "Better answer" since it scored 6 > 5
    assert result == "Better answer"


@pytest.mark.asyncio
async def test_reflexion_single_attempt():
    """With max_attempts=1, no reflection occurs."""
    strategy = ReflexionStrategy(max_attempts=1, score_threshold=9.0)
    agent = MockAgent(
        responses=[
            ChatMessage.assistant("Only attempt"),
            ChatMessage.assistant("4"),
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Question")
    assert result == "Only attempt"
    assert agent._call_count == 2  # 1 generate + 1 evaluate, no reflect
