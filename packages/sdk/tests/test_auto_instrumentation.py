"""End-to-end tests for auto-instrumentation (OTel spans + cost tracking).

Verifies that:
- LLM_CALL_FINISHED events fire with correct token/cost data
- CostTracker accumulates tokens automatically via event_hook
- RunStore event hook records runs with token data
- UsageInfo flows from engines through strategy to events
"""

from __future__ import annotations

from typing import Any

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage, ToolCall, UsageInfo
from sagewai.models.tool import ToolSpec
from sagewai.observability.costs import CostTracker

# ---------------------------------------------------------------------------
# MockAgent that returns usage in responses
# ---------------------------------------------------------------------------


class UsageAgent(BaseAgent):
    """Agent that returns predetermined responses with usage info."""

    def __init__(self, responses: list[ChatMessage], **kwargs: Any):
        super().__init__(**kwargs)
        self._responses = list(responses)
        self._call_count = 0

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


# ---------------------------------------------------------------------------
# LLM_CALL_FINISHED event emission
# ---------------------------------------------------------------------------


class TestLLMCallFinishedEvent:
    @pytest.mark.asyncio
    async def test_emitted_on_text_response(self):
        """LLM_CALL_FINISHED fires when response has usage info."""
        events: list[tuple[AgentEvent, dict]] = []

        agent = UsageAgent(
            responses=[
                ChatMessage.assistant(
                    "Hello!",
                    usage=UsageInfo(input_tokens=50, output_tokens=20),
                ),
            ],
            name="test",
            model="gpt-4o",
        )
        agent.on_event(lambda e, d: events.append((e, d)))

        await agent.chat("Hi")

        llm_events = [(e, d) for e, d in events if e == AgentEvent.LLM_CALL_FINISHED]
        assert len(llm_events) == 1
        _, data = llm_events[0]
        assert data["model"] == "gpt-4o"
        assert data["input_tokens"] == 50
        assert data["output_tokens"] == 20
        assert data["cost_usd"] > 0
        assert data["duration_ms"] > 0

    @pytest.mark.asyncio
    async def test_emitted_per_iteration(self):
        """LLM_CALL_FINISHED fires once per LLM call in multi-step."""
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

        agent = UsageAgent(
            responses=[
                ChatMessage.assistant(
                    tool_calls=[ToolCall(id="tc1", name="search", arguments={"query": "x"})],
                    usage=UsageInfo(input_tokens=100, output_tokens=30),
                ),
                ChatMessage.assistant(
                    "Done",
                    usage=UsageInfo(input_tokens=150, output_tokens=40),
                ),
            ],
            name="multi-step",
            model="gpt-4o",
            tools=[tool_spec],
        )
        agent.on_event(lambda e, d: events.append((e, d)))

        await agent.chat("Search for x")

        llm_events = [(e, d) for e, d in events if e == AgentEvent.LLM_CALL_FINISHED]
        assert len(llm_events) == 2
        assert llm_events[0][1]["input_tokens"] == 100
        assert llm_events[1][1]["input_tokens"] == 150

    @pytest.mark.asyncio
    async def test_emitted_with_zero_usage_when_no_usage_info(self):
        """LLM_CALL_FINISHED fires with zero tokens when response has no usage."""
        events: list[tuple[AgentEvent, dict]] = []

        agent = UsageAgent(
            responses=[ChatMessage.assistant("Hello!")],
            name="no-usage",
            model="gpt-4o",
        )
        agent.on_event(lambda e, d: events.append((e, d)))

        await agent.chat("Hi")

        llm_events = [(e, d) for e, d in events if e == AgentEvent.LLM_CALL_FINISHED]
        assert len(llm_events) == 1
        assert llm_events[0][1]["input_tokens"] == 0
        assert llm_events[0][1]["output_tokens"] == 0
        assert llm_events[0][1]["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# CostTracker integration
# ---------------------------------------------------------------------------


class TestCostTrackerIntegration:
    @pytest.mark.asyncio
    async def test_auto_tracks_costs(self):
        """CostTracker accumulates tokens from LLM_CALL_FINISHED events."""
        tracker = CostTracker()

        agent = UsageAgent(
            responses=[
                ChatMessage.assistant(
                    "Hello!",
                    usage=UsageInfo(input_tokens=200, output_tokens=80),
                ),
            ],
            name="tracked",
            model="gpt-4o",
        )
        agent.on_event(tracker.event_hook)

        await agent.chat("Hi")

        assert len(tracker.runs) == 1
        run = tracker.runs[0]
        assert run.agent_name == "tracked"
        assert run.call_count == 1
        assert run.total_input_tokens == 200
        assert run.total_output_tokens == 80
        assert run.total_cost_usd > 0

    @pytest.mark.asyncio
    async def test_multi_step_accumulation(self):
        """CostTracker sums tokens across multiple LLM calls in one run."""
        tracker = CostTracker()

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

        agent = UsageAgent(
            responses=[
                ChatMessage.assistant(
                    tool_calls=[ToolCall(id="tc1", name="search", arguments={"query": "x"})],
                    usage=UsageInfo(input_tokens=100, output_tokens=30),
                ),
                ChatMessage.assistant(
                    "Done",
                    usage=UsageInfo(input_tokens=200, output_tokens=50),
                ),
            ],
            name="multi",
            model="gpt-4o",
            tools=[tool_spec],
        )
        agent.on_event(tracker.event_hook)

        await agent.chat("Search")

        run = tracker.runs[0]
        assert run.call_count == 2
        assert run.total_input_tokens == 300
        assert run.total_output_tokens == 80


# ---------------------------------------------------------------------------
# UsageInfo model
# ---------------------------------------------------------------------------


class TestUsageInfo:
    def test_total_tokens(self):
        usage = UsageInfo(input_tokens=100, output_tokens=50)
        assert usage.total_tokens == 150

    def test_defaults(self):
        usage = UsageInfo()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.model == ""
        assert usage.duration_ms == 0.0

    def test_on_chat_message(self):
        msg = ChatMessage.assistant(
            "Hello",
            usage=UsageInfo(input_tokens=10, output_tokens=5),
        )
        assert msg.usage is not None
        assert msg.usage.total_tokens == 15

    def test_chat_message_without_usage(self):
        msg = ChatMessage.assistant("Hello")
        assert msg.usage is None


# ---------------------------------------------------------------------------
# Event sequence ordering
# ---------------------------------------------------------------------------


class TestEventSequence:
    @pytest.mark.asyncio
    async def test_llm_call_finished_between_steps(self):
        """LLM_CALL_FINISHED fires between STEP_STARTED and STEP_FINISHED."""
        events: list[AgentEvent] = []

        agent = UsageAgent(
            responses=[
                ChatMessage.assistant(
                    "OK",
                    usage=UsageInfo(input_tokens=50, output_tokens=20),
                ),
            ],
            name="seq",
            model="gpt-4o",
        )
        agent.on_event(lambda e, d: events.append(e))

        await agent.chat("Hi")

        step_start_idx = events.index(AgentEvent.STEP_STARTED)
        llm_idx = events.index(AgentEvent.LLM_CALL_FINISHED)
        step_end_idx = events.index(AgentEvent.STEP_FINISHED)

        assert step_start_idx < llm_idx < step_end_idx
