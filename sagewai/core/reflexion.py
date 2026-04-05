# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Reflexion execution strategy — evaluate, reflect, and retry with accumulated critique.

Unlike :class:`SelfCorrectionStrategy` which validates output schemas,
Reflexion uses an LLM-as-judge to evaluate quality and generates reflective
critiques that accumulate across attempts.

Usage::

    from sagewai.core.reflexion import ReflexionStrategy

    agent = UniversalAgent(
        name="Analyst",
        model="gpt-4o",
        strategy=ReflexionStrategy(max_attempts=3, score_threshold=7.0),
    )

Reference: Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement
Learning" (2023).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sagewai.core._strategy_utils import extract_task, parse_score
from sagewai.core.events import AgentEvent
from sagewai.core.strategies import ReActStrategy
from sagewai.models.message import ChatMessage

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent
    from sagewai.core.strategies import ExecutionStrategy
    from sagewai.models.tool import ToolSpec

logger = logging.getLogger(__name__)

_EVAL_PROMPT = (
    "Evaluate the following response to the given task on a scale of 1-10.\n"
    "Consider correctness, completeness, clarity, and relevance.\n\n"
    "Task:\n{task}\n\n"
    "Response:\n{response}\n\n"
    "Score (1-10):"
)

_REFLECT_PROMPT = (
    "The following response to a task scored {score}/10. Analyze what is "
    "wrong or missing and provide specific, actionable feedback for improvement.\n\n"
    "Task:\n{task}\n\n"
    "Response:\n{response}\n\n"
    "Critique:"
)


class ReflexionStrategy:
    """Reflexion execution strategy with LLM-as-judge evaluation.

    Runs an inner strategy (default: ReAct), evaluates the result with an
    LLM judge, and if the score is below a threshold, generates a reflective
    critique. Accumulated reflections are prepended to subsequent attempts,
    giving the agent memory of past failures.

    Parameters
    ----------
    base_strategy:
        The inner strategy for generating responses (default: ReActStrategy).
    max_attempts:
        Maximum number of generate-evaluate-reflect cycles.
    score_threshold:
        Minimum evaluation score (1-10) to accept a response.
    eval_prompt:
        Template for evaluation. Must contain ``{task}`` and ``{response}``.
    reflect_prompt:
        Template for reflection. Must contain ``{task}``, ``{response}``,
        and ``{score}``.
    """

    def __init__(
        self,
        *,
        base_strategy: ExecutionStrategy | None = None,
        max_attempts: int = 3,
        score_threshold: float = 7.0,
        eval_prompt: str | None = None,
        reflect_prompt: str | None = None,
    ) -> None:
        self.base_strategy = base_strategy or ReActStrategy()
        self.max_attempts = max(1, max_attempts)
        self.score_threshold = max(1.0, min(10.0, score_threshold))
        self.eval_prompt = eval_prompt or _EVAL_PROMPT
        self.reflect_prompt = reflect_prompt or _REFLECT_PROMPT

    async def execute(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_iterations: int,
    ) -> ChatMessage:
        """Run reflexion loop: generate → evaluate → reflect → retry."""
        task = extract_task(messages)
        reflections: list[str] = []
        best_response: ChatMessage | None = None
        best_score: float = 0.0

        for attempt in range(self.max_attempts):
            await agent._emit(
                AgentEvent.STEP_STARTED,
                {"step": f"reflexion_attempt_{attempt}", "attempt": attempt},
            )

            # Build messages with accumulated reflections
            attempt_messages = list(messages)
            if reflections:
                reflection_context = (
                    "[Previous attempt feedback]\n"
                    + "\n---\n".join(reflections)
                    + "\n\nUse this feedback to improve your response."
                )
                attempt_messages.insert(0, ChatMessage.system(reflection_context))

            # Generate response via inner strategy
            response = await self.base_strategy.execute(
                agent, attempt_messages, tools, max_iterations,
            )

            # Evaluate
            await agent._emit(
                AgentEvent.STEP_STARTED, {"step": f"reflexion_evaluate_{attempt}"},
            )
            score = await self._evaluate(agent, task, response.content or "")
            await agent._emit(
                AgentEvent.STEP_FINISHED,
                {"step": f"reflexion_evaluate_{attempt}", "score": score},
            )

            logger.info(
                "Reflexion attempt %d/%d: score %.1f (threshold %.1f)",
                attempt + 1, self.max_attempts, score, self.score_threshold,
            )

            # Track best
            if score > best_score:
                best_score = score
                best_response = response

            # Accept if above threshold
            if score >= self.score_threshold:
                await agent._emit(
                    AgentEvent.STEP_FINISHED,
                    {
                        "step": f"reflexion_attempt_{attempt}",
                        "score": score,
                        "accepted": True,
                    },
                )
                messages.append(response)
                return response

            # Reflect (skip on last attempt)
            if attempt < self.max_attempts - 1:
                await agent._emit(
                    AgentEvent.STEP_STARTED, {"step": f"reflexion_reflect_{attempt}"},
                )
                critique = await self._reflect(
                    agent, task, response.content or "", score,
                )
                reflections.append(critique)
                await agent._emit(
                    AgentEvent.STEP_FINISHED,
                    {"step": f"reflexion_reflect_{attempt}", "critique": critique[:200]},
                )

            await agent._emit(
                AgentEvent.STEP_FINISHED,
                {"step": f"reflexion_attempt_{attempt}", "score": score, "accepted": False},
            )

        # Exhausted attempts — return best response
        logger.warning(
            "Reflexion exhausted %d attempts (best score: %.1f). Returning best response.",
            self.max_attempts, best_score,
        )
        result = best_response or ChatMessage.assistant(
            "[Reflexion: no valid response generated]"
        )
        messages.append(result)
        return result

    async def _evaluate(self, agent: BaseAgent, task: str, response: str) -> float:
        """Ask LLM to score the response."""
        eval_text = self.eval_prompt.format(
            task=task[:1500], response=response[:2000],
        )
        eval_messages = [ChatMessage.user(eval_text)]
        try:
            eval_response = await agent._call_llm(eval_messages, [])
            return parse_score(eval_response.content or "")
        except Exception:
            logger.exception("Reflexion evaluation failed")
            return 5.0

    async def _reflect(
        self, agent: BaseAgent, task: str, response: str, score: float,
    ) -> str:
        """Ask LLM to generate a critique."""
        reflect_text = self.reflect_prompt.format(
            task=task[:1500], response=response[:2000], score=f"{score:.1f}",
        )
        reflect_messages = [ChatMessage.user(reflect_text)]
        try:
            reflect_response = await agent._call_llm(reflect_messages, [])
            return reflect_response.content or ""
        except Exception:
            logger.exception("Reflexion reflection failed")
            return ""
