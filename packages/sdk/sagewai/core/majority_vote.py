# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Majority Vote (Self-Consistency) execution strategy.

Runs the same prompt N times in parallel and selects the most consistent
answer, either via LLM aggregation or by returning the first response.

Usage::

    from sagewai.core.majority_vote import MajorityVoteStrategy

    agent = UniversalAgent(
        name="Calculator",
        model="gpt-4o",
        strategy=MajorityVoteStrategy(n_samples=5),
    )

Reference: Wang et al., "Self-Consistency Improves Chain of Thought Reasoning
in Language Models" (2023).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal

from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent
    from sagewai.models.tool import ToolSpec

logger = logging.getLogger(__name__)

_AGGREGATION_PROMPT = (
    "You have been given {n} different responses to the same question. "
    "Identify the most common or consistent answer across all responses. "
    "Return ONLY that answer — do not explain your reasoning.\n\n"
    "Question: {task}\n\n"
    "{responses}\n\n"
    "Most consistent answer:"
)


class MajorityVoteStrategy:
    """Self-Consistency / Majority Vote execution strategy.

    Generates ``n_samples`` responses in parallel and selects the most
    consistent one. Reduces hallucinations and reasoning errors by
    filtering out outlier answers.

    Parameters
    ----------
    n_samples:
        Number of parallel samples to generate (minimum 2).
    aggregation:
        How to select the final answer:
        - ``"llm"``: Ask the LLM to identify the most consistent response.
        - ``"first"``: Return the first response (baseline fallback).
    """

    def __init__(
        self,
        *,
        n_samples: int = 3,
        aggregation: Literal["llm", "first"] = "llm",
    ) -> None:
        self.n_samples = max(2, n_samples)
        self.aggregation = aggregation

    async def execute(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_iterations: int,
    ) -> ChatMessage:
        """Generate N samples and return the most consistent answer."""
        await agent._emit(
            AgentEvent.STEP_STARTED,
            {"step": "majority_vote_sample", "n_samples": self.n_samples},
        )

        # Generate N responses in parallel
        task = self._extract_task(messages)

        async def generate_sample(idx: int) -> ChatMessage:
            sample_msgs = list(messages)
            if idx > 0:
                # Add diversity prompt for non-first samples
                sample_msgs.append(
                    ChatMessage.user(
                        f"(Provide your answer independently — variation {idx + 1})"
                    )
                )
            try:
                return await agent._call_llm(sample_msgs, [])
            except Exception:
                logger.exception("Majority vote sample %d failed", idx)
                return ChatMessage.assistant("[Sample generation failed]")

        responses = await asyncio.gather(
            *(generate_sample(i) for i in range(self.n_samples))
        )

        await agent._emit(
            AgentEvent.STEP_FINISHED,
            {
                "step": "majority_vote_sample",
                "samples_generated": len(responses),
            },
        )

        # Filter out failures
        valid = [r for r in responses if r.content and "[Sample generation failed]" not in r.content]
        if not valid:
            result = responses[0] if responses else ChatMessage.assistant(
                "[Majority vote: all samples failed]"
            )
            messages.append(result)
            return result

        # Aggregate
        if self.aggregation == "llm" and len(valid) > 1:
            await agent._emit(
                AgentEvent.STEP_STARTED, {"step": "majority_vote_aggregate"},
            )
            result = await self._llm_aggregate(agent, task, valid)
            await agent._emit(
                AgentEvent.STEP_FINISHED, {"step": "majority_vote_aggregate"},
            )
        else:
            result = valid[0]

        if result.content:
            await agent._emit(
                AgentEvent.TEXT_MESSAGE_CONTENT,
                {"message_id": "mv_result", "delta": result.content},
            )

        messages.append(result)
        return result

    async def _llm_aggregate(
        self,
        agent: BaseAgent,
        task: str,
        responses: list[ChatMessage],
    ) -> ChatMessage:
        """Ask the LLM to pick the most consistent answer."""
        formatted = "\n\n".join(
            f"--- Response {i + 1} ---\n{r.content[:800]}"
            for i, r in enumerate(responses)
        )
        agg_text = _AGGREGATION_PROMPT.format(
            n=len(responses), task=task[:1000], responses=formatted,
        )
        agg_messages = [ChatMessage.user(agg_text)]
        try:
            return await agent._call_llm(agg_messages, [])
        except Exception:
            logger.exception("Majority vote aggregation failed")
            return responses[0]

    @staticmethod
    def _extract_task(messages: list[ChatMessage]) -> str:
        """Extract the original user task."""
        for msg in messages:
            if msg.role.value == "user" and msg.content:
                return msg.content
        return ""
