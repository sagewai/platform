# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tree-of-Thoughts execution strategy.

Parallel branch exploration with self-evaluation scoring and pruning.
Generates multiple reasoning paths, scores them via LLM self-evaluation,
prunes low-scoring branches, and continues the best path.

Usage::

    from sagewai.core.tree_of_thoughts import TreeOfThoughtsStrategy

    agent = UniversalAgent(
        name="Reasoner",
        model="gpt-4o",
        strategy=TreeOfThoughtsStrategy(branches=3, max_depth=2),
    )
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sagewai.core._strategy_utils import parse_score
from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent

logger = logging.getLogger(__name__)

_BRANCH_PROMPT = (
    "Generate a distinct approach to solve this problem. "
    "Think step by step and explore a unique reasoning path. "
    "Approach #{branch_num}:"
)

_EVAL_PROMPT = (
    "Evaluate the following reasoning path on a scale of 1-10 for correctness, "
    "completeness, and coherence. Respond with ONLY a number between 1 and 10.\n\n"
    "Reasoning path:\n{reasoning}\n\nScore:"
)


@dataclass
class ThoughtBranch:
    """A single reasoning branch in the tree."""

    depth: int
    messages: list[ChatMessage] = field(default_factory=list)
    response: ChatMessage | None = None
    score: float = 0.0


class TreeOfThoughtsStrategy:
    """Tree-of-Thoughts execution strategy.

    At each depth level:
    1. Generate ``branches`` parallel reasoning paths from the current context.
    2. Score each branch via LLM self-evaluation.
    3. Prune branches below the top ``top_k`` scores.
    4. Continue with the best branch for the next depth level.
    5. Return the final response from the highest-scoring branch.

    Parameters
    ----------
    branches:
        Number of parallel reasoning branches to generate at each depth.
    max_depth:
        Maximum depth of the thought tree.
    top_k:
        Number of top branches to keep after pruning (default: 1).
    branch_prompt:
        Template for branch generation prompts. Must contain ``{branch_num}``.
    eval_prompt:
        Template for evaluation prompts. Must contain ``{reasoning}``.
    """

    def __init__(
        self,
        *,
        branches: int = 3,
        max_depth: int = 2,
        top_k: int = 1,
        branch_prompt: str | None = None,
        eval_prompt: str | None = None,
    ) -> None:
        self.branches = max(2, branches)
        self.max_depth = max(1, max_depth)
        self.top_k = max(1, min(top_k, branches))
        self.branch_prompt = branch_prompt or _BRANCH_PROMPT
        self.eval_prompt = eval_prompt or _EVAL_PROMPT

    async def execute(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_iterations: int,
    ) -> ChatMessage:
        """Run Tree-of-Thoughts exploration and return the best result."""
        await agent._emit(
            AgentEvent.STEP_STARTED,
            {"step": "tot_start", "branches": self.branches, "max_depth": self.max_depth},
        )

        # Initialize branches from the current context
        active_branches = [
            ThoughtBranch(depth=0, messages=list(messages)) for _ in range(self.branches)
        ]

        best_response: ChatMessage | None = None

        for depth in range(self.max_depth):
            await agent._emit(
                AgentEvent.STEP_STARTED,
                {"step": f"tot_depth_{depth}", "active_branches": len(active_branches)},
            )

            # Generate responses for all active branches in parallel
            tasks = []
            for i, branch in enumerate(active_branches):
                branch_msgs = list(branch.messages)
                # Add branch-specific prompt to encourage diverse thinking
                if depth == 0:
                    branch_prompt = self.branch_prompt.format(branch_num=i + 1)
                    branch_msgs.append(ChatMessage.user(branch_prompt))
                tasks.append(self._generate_branch(agent, branch_msgs, tools, i, depth))

            branch_results = await asyncio.gather(*tasks)

            # Update branches with results
            for branch, result in zip(active_branches, branch_results):
                branch.response = result
                branch.messages.append(result)
                branch.depth = depth + 1

            # Score all branches
            await self._score_branches(agent, active_branches)

            # Prune to top_k
            active_branches.sort(key=lambda b: b.score, reverse=True)
            active_branches = active_branches[: self.top_k]

            best_response = active_branches[0].response

            await agent._emit(
                AgentEvent.STEP_FINISHED,
                {
                    "step": f"tot_depth_{depth}",
                    "best_score": active_branches[0].score,
                    "pruned_to": len(active_branches),
                },
            )

            logger.info(
                "ToT depth %d: best score %.1f, kept %d branches",
                depth,
                active_branches[0].score,
                len(active_branches),
            )

            # If at final depth, break early (no need to continue)
            if depth == self.max_depth - 1:
                break

            # Expand remaining branches for next depth
            expanded: list[ThoughtBranch] = []
            for branch in active_branches:
                for j in range(self.branches):
                    expanded.append(ThoughtBranch(depth=depth + 1, messages=list(branch.messages)))
            active_branches = expanded

        await agent._emit(
            AgentEvent.STEP_FINISHED,
            {"step": "tot_complete", "final_score": active_branches[0].score},
        )

        # Update the original messages with the best path
        if best_response and best_response.content:
            messages.append(best_response)
            return best_response

        # Fallback: direct LLM call if ToT produced no text content
        return await agent._call_llm(messages, [])

    async def _generate_branch(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        branch_idx: int,
        depth: int,
    ) -> ChatMessage:
        """Generate a single branch response.

        Tools are excluded from branch generation to force text-only reasoning.
        If tools were passed, the LLM might return tool_calls with no content,
        which breaks scoring and produces empty responses.
        """
        try:
            return await agent._call_llm(messages, [])
        except Exception:
            logger.exception("Branch %d at depth %d failed", branch_idx, depth)
            return ChatMessage.assistant(content="[Branch generation failed]")

    async def _score_branches(
        self,
        agent: BaseAgent,
        branches: list[ThoughtBranch],
    ) -> None:
        """Score all branches in parallel using LLM self-evaluation."""
        tasks = [self._evaluate_branch(agent, branch) for branch in branches]
        scores = await asyncio.gather(*tasks)
        for branch, score in zip(branches, scores):
            branch.score = score

    async def _evaluate_branch(
        self,
        agent: BaseAgent,
        branch: ThoughtBranch,
    ) -> float:
        """Evaluate a single branch and return a score (1-10)."""
        if not branch.response or not branch.response.content:
            return 0.0

        eval_text = self.eval_prompt.format(reasoning=branch.response.content[:1000])
        eval_messages = [ChatMessage.user(eval_text)]

        try:
            eval_response = await agent._call_llm(eval_messages, [])
            return parse_score(eval_response.content or "")
        except Exception:
            logger.exception("Branch evaluation failed")
            return 0.0
