# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""PlanningStrategy — decompose goals into subtasks, then execute step by step."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Literal

from sagewai.core._strategy_utils import parse_json
from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent
    from sagewai.models.tool import ToolSpec

logger = logging.getLogger(__name__)

_PLAN_SYSTEM_PROMPT = (
    "You are a planning agent. Given the user's goal, create a numbered plan "
    "as a JSON array. Each element must have 'step' (int) and 'action' (str). "
    'Example: [{"step": 1, "action": "Research the topic"}, '
    '{"step": 2, "action": "Write a summary"}]. '
    "Respond with ONLY the JSON array, no other text."
)

_REFLECT_PROMPT = (
    'You just completed step {step}: "{action}"\n'
    "Result: {result}\n\n"
    "Remaining plan: {remaining}\n\n"
    "Should the remaining plan change based on what you learned? "
    "If yes, respond with a revised JSON array of remaining steps. "
    "If no, respond with just 'no'."
)


class PlanningStrategy:
    """Execution strategy that generates a plan, then executes each step.

    Modes:
      - ``plan_then_act``: Generate plan once, execute all steps sequentially.
      - ``plan_act_reflect``: After each step, optionally revise the remaining plan.
    """

    def __init__(
        self,
        *,
        mode: Literal["plan_then_act", "plan_act_reflect"] = "plan_act_reflect",
        max_steps: int = 10,
        planner_model: str | None = None,
    ) -> None:
        self.mode = mode
        self.max_steps = max_steps
        self.planner_model = planner_model

    async def execute(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_iterations: int,
    ) -> ChatMessage:
        """Generate a plan and execute it step by step."""
        # Phase 1: Generate the plan
        plan = await self._generate_plan(agent, messages)
        await agent._emit(AgentEvent.PLAN_CREATED, {"steps": list(plan)})

        # Phase 2: Execute steps
        step_results: list[str] = []
        steps_executed = 0

        while plan and steps_executed < self.max_steps:
            current_step = plan.pop(0)
            steps_executed += 1

            # Execute the step
            step_messages = list(messages) + [
                ChatMessage.system(
                    f"You are executing step {current_step['step']}: "
                    f"{current_step['action']}\n\n"
                    f"Previous results: "
                    f"{json.dumps(step_results[-3:]) if step_results else 'None'}"
                ),
            ]
            response = await agent._call_llm(step_messages, tools)
            result_text = response.content or ""
            step_results.append(result_text)

            # Phase 3: Reflect (if enabled)
            if self.mode == "plan_act_reflect" and plan:
                revised = await self._reflect(
                    agent, current_step, result_text, plan, messages
                )
                if revised is not None:
                    plan = revised
                    await agent._emit(AgentEvent.PLAN_REVISED, {"steps": list(plan)})

        # Return the last step's result as the final response
        final_text = step_results[-1] if step_results else "No steps were executed."
        return ChatMessage.assistant(final_text)

    async def _generate_plan(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
    ) -> list[dict[str, Any]]:
        """Ask the LLM to generate a plan."""
        plan_messages = [ChatMessage.system(_PLAN_SYSTEM_PROMPT)] + [
            m for m in messages if m.role == "user"
        ]
        response = await agent._call_llm(plan_messages, [])
        return self._parse_plan(response.content or "[]")

    async def _reflect(
        self,
        agent: BaseAgent,
        completed_step: dict[str, Any],
        result: str,
        remaining: list[dict[str, Any]],
        original_messages: list[ChatMessage],
    ) -> list[dict[str, Any]] | None:
        """Ask LLM if remaining plan should change. Returns revised plan or None."""
        reflect_text = _REFLECT_PROMPT.format(
            step=completed_step["step"],
            action=completed_step["action"],
            result=result,
            remaining=json.dumps(remaining),
        )
        reflect_messages = [
            ChatMessage.system("You are reviewing your plan progress."),
            ChatMessage.user(reflect_text),
        ]
        response = await agent._call_llm(reflect_messages, [])
        text = (response.content or "").strip()

        if text.lower() == "no":
            return None

        revised = self._parse_plan(text)
        return revised if revised else None

    @staticmethod
    def _parse_plan(text: str) -> list[dict[str, Any]]:
        """Parse a JSON plan from LLM output, tolerant of SLM formatting."""
        try:
            parsed = parse_json(text)
            if isinstance(parsed, list):
                return [
                    s
                    for s in parsed
                    if isinstance(s, dict) and "step" in s and "action" in s
                ]
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse plan: %s", text[:200])
        return []


class PlanActReflectStrategy(PlanningStrategy):
    """Plan-Act-Reflect variant for no-arg registry instantiation."""

    def __init__(self) -> None:
        super().__init__(mode="plan_act_reflect")


class PlanThenActStrategy(PlanningStrategy):
    """Plan-Then-Act variant for no-arg registry instantiation."""

    def __init__(self) -> None:
        super().__init__(mode="plan_then_act")
