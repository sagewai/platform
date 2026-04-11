"""Tests for EvaluatorOptimizerStrategy."""

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.evaluator_optimizer import EvaluatorOptimizerStrategy
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


class TestEvaluatorOptimizerInit:
    """Constructor defaults and clamping."""

    def test_defaults(self):
        s = EvaluatorOptimizerStrategy()
        assert s.max_revisions == 3
        assert s.approve_threshold == 8.0

    def test_clamping(self):
        s = EvaluatorOptimizerStrategy(max_revisions=0, approve_threshold=12.0)
        assert s.max_revisions == 1
        assert s.approve_threshold == 10.0


class TestScoreFeedbackParsing:
    """Test _extract_score and _extract_feedback."""

    def test_extract_score_standard(self):
        assert EvaluatorOptimizerStrategy._extract_score("Score: 8\nFeedback: Good") == 8.0

    def test_extract_score_fallback(self):
        assert EvaluatorOptimizerStrategy._extract_score("7") == 7.0

    def test_extract_feedback_standard(self):
        fb = EvaluatorOptimizerStrategy._extract_feedback(
            "Score: 5\nFeedback: Needs more detail"
        )
        assert "Needs more detail" in fb

    def test_extract_feedback_multiline(self):
        fb = EvaluatorOptimizerStrategy._extract_feedback(
            "Score: 4\nFeedback: Line 1\nLine 2\nLine 3"
        )
        assert "Line 1" in fb
        assert "Line 3" in fb


@pytest.mark.asyncio
async def test_eo_immediate_approve():
    """First response scores above threshold — no revision needed."""
    strategy = EvaluatorOptimizerStrategy(approve_threshold=7.0)
    agent = MockAgent(
        responses=[
            # Generate
            ChatMessage.assistant("Great response"),
            # Evaluate: high score
            ChatMessage.assistant("Score: 9\nFeedback: Excellent work"),
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Write something")
    assert result == "Great response"
    assert agent._call_count == 2


@pytest.mark.asyncio
async def test_eo_one_revision():
    """Low first score triggers revision; second attempt approved."""
    strategy = EvaluatorOptimizerStrategy(max_revisions=2, approve_threshold=8.0)
    agent = MockAgent(
        responses=[
            # Generate v1
            ChatMessage.assistant("Draft"),
            # Evaluate v1: low score
            ChatMessage.assistant("Score: 4\nFeedback: Too short, add examples"),
            # Generate v2 (with feedback appended)
            ChatMessage.assistant("Improved draft with examples"),
            # Evaluate v2: approved
            ChatMessage.assistant("Score: 9\nFeedback: Much better"),
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Write something")
    assert result == "Improved draft with examples"
    assert agent._call_count == 4


@pytest.mark.asyncio
async def test_eo_exhausted_returns_last():
    """When max revisions exhausted, return the last response."""
    strategy = EvaluatorOptimizerStrategy(max_revisions=1, approve_threshold=9.0)
    agent = MockAgent(
        responses=[
            # Generate v1
            ChatMessage.assistant("Attempt 1"),
            # Evaluate v1
            ChatMessage.assistant("Score: 5\nFeedback: Not good enough"),
            # Generate v2
            ChatMessage.assistant("Attempt 2"),
            # Evaluate v2: still not approved
            ChatMessage.assistant("Score: 6\nFeedback: Still needs work"),
        ],
        name="test",
        model="mock",
        strategy=strategy,
    )
    result = await agent.chat("Write something")
    assert result == "Attempt 2"
