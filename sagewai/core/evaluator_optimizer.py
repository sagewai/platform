# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Evaluator-Optimizer (Actor-Critic) execution strategy.

A generate → evaluate → revise loop where the LLM produces a response,
an evaluator scores it and provides feedback, and the generator revises
until the evaluator approves or max revisions are reached.

Usage::

    from sagewai.core.evaluator_optimizer import EvaluatorOptimizerStrategy

    agent = UniversalAgent(
        name="Writer",
        model="gpt-4o",
        strategy=EvaluatorOptimizerStrategy(max_revisions=3),
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sagewai.core._strategy_utils import extract_task, parse_score
from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent
    from sagewai.models.tool import ToolSpec

logger = logging.getLogger(__name__)

_EVAL_PROMPT = (
    "You are a critical evaluator. Score the following response on a scale "
    "of 1-10 for quality, correctness, and completeness.\n"
    "Then provide specific feedback for improvement.\n\n"
    "Task:\n{task}\n\n"
    "Response:\n{response}\n\n"
    "Format your reply as:\n"
    "Score: <number>\n"
    "Feedback: <your feedback>"
)


class EvaluatorOptimizerStrategy:
    """Generate-evaluate-revise loop execution strategy.

    1. Generate a response (with tools if available).
    2. Evaluate it with an LLM critic (score + feedback).
    3. If score >= threshold → return (approved).
    4. Otherwise, append feedback and retry.
    5. After max_revisions, return the last response.

    Parameters
    ----------
    max_revisions:
        Maximum revision cycles (default: 3).
    approve_threshold:
        Score (1-10) at or above which the response is accepted.
    eval_prompt:
        Template for evaluation. Must contain ``{task}`` and ``{response}``.
    """

    def __init__(
        self,
        *,
        max_revisions: int = 3,
        approve_threshold: float = 8.0,
        eval_prompt: str | None = None,
    ) -> None:
        self.max_revisions = max(1, max_revisions)
        self.approve_threshold = max(1.0, min(10.0, approve_threshold))
        self.eval_prompt = eval_prompt or _EVAL_PROMPT

    async def execute(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_iterations: int,
    ) -> ChatMessage:
        """Run the evaluator-optimizer loop."""
        task = extract_task(messages)
        last_response: ChatMessage | None = None

        for revision in range(self.max_revisions + 1):
            # Generate
            await agent._emit(
                AgentEvent.STEP_STARTED,
                {"step": f"eo_generate_{revision}", "revision": revision},
            )

            response = await agent._call_llm(messages, [])
            messages.append(response)
            last_response = response

            await agent._emit(
                AgentEvent.STEP_FINISHED, {"step": f"eo_generate_{revision}"},
            )

            if not response.content:
                continue

            # Evaluate
            await agent._emit(
                AgentEvent.STEP_STARTED, {"step": f"eo_evaluate_{revision}"},
            )

            score, feedback = await self._evaluate(
                agent, task, response.content,
            )

            verdict = "approve" if score >= self.approve_threshold else "revise"
            await agent._emit(
                AgentEvent.STEP_FINISHED,
                {
                    "step": f"eo_evaluate_{revision}",
                    "score": score,
                    "verdict": verdict,
                },
            )

            logger.info(
                "EvalOpt revision %d/%d: score %.1f (%s)",
                revision, self.max_revisions, score, verdict,
            )

            if score >= self.approve_threshold:
                if response.content:
                    await agent._emit(
                        AgentEvent.TEXT_MESSAGE_CONTENT,
                        {"message_id": f"eo_approved_{revision}", "delta": response.content},
                    )
                return response

            # Revise: append feedback as user message (skip on last iteration)
            if revision < self.max_revisions and feedback:
                messages.append(
                    ChatMessage.user(
                        f"The evaluator scored your response {score:.0f}/10 "
                        f"and provided this feedback:\n{feedback}\n\n"
                        f"Please revise your response to address the feedback."
                    )
                )

        # Exhausted revisions — return last response
        logger.warning(
            "EvaluatorOptimizer exhausted %d revisions. Returning last response.",
            self.max_revisions,
        )
        result = last_response or ChatMessage.assistant(
            "[EvaluatorOptimizer: no response generated]"
        )
        if result.content:
            await agent._emit(
                AgentEvent.TEXT_MESSAGE_CONTENT,
                {"message_id": "eo_final", "delta": result.content},
            )
        return result

    async def _evaluate(
        self, agent: BaseAgent, task: str, response: str,
    ) -> tuple[float, str]:
        """Evaluate a response. Returns (score, feedback)."""
        eval_text = self.eval_prompt.format(
            task=task[:1500], response=response[:2000],
        )
        eval_messages = [ChatMessage.user(eval_text)]
        try:
            eval_response = await agent._call_llm(eval_messages, [])
            content = eval_response.content or ""
            score = self._extract_score(content)
            feedback = self._extract_feedback(content)
            return score, feedback
        except Exception:
            logger.exception("EvaluatorOptimizer evaluation failed")
            return 5.0, ""

    @staticmethod
    def _extract_score(text: str) -> float:
        """Extract score from 'Score: N' format, falling back to parse_score."""
        for line in text.split("\n"):
            line = line.strip()
            if line.lower().startswith("score:"):
                return parse_score(line.split(":", 1)[1])
        return parse_score(text)

    @staticmethod
    def _extract_feedback(text: str) -> str:
        """Extract feedback from 'Feedback: ...' format."""
        lines = text.split("\n")
        collecting = False
        feedback_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith("feedback:"):
                collecting = True
                remainder = stripped.split(":", 1)[1].strip()
                if remainder:
                    feedback_lines.append(remainder)
            elif collecting:
                feedback_lines.append(line)
        if feedback_lines:
            return "\n".join(feedback_lines).strip()
        # Fallback: return everything after the score line
        for i, line in enumerate(lines):
            if line.strip().lower().startswith("score:"):
                return "\n".join(lines[i + 1:]).strip()
        return text.strip()
