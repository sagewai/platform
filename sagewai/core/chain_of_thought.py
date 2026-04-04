# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Chain-of-Thought execution strategy.

Single-pass reasoning with explicit step-by-step instructions. No tool loop —
the LLM is asked to reason through the problem in one call.

Usage::

    from sagewai.core.chain_of_thought import ChainOfThoughtStrategy

    agent = UniversalAgent(
        name="Reasoner",
        model="gpt-4o",
        strategy=ChainOfThoughtStrategy(),
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent
    from sagewai.models.tool import ToolSpec

logger = logging.getLogger(__name__)

_DEFAULT_COT_PROMPT = (
    "Think step by step. Break the problem into parts, reason through each one, "
    "and then provide your final answer clearly at the end."
)


class ChainOfThoughtStrategy:
    """Single-pass Chain-of-Thought reasoning strategy.

    Prepends a reasoning instruction to the conversation and makes one LLM call.
    By default, tools are excluded to force pure text reasoning. Set
    ``include_tools=True`` to allow tool use alongside CoT reasoning.

    Parameters
    ----------
    cot_prompt:
        System instruction prepended to encourage step-by-step reasoning.
    include_tools:
        Whether to pass tools to the LLM call (default: False).
    """

    def __init__(
        self,
        *,
        cot_prompt: str | None = None,
        include_tools: bool = False,
    ) -> None:
        self.cot_prompt = cot_prompt or _DEFAULT_COT_PROMPT
        self.include_tools = include_tools

    async def execute(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_iterations: int,
    ) -> ChatMessage:
        """Run a single CoT reasoning pass."""
        await agent._emit(AgentEvent.STEP_STARTED, {"step": "cot_reasoning"})

        # Prepend CoT instruction as a system message
        cot_messages = [ChatMessage.system(self.cot_prompt)] + list(messages)
        available_tools = tools if self.include_tools else []

        response = await agent._call_llm(cot_messages, available_tools)
        messages.append(response)

        if response.content:
            await agent._emit(
                AgentEvent.TEXT_MESSAGE_CONTENT,
                {"message_id": "cot_response", "delta": response.content},
            )

        await agent._emit(AgentEvent.STEP_FINISHED, {"step": "cot_reasoning"})
        return response
