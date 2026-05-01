# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for Tree-of-Thoughts execution strategy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.core.tree_of_thoughts import ThoughtBranch, TreeOfThoughtsStrategy
from sagewai.models.message import ChatMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_agent(responses: list[str] | None = None):
    """Create a mock agent that returns sequential responses."""
    agent = MagicMock()
    agent.config = MagicMock()
    agent.config.name = "test-agent"
    agent._emit = AsyncMock()

    if responses:
        response_iter = iter(responses)

        async def mock_call_llm(messages, tools):
            try:
                text = next(response_iter)
            except StopIteration:
                text = "default response"
            return ChatMessage.assistant(text)

        agent._call_llm = AsyncMock(side_effect=mock_call_llm)
    else:
        agent._call_llm = AsyncMock(return_value=ChatMessage.assistant("default"))

    return agent


# ---------------------------------------------------------------------------
# ThoughtBranch
# ---------------------------------------------------------------------------


class TestThoughtBranch:
    def test_defaults(self):
        branch = ThoughtBranch(depth=0)
        assert branch.depth == 0
        assert branch.messages == []
        assert branch.response is None
        assert branch.score == 0.0

    def test_with_response(self):
        resp = ChatMessage.assistant("thought")
        branch = ThoughtBranch(depth=1, response=resp, score=7.5)
        assert branch.response.content == "thought"
        assert branch.score == 7.5


# ---------------------------------------------------------------------------
# TreeOfThoughtsStrategy config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self):
        s = TreeOfThoughtsStrategy()
        assert s.branches == 3
        assert s.max_depth == 2
        assert s.top_k == 1

    def test_branches_clamped(self):
        s = TreeOfThoughtsStrategy(branches=1)
        assert s.branches == 2  # min 2

    def test_max_depth_clamped(self):
        s = TreeOfThoughtsStrategy(max_depth=0)
        assert s.max_depth == 1  # min 1

    def test_top_k_clamped_to_branches(self):
        s = TreeOfThoughtsStrategy(branches=3, top_k=5)
        assert s.top_k == 3  # clamped to branches

    def test_top_k_min(self):
        s = TreeOfThoughtsStrategy(top_k=0)
        assert s.top_k == 1  # min 1

    def test_custom_prompts(self):
        s = TreeOfThoughtsStrategy(
            branch_prompt="Think #{branch_num}",
            eval_prompt="Rate: {reasoning}",
        )
        assert "Think" in s.branch_prompt
        assert "Rate" in s.eval_prompt


# ---------------------------------------------------------------------------
# Score parsing
# ---------------------------------------------------------------------------


class TestParseScore:
    def test_simple_number(self):
        assert TreeOfThoughtsStrategy._parse_score("8") == 8.0

    def test_number_with_text(self):
        assert TreeOfThoughtsStrategy._parse_score("Score: 7") == 7.0

    def test_decimal(self):
        assert TreeOfThoughtsStrategy._parse_score("7.5") == 7.5

    def test_clamped_high(self):
        assert TreeOfThoughtsStrategy._parse_score("15") == 10.0

    def test_clamped_low(self):
        assert TreeOfThoughtsStrategy._parse_score("0") == 1.0

    def test_no_number(self):
        assert TreeOfThoughtsStrategy._parse_score("no number here") == 5.0

    def test_number_with_punctuation(self):
        assert TreeOfThoughtsStrategy._parse_score("(8)") == 8.0

    def test_empty_string(self):
        assert TreeOfThoughtsStrategy._parse_score("") == 5.0


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class TestExecution:
    @pytest.mark.asyncio
    async def test_basic_execution(self):
        """ToT produces a result from the best scoring branch."""
        # For 2 branches, max_depth=1:
        # - 2 branch generation calls
        # - 2 evaluation calls
        # Total: 4 _call_llm calls
        responses = [
            "Branch 1 answer",  # branch 0 generation
            "Branch 2 answer",  # branch 1 generation
            "8",  # branch 0 eval
            "6",  # branch 1 eval
        ]
        agent = _make_mock_agent(responses)
        strategy = TreeOfThoughtsStrategy(branches=2, max_depth=1)

        result = await strategy.execute(agent, [ChatMessage.user("test")], [], 10)
        assert result.content is not None
        assert result.content != ""

    @pytest.mark.asyncio
    async def test_best_branch_selected(self):
        """The highest-scoring branch is selected."""
        responses = [
            "low quality",  # branch 0
            "high quality",  # branch 1
            "3",  # score branch 0
            "9",  # score branch 1
        ]
        agent = _make_mock_agent(responses)
        strategy = TreeOfThoughtsStrategy(branches=2, max_depth=1)

        result = await strategy.execute(agent, [ChatMessage.user("test")], [], 10)
        assert result.content == "high quality"

    @pytest.mark.asyncio
    async def test_emits_events(self):
        """Strategy emits step events during execution."""
        responses = ["answer1", "answer2", "7", "5"]
        agent = _make_mock_agent(responses)
        strategy = TreeOfThoughtsStrategy(branches=2, max_depth=1)

        await strategy.execute(agent, [ChatMessage.user("test")], [], 10)
        emit_calls = agent._emit.call_args_list
        assert len(emit_calls) > 0
        # Check that tot_start was emitted
        first_call_data = emit_calls[0][0][1]
        assert "branches" in first_call_data

    @pytest.mark.asyncio
    async def test_multi_depth(self):
        """Multi-depth tree explores deeper."""
        # branches=2, max_depth=2, top_k=1
        # Depth 0: 2 branches + 2 evals = 4 calls
        # Depth 1: 2 branches (expanded from best) + 2 evals = 4 calls
        responses = [
            "depth0_b0",
            "depth0_b1",  # depth 0 branches
            "8",
            "5",  # depth 0 evals
            "depth1_b0",
            "depth1_b1",  # depth 1 branches (expanded from best)
            "9",
            "6",  # depth 1 evals
        ]
        agent = _make_mock_agent(responses)
        strategy = TreeOfThoughtsStrategy(branches=2, max_depth=2, top_k=1)

        result = await strategy.execute(agent, [ChatMessage.user("test")], [], 10)
        assert result.content is not None

    @pytest.mark.asyncio
    async def test_branch_failure_handled(self):
        """Failed branches get a fallback response with score 0."""
        agent = MagicMock()
        agent.config = MagicMock()
        agent.config.name = "test"
        agent._emit = AsyncMock()

        call_count = 0

        async def failing_llm(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("LLM error")
            if call_count == 2:
                return ChatMessage.assistant("good answer")
            # Eval calls
            if call_count == 3:
                return ChatMessage.assistant("2")  # failed branch gets low score
            return ChatMessage.assistant("8")  # good branch gets high score

        agent._call_llm = AsyncMock(side_effect=failing_llm)
        strategy = TreeOfThoughtsStrategy(branches=2, max_depth=1)

        result = await strategy.execute(agent, [ChatMessage.user("test")], [], 10)
        assert result.content == "good answer"

    @pytest.mark.asyncio
    async def test_messages_updated(self):
        """The original messages list is updated with the best response."""
        responses = ["answer1", "answer2", "8", "5"]
        agent = _make_mock_agent(responses)
        strategy = TreeOfThoughtsStrategy(branches=2, max_depth=1)

        messages = [ChatMessage.user("test")]
        await strategy.execute(agent, messages, [], 10)
        assert len(messages) == 2  # original + best response
        assert messages[-1].role.value == "assistant"

    @pytest.mark.asyncio
    async def test_eval_failure_returns_zero(self):
        """If evaluation fails, branch gets score 0."""
        agent = MagicMock()
        agent.config = MagicMock()
        agent.config.name = "test"
        agent._emit = AsyncMock()

        call_count = 0

        async def partial_fail_llm(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return ChatMessage.assistant(f"answer {call_count}")
            if call_count == 3:
                raise RuntimeError("eval failed")
            return ChatMessage.assistant("7")

        agent._call_llm = AsyncMock(side_effect=partial_fail_llm)
        strategy = TreeOfThoughtsStrategy(branches=2, max_depth=1)

        result = await strategy.execute(agent, [ChatMessage.user("test")], [], 10)
        # Should still succeed, picking the branch that got scored
        assert result.content is not None
