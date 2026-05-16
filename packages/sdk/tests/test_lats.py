# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for Language Agent Tree Search (LATS) execution strategy."""

from __future__ import annotations

from typing import Any

import pytest

from sagewai.core._strategy_utils import parse_score
from sagewai.core.base import BaseAgent
from sagewai.core.events import AgentEvent
from sagewai.core.lats import LATSNode, LATSStrategy
from sagewai.models.message import ChatMessage, ToolCall
from sagewai.models.tool import ToolSpec

# ---------------------------------------------------------------------------
# Mock agent
# ---------------------------------------------------------------------------


class MockLLMAgent(BaseAgent):
    """Agent that returns predetermined LLM responses in sequence."""

    def __init__(self, responses: list[ChatMessage], **kwargs: Any):
        super().__init__(**kwargs)
        self._responses = list(responses)
        self._call_count = 0

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        if self._call_count < len(self._responses):
            response = self._responses[self._call_count]
            self._call_count += 1
            return response
        return ChatMessage.assistant(content="[No more responses]")


# ---------------------------------------------------------------------------
# LATSNode tests
# ---------------------------------------------------------------------------


class TestLATSNode:
    def test_uct_score_unvisited(self):
        node = LATSNode(id=1)
        assert node.uct_score() == float("inf")

    def test_uct_score_no_parent(self):
        node = LATSNode(id=1, value=8.0, visits=2)
        assert node.uct_score() == 4.0  # exploitation only

    def test_uct_score_with_parent(self):
        parent = LATSNode(id=0, visits=10)
        child = LATSNode(id=1, parent=parent, value=6.0, visits=3)
        score = child.uct_score(exploration_weight=1.41)
        # exploitation = 6/3 = 2.0
        # exploration = 1.41 * sqrt(ln(10)/3) ≈ 1.41 * 0.876 ≈ 1.24
        assert score > 2.0
        assert score < 4.0

    def test_average_value_zero_visits(self):
        node = LATSNode(id=1)
        assert node.average_value == 0.0

    def test_average_value(self):
        node = LATSNode(id=1, value=15.0, visits=3)
        assert node.average_value == 5.0


# ---------------------------------------------------------------------------
# LATSStrategy unit tests
# ---------------------------------------------------------------------------


class TestLATSStrategyInit:
    def test_defaults(self):
        s = LATSStrategy()
        assert s.n_samples == 3
        assert s.max_depth == 4
        assert s.max_iterations == 8
        assert s.exploration_weight == 1.41
        assert s.reflection_threshold == 4.0

    def test_custom_params(self):
        s = LATSStrategy(n_samples=5, max_depth=6, max_iterations=12)
        assert s.n_samples == 5
        assert s.max_depth == 6
        assert s.max_iterations == 12

    def test_min_bounds(self):
        s = LATSStrategy(n_samples=0, max_depth=0, max_iterations=0)
        assert s.n_samples == 2
        assert s.max_depth == 1
        assert s.max_iterations == 1


class TestLATSHelpers:
    def test_extract_task(self):
        messages = [
            ChatMessage.system("You are helpful."),
            ChatMessage.user("Solve this problem"),
        ]
        assert LATSStrategy._extract_task(messages) == "Solve this problem"

    def test_extract_task_no_user(self):
        messages = [ChatMessage.system("System only")]
        assert LATSStrategy._extract_task(messages) == ""

    def test_parse_score_valid(self):
        assert parse_score("8") == 8.0
        assert parse_score("Score: 7.5") == 7.5
        assert parse_score("  3  ") == 3.0

    def test_parse_score_clamped(self):
        assert parse_score("15") == 10.0
        assert parse_score("0") == 1.0

    def test_parse_score_fallback(self):
        assert parse_score("no number here") == 5.5

    def test_format_trajectory(self):
        node = LATSNode(
            id=1,
            messages=[
                ChatMessage.system("sys"),
                ChatMessage.user("task"),
                ChatMessage.assistant("response"),
            ],
        )
        trajectory = LATSStrategy._format_trajectory(node)
        assert "USER: task" in trajectory
        assert "ASSISTANT: response" in trajectory
        assert "SYSTEM" not in trajectory  # system messages excluded

    def test_format_trajectory_with_tool_calls(self):
        node = LATSNode(
            id=1,
            messages=[
                ChatMessage.user("task"),
                ChatMessage.assistant(
                    tool_calls=[ToolCall(id="tc1", name="search", arguments={"q": "test"})]
                ),
            ],
        )
        trajectory = LATSStrategy._format_trajectory(node)
        assert "TOOL CALL: search" in trajectory


class TestLATSTreeOperations:
    def test_create_node_root(self):
        s = LATSStrategy()
        root = s._create_node(parent=None, messages=[])
        assert root.depth == 0
        assert root.parent is None
        assert s._node_counter == 1

    def test_create_node_child(self):
        s = LATSStrategy()
        root = s._create_node(parent=None, messages=[])
        child = s._create_node(parent=root, messages=[])
        assert child.depth == 1
        assert child.parent is root
        assert s._node_counter == 2

    def test_select_unvisited(self):
        s = LATSStrategy()
        root = LATSNode(id=0, visits=5, value=10.0)
        child1 = LATSNode(id=1, parent=root, visits=3, value=6.0)
        child2 = LATSNode(id=2, parent=root, visits=0)  # unvisited -> inf UCT
        root.children = [child1, child2]

        selected = s._select(root)
        assert selected is child2  # unvisited gets infinite UCT

    def test_select_no_children(self):
        s = LATSStrategy()
        root = LATSNode(id=0)
        assert s._select(root) is root

    def test_backpropagate(self):
        s = LATSStrategy()
        root = LATSNode(id=0)
        child = LATSNode(id=1, parent=root)
        grandchild = LATSNode(id=2, parent=child)

        s._backpropagate(grandchild, 7.0)

        assert grandchild.visits == 1
        assert grandchild.value == 7.0
        assert child.visits == 1
        assert child.value == 7.0
        assert root.visits == 1
        assert root.value == 7.0

    def test_find_best_leaf(self):
        s = LATSStrategy()
        root = LATSNode(id=0, value=3.0, visits=1)
        child1 = LATSNode(
            id=1,
            parent=root,
            value=8.0,
            visits=1,
            response=ChatMessage.assistant("good"),
        )
        child2 = LATSNode(
            id=2,
            parent=root,
            value=5.0,
            visits=1,
            response=ChatMessage.assistant("ok"),
        )
        root.children = [child1, child2]

        best = s._find_best_leaf(root)
        assert best is child1


# ---------------------------------------------------------------------------
# Integration-level tests (mocked LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lats_execute_simple():
    """LATS returns a result when agent produces terminal responses."""
    # Response sequence: n_samples expansion responses + n_samples eval scores
    responses = [
        # Expansion responses (2 samples)
        ChatMessage.assistant(content="Answer A"),
        ChatMessage.assistant(content="Answer B"),
        # Eval scores
        ChatMessage.assistant(content="9"),
        ChatMessage.assistant(content="6"),
    ]

    agent = MockLLMAgent(
        responses=responses,
        name="lats-test",
        model="mock",
        strategy=LATSStrategy(n_samples=2, max_depth=2, max_iterations=1),
    )

    result = await agent.chat("What is 2+2?")
    assert result  # Should return something
    assert agent._call_count > 0


@pytest.mark.asyncio
async def test_lats_execute_with_tools():
    """LATS handles tool calls during expansion."""

    async def mock_search(query: str) -> str:
        return f"Result for {query}"

    tool = ToolSpec(
        name="search",
        description="Search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=mock_search,
    )

    responses = [
        # First sample: tool call
        ChatMessage.assistant(
            tool_calls=[ToolCall(id="tc1", name="search", arguments={"query": "test"})]
        ),
        # Second sample: direct answer
        ChatMessage.assistant(content="Direct answer"),
        # Eval scores
        ChatMessage.assistant(content="7"),
        ChatMessage.assistant(content="8"),
    ]

    agent = MockLLMAgent(
        responses=responses,
        name="lats-tool-test",
        model="mock",
        tools=[tool],
        strategy=LATSStrategy(n_samples=2, max_depth=2, max_iterations=1),
    )

    result = await agent.chat("Search for test")
    assert result


@pytest.mark.asyncio
async def test_lats_emits_events():
    """LATS emits lifecycle events."""
    events: list[tuple[AgentEvent, dict]] = []

    responses = [
        ChatMessage.assistant(content="Answer 1"),
        ChatMessage.assistant(content="Answer 2"),
        ChatMessage.assistant(content="8"),
        ChatMessage.assistant(content="7"),
    ]

    agent = MockLLMAgent(
        responses=responses,
        name="lats-events",
        model="mock",
        strategy=LATSStrategy(n_samples=2, max_depth=2, max_iterations=1),
    )
    agent.on_event(lambda e, d: events.append((e, d)))

    await agent.chat("Test")

    event_types = [e for e, _ in events]
    assert AgentEvent.RUN_STARTED in event_types
    assert AgentEvent.RUN_FINISHED in event_types
    assert AgentEvent.STEP_STARTED in event_types
    assert AgentEvent.STEP_FINISHED in event_types


@pytest.mark.asyncio
async def test_lats_reflection_on_low_score():
    """LATS generates reflections for low-scoring nodes."""
    responses = [
        # Expansion (2 samples)
        ChatMessage.assistant(content="Bad answer"),
        ChatMessage.assistant(content="Another bad answer"),
        # Eval scores (both low)
        ChatMessage.assistant(content="2"),
        ChatMessage.assistant(content="3"),
        # Reflections (for both low-scoring nodes)
        ChatMessage.assistant(content="Should try a different approach"),
        ChatMessage.assistant(content="Need to use tools instead"),
        # Second iteration expansion (2 samples)
        ChatMessage.assistant(content="Better answer"),
        ChatMessage.assistant(content="Good answer"),
        # Second iteration eval
        ChatMessage.assistant(content="9"),
        ChatMessage.assistant(content="7"),
    ]

    agent = MockLLMAgent(
        responses=responses,
        name="lats-reflect",
        model="mock",
        strategy=LATSStrategy(
            n_samples=2,
            max_depth=3,
            max_iterations=2,
            reflection_threshold=5.0,
        ),
    )

    result = await agent.chat("Solve this")
    assert result
    assert agent._call_count > 4  # Should have done reflection + second iteration


@pytest.mark.asyncio
async def test_lats_max_depth_terminates():
    """Nodes at max depth are marked terminal."""
    responses = [
        ChatMessage.assistant(content="Step 1"),
        ChatMessage.assistant(content="Step 1b"),
        ChatMessage.assistant(content="8"),
        ChatMessage.assistant(content="6"),
    ]

    agent = MockLLMAgent(
        responses=responses,
        name="lats-depth",
        model="mock",
        strategy=LATSStrategy(n_samples=2, max_depth=1, max_iterations=2),
    )

    result = await agent.chat("Test depth limit")
    assert result


@pytest.mark.asyncio
async def test_lats_early_stop_high_confidence():
    """LATS stops early when a high-confidence answer is found."""
    responses = [
        ChatMessage.assistant(content="Perfect answer"),
        ChatMessage.assistant(content="Ok answer"),
        # High score triggers early stop
        ChatMessage.assistant(content="9.5"),
        ChatMessage.assistant(content="5"),
    ]

    agent = MockLLMAgent(
        responses=responses,
        name="lats-early",
        model="mock",
        strategy=LATSStrategy(n_samples=2, max_depth=4, max_iterations=5),
    )

    result = await agent.chat("Easy question")
    assert result
    # Should stop after first iteration due to 9.5 score
    assert agent._call_count == 4  # 2 expansions + 2 evals


@pytest.mark.asyncio
async def test_lats_fallback_on_no_terminal():
    """LATS falls back to best leaf when no terminal node found."""
    responses = [
        # All responses have tool calls (non-terminal)
        ChatMessage.assistant(tool_calls=[ToolCall(id="t1", name="search", arguments={"q": "a"})]),
        ChatMessage.assistant(tool_calls=[ToolCall(id="t2", name="search", arguments={"q": "b"})]),
        # Eval scores
        ChatMessage.assistant(content="7"),
        ChatMessage.assistant(content="5"),
    ]

    async def mock_search(q: str) -> str:
        return "result"

    tool = ToolSpec(
        name="search",
        description="Search",
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
        handler=mock_search,
    )

    agent = MockLLMAgent(
        responses=responses,
        name="lats-fallback",
        model="mock",
        tools=[tool],
        strategy=LATSStrategy(n_samples=2, max_depth=2, max_iterations=1),
    )

    # When no terminal nodes exist, LATS returns best leaf (tool-call node)
    # which may have no text content — chat() returns ""
    await agent.chat("Search query")
    # The strategy completes without error; at least 4 LLM calls made
    assert agent._call_count >= 4
