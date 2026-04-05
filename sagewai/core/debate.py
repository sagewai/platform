# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Debate execution strategy — multi-perspective reasoning with judge synthesis.

Multiple debater personas argue across rounds, then a judge synthesizes the
strongest arguments into a final answer. Reduces hallucinations by forcing
explicit disagreement and critique.

Usage::

    from sagewai.core.debate import DebateStrategy

    agent = UniversalAgent(
        name="Analyst",
        model="gpt-4o",
        strategy=DebateStrategy(n_debaters=3, max_rounds=2),
    )
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent
    from sagewai.models.tool import ToolSpec

logger = logging.getLogger(__name__)

_DEBATER_PROMPTS = [
    "You are Debater {n}. Provide a well-reasoned answer to the task. "
    "Be thorough and consider multiple angles. Defend your position clearly.",
    "You are Debater {n}. Take a different perspective from the others. "
    "Challenge assumptions and consider alternative viewpoints.",
    "You are Debater {n}. Focus on finding flaws in other arguments "
    "and strengthening the most defensible position.",
]

_ROUND_PROMPT = (
    "Here are the other debaters' arguments from the previous round:\n\n"
    "{previous_arguments}\n\n"
    "Consider these perspectives. Refine your position — you may update, "
    "strengthen, or change your answer based on the arguments presented."
)

_JUDGE_PROMPT = (
    "You are the judge. {n} debaters have argued their positions across "
    "{rounds} round(s). Review all final arguments below and synthesize "
    "the strongest reasoning into a single, definitive answer.\n\n"
    "{arguments}\n\n"
    "Final answer:"
)


class DebateStrategy:
    """Multi-perspective debate execution strategy.

    1. Generate N initial debater responses (parallel, with distinct role prompts).
    2. For each round: each debater sees all others' arguments and refines.
    3. A judge LLM reviews all final arguments and synthesizes the best answer.

    Parameters
    ----------
    n_debaters:
        Number of debater perspectives (minimum 2).
    max_rounds:
        Number of debate rounds before judging (minimum 1).
    judge_prompt:
        Template for the judge. Must contain ``{n}``, ``{rounds}``,
        and ``{arguments}``.
    """

    def __init__(
        self,
        *,
        n_debaters: int = 3,
        max_rounds: int = 2,
        judge_prompt: str | None = None,
    ) -> None:
        self.n_debaters = max(2, n_debaters)
        self.max_rounds = max(1, max_rounds)
        self.judge_prompt = judge_prompt or _JUDGE_PROMPT

    async def execute(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_iterations: int,
    ) -> ChatMessage:
        """Run the debate and return the judge's synthesis."""
        await agent._emit(
            AgentEvent.STEP_STARTED,
            {
                "step": "debate_start",
                "n_debaters": self.n_debaters,
                "max_rounds": self.max_rounds,
            },
        )

        # Track each debater's arguments across rounds
        arguments: list[list[str]] = [[] for _ in range(self.n_debaters)]

        for round_num in range(self.max_rounds):
            await agent._emit(
                AgentEvent.STEP_STARTED,
                {"step": f"debate_round_{round_num}", "round": round_num},
            )

            # Build messages for each debater
            tasks = []
            for i in range(self.n_debaters):
                debater_msgs = self._build_debater_messages(
                    messages, i, round_num, arguments,
                )
                tasks.append(self._generate_argument(agent, debater_msgs, i, round_num))

            # Run all debaters in parallel
            responses = await asyncio.gather(*tasks)

            for i, resp in enumerate(responses):
                arguments[i].append(resp.content or "")

            await agent._emit(
                AgentEvent.STEP_FINISHED,
                {
                    "step": f"debate_round_{round_num}",
                    "debaters": self.n_debaters,
                },
            )

            logger.info(
                "Debate round %d/%d: %d debaters responded",
                round_num + 1, self.max_rounds, self.n_debaters,
            )

        # Judge phase
        await agent._emit(AgentEvent.STEP_STARTED, {"step": "debate_judge"})

        result = await self._judge(agent, arguments)

        await agent._emit(AgentEvent.STEP_FINISHED, {"step": "debate_judge"})

        if result.content:
            await agent._emit(
                AgentEvent.TEXT_MESSAGE_CONTENT,
                {"message_id": "debate_verdict", "delta": result.content},
            )

        await agent._emit(AgentEvent.STEP_FINISHED, {"step": "debate_start"})
        messages.append(result)
        return result

    def _build_debater_messages(
        self,
        original_messages: list[ChatMessage],
        debater_idx: int,
        round_num: int,
        arguments: list[list[str]],
    ) -> list[ChatMessage]:
        """Build the message list for a specific debater in a specific round."""
        # Debater role prompt (cycle through available prompts)
        role_template = _DEBATER_PROMPTS[debater_idx % len(_DEBATER_PROMPTS)]
        role_prompt = role_template.format(n=debater_idx + 1)

        msgs = [ChatMessage.system(role_prompt)] + list(original_messages)

        # After round 0, include other debaters' arguments
        if round_num > 0:
            prev_args = []
            for j in range(self.n_debaters):
                if j != debater_idx and arguments[j]:
                    latest = arguments[j][-1]
                    prev_args.append(f"Debater {j + 1}: {latest[:600]}")

            if prev_args:
                context = _ROUND_PROMPT.format(
                    previous_arguments="\n\n".join(prev_args),
                )
                msgs.append(ChatMessage.user(context))

        return msgs

    async def _generate_argument(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        debater_idx: int,
        round_num: int,
    ) -> ChatMessage:
        """Generate a single debater's argument."""
        try:
            return await agent._call_llm(messages, [])
        except Exception:
            logger.exception(
                "Debater %d failed in round %d", debater_idx, round_num,
            )
            return ChatMessage.assistant("[Debater argument generation failed]")

    async def _judge(
        self,
        agent: BaseAgent,
        arguments: list[list[str]],
    ) -> ChatMessage:
        """Ask the LLM to judge all final arguments and synthesize an answer."""
        # Collect final arguments (last from each debater)
        final_args = []
        for i, debater_args in enumerate(arguments):
            if debater_args:
                final_args.append(
                    f"--- Debater {i + 1} (final position) ---\n{debater_args[-1][:800]}"
                )

        formatted = "\n\n".join(final_args)
        judge_text = self.judge_prompt.format(
            n=len(arguments),
            rounds=self.max_rounds,
            arguments=formatted,
        )
        judge_messages = [ChatMessage.user(judge_text)]
        try:
            return await agent._call_llm(judge_messages, [])
        except Exception:
            logger.exception("Debate judge failed")
            # Fallback: return first debater's final argument
            for debater_args in arguments:
                if debater_args and debater_args[-1]:
                    return ChatMessage.assistant(debater_args[-1])
            return ChatMessage.assistant("[Debate: judge and all debaters failed]")
