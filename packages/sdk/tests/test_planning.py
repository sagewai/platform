"""Tests for PlanningStrategy — task decomposition and execution."""

from __future__ import annotations

import json
from typing import Any

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.planning import PlanningStrategy
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec


class PlannerAgent(BaseAgent):
    """Agent that returns a predetermined plan, then executes steps."""

    def __init__(self, plan: list[dict], step_responses: list[str], **kwargs: Any):
        super().__init__(**kwargs)
        self._plan = plan
        self._step_responses = list(step_responses)
        self._call_count = 0

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        call_idx = self._call_count
        self._call_count += 1

        # First call: return the plan
        if call_idx == 0:
            return ChatMessage.assistant(json.dumps(self._plan))

        # Subsequent calls: return step responses
        step_idx = call_idx - 1
        if step_idx < len(self._step_responses):
            return ChatMessage.assistant(self._step_responses[step_idx])
        return ChatMessage.assistant("Done")


class ReplannerAgent(BaseAgent):
    """Agent that revises the plan after step 1."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._call_count = 0

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        call_idx = self._call_count
        self._call_count += 1

        if call_idx == 0:
            # Initial plan
            plan = [
                {"step": 1, "action": "Research topic"},
                {"step": 2, "action": "Write draft"},
            ]
            return ChatMessage.assistant(json.dumps(plan))
        if call_idx == 1:
            # Execute step 1
            return ChatMessage.assistant("Research complete")
        if call_idx == 2:
            # Reflect: revise plan
            revised = [
                {"step": 2, "action": "Write draft with new data"},
                {"step": 3, "action": "Add conclusion"},
            ]
            return ChatMessage.assistant(json.dumps(revised))
        if call_idx == 3:
            return ChatMessage.assistant("Draft written")
        if call_idx == 4:
            return ChatMessage.assistant("Conclusion added. Final result.")
        return ChatMessage.assistant("Done")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestPlanningStrategyInit:
    def test_default_mode(self):
        strategy = PlanningStrategy()
        assert strategy.mode == "plan_act_reflect"

    def test_plan_then_act_mode(self):
        strategy = PlanningStrategy(mode="plan_then_act")
        assert strategy.mode == "plan_then_act"

    def test_max_steps(self):
        strategy = PlanningStrategy(max_steps=5)
        assert strategy.max_steps == 5


# ---------------------------------------------------------------------------
# Plan-then-Act
# ---------------------------------------------------------------------------


class TestPlanThenAct:
    @pytest.mark.asyncio
    async def test_generates_and_executes_plan(self):
        plan = [
            {"step": 1, "action": "Research AI trends"},
            {"step": 2, "action": "Write summary"},
        ]
        agent = PlannerAgent(
            plan=plan,
            step_responses=["Found 3 trends", "Summary: AI is growing"],
            name="planner",
        )
        strategy = PlanningStrategy(mode="plan_then_act", max_steps=5)

        result = await strategy.execute(
            agent=agent,
            messages=[ChatMessage.user("Summarize AI trends")],
            tools=[],
            max_iterations=10,
        )
        # Final response should be from the last step
        assert "Summary" in result.content or "AI" in result.content

    @pytest.mark.asyncio
    async def test_respects_max_steps(self):
        plan = [
            {"step": 1, "action": "Step 1"},
            {"step": 2, "action": "Step 2"},
            {"step": 3, "action": "Step 3"},
        ]
        agent = PlannerAgent(
            plan=plan,
            step_responses=["Done 1", "Done 2", "Done 3"],
            name="planner",
        )
        strategy = PlanningStrategy(mode="plan_then_act", max_steps=2)

        await strategy.execute(
            agent=agent,
            messages=[ChatMessage.user("Do things")],
            tools=[],
            max_iterations=10,
        )
        # Should only execute 2 steps, not 3
        assert agent._call_count <= 3  # 1 plan + 2 step executions


# ---------------------------------------------------------------------------
# Plan-Act-Reflect
# ---------------------------------------------------------------------------


class TestPlanActReflect:
    @pytest.mark.asyncio
    async def test_reflect_mode_executes(self):
        plan = [
            {"step": 1, "action": "Research"},
            {"step": 2, "action": "Write"},
        ]
        agent = PlannerAgent(
            plan=plan,
            step_responses=[
                "Research done",
                "no",  # reflect: no changes needed
                "Article written",
            ],
            name="planner",
        )
        strategy = PlanningStrategy(mode="plan_act_reflect", max_steps=5)

        result = await strategy.execute(
            agent=agent,
            messages=[ChatMessage.user("Write an article")],
            tools=[],
            max_iterations=10,
        )
        assert result.content is not None


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class TestPlanningEvents:
    @pytest.mark.asyncio
    async def test_emits_plan_created_event(self):
        plan = [{"step": 1, "action": "Do something"}]
        agent = PlannerAgent(
            plan=plan,
            step_responses=["Done"],
            name="planner",
        )
        strategy = PlanningStrategy(mode="plan_then_act")

        events: list[tuple] = []
        agent.on_event(lambda event, data: events.append((event, data)))

        await strategy.execute(
            agent=agent,
            messages=[ChatMessage.user("test")],
            tools=[],
            max_iterations=10,
        )

        from sagewai.core.events import AgentEvent

        plan_events = [(e, d) for e, d in events if e == AgentEvent.PLAN_CREATED]
        assert len(plan_events) == 1
        assert len(plan_events[0][1]["steps"]) == 1
