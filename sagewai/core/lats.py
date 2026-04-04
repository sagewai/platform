# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Language Agent Tree Search (LATS) execution strategy.

MCTS-inspired search over agent reasoning trajectories with tool use,
LLM self-evaluation, and reflective backtracking.

At each step the strategy:

1. **Select** the most promising node via UCT (Upper Confidence Bound for Trees).
2. **Expand** by generating ``n_samples`` candidate actions (LLM calls that may
   include tool use).
3. **Evaluate** each candidate with an LLM scoring call.
4. **Backpropagate** scores up the tree to inform future selection.

After ``max_iterations`` search steps (or when the best trajectory is
considered complete), the highest-value leaf is returned as the final response.

Usage::

    from sagewai.core.lats import LATSStrategy

    agent = UniversalAgent(
        name="Searcher",
        model="gpt-4o",
        strategy=LATSStrategy(n_samples=3, max_depth=4),
    )

Reference: Zhou et al., "Language Agent Tree Search Unifies Reasoning Acting
and Planning in Language Models" (2023).
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent

logger = logging.getLogger(__name__)

_EVAL_PROMPT = (
    "Evaluate the following agent trajectory on a scale of 1-10 for progress "
    "toward solving the original task. Consider correctness, completeness, and "
    "whether the approach is on the right track.\n\n"
    "Original task:\n{task}\n\n"
    "Agent trajectory:\n{trajectory}\n\n"
    "Score (1-10):"
)

_REFLECT_PROMPT = (
    "The following approach did not score well. Analyze what went wrong and "
    "suggest a better strategy.\n\n"
    "Task: {task}\n\n"
    "Failed trajectory:\n{trajectory}\n\n"
    "Score: {score}/10\n\n"
    "Reflection (be specific about what to do differently):"
)


@dataclass
class LATSNode:
    """A node in the LATS search tree.

    Each node represents a state in the agent's reasoning trajectory.
    """

    id: int
    parent: LATSNode | None = None
    children: list[LATSNode] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)
    response: ChatMessage | None = None
    value: float = 0.0
    visits: int = 0
    depth: int = 0
    is_terminal: bool = False
    reflection: str | None = None

    def uct_score(self, exploration_weight: float = 1.41) -> float:
        """Upper Confidence Bound for Trees (UCT) selection score."""
        if self.visits == 0:
            return float("inf")
        if self.parent is None or self.parent.visits == 0:
            return self.value / self.visits
        exploitation = self.value / self.visits
        exploration = exploration_weight * math.sqrt(math.log(self.parent.visits) / self.visits)
        return exploitation + exploration

    @property
    def average_value(self) -> float:
        """Average value across all visits."""
        return self.value / self.visits if self.visits > 0 else 0.0


class LATSStrategy:
    """Language Agent Tree Search execution strategy.

    Combines Monte Carlo Tree Search with LLM-based evaluation and
    reflection to systematically explore reasoning trajectories.

    Parameters
    ----------
    n_samples:
        Number of candidate actions to generate at each expansion.
    max_depth:
        Maximum depth of the search tree.
    max_iterations:
        Maximum number of MCTS iterations (select-expand-evaluate-backprop
        cycles).  This overrides the agent's ``max_iterations`` for the
        outer search loop; each inner ReAct step still respects tool limits.
    exploration_weight:
        UCT exploration constant (higher = more exploration).
    reflection_threshold:
        Nodes scoring below this trigger a reflection step.
    eval_prompt:
        Template for trajectory evaluation. Must contain ``{task}`` and
        ``{trajectory}``.
    reflect_prompt:
        Template for reflection on low-scoring trajectories. Must contain
        ``{task}``, ``{trajectory}``, and ``{score}``.
    """

    def __init__(
        self,
        *,
        n_samples: int = 3,
        max_depth: int = 4,
        max_iterations: int = 8,
        exploration_weight: float = 1.41,
        reflection_threshold: float = 4.0,
        eval_prompt: str | None = None,
        reflect_prompt: str | None = None,
    ) -> None:
        self.n_samples = max(2, n_samples)
        self.max_depth = max(1, max_depth)
        self.max_iterations = max(1, max_iterations)
        self.exploration_weight = exploration_weight
        self.reflection_threshold = reflection_threshold
        self.eval_prompt = eval_prompt or _EVAL_PROMPT
        self.reflect_prompt = reflect_prompt or _REFLECT_PROMPT
        self._node_counter = 0

    async def execute(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_iterations: int,
    ) -> ChatMessage:
        """Run LATS and return the best trajectory's final response."""
        self._node_counter = 0

        # Extract the original task from user messages
        task = self._extract_task(messages)

        # Create root node
        root = self._create_node(parent=None, messages=list(messages))

        await agent._emit(
            AgentEvent.STEP_STARTED,
            {
                "step": "lats_start",
                "n_samples": self.n_samples,
                "max_depth": self.max_depth,
                "max_iterations": self.max_iterations,
            },
        )

        best_node: LATSNode | None = None

        for iteration in range(self.max_iterations):
            await agent._emit(
                AgentEvent.STEP_STARTED,
                {"step": f"lats_iter_{iteration}", "tree_size": self._node_counter},
            )

            # 1. SELECT — find the most promising leaf
            leaf = self._select(root)

            # If leaf is at max depth, mark as terminal and backprop
            if leaf.depth >= self.max_depth:
                leaf.is_terminal = True
                self._backpropagate(leaf, leaf.average_value)
                await agent._emit(
                    AgentEvent.STEP_FINISHED,
                    {"step": f"lats_iter_{iteration}", "action": "max_depth_reached"},
                )
                continue

            # 2. EXPAND — generate n_samples candidate actions
            children = await self._expand(agent, leaf, tools, task)

            if not children:
                leaf.is_terminal = True
                self._backpropagate(leaf, leaf.average_value)
                await agent._emit(
                    AgentEvent.STEP_FINISHED,
                    {"step": f"lats_iter_{iteration}", "action": "no_expansion"},
                )
                continue

            # 3. EVALUATE — score each child
            scores = await self._evaluate_nodes(agent, children, task)

            for child, score in zip(children, scores):
                child.value = score
                child.visits = 1

                # Check if this is a terminal (no tool calls = final answer)
                if child.response and not child.response.tool_calls:
                    child.is_terminal = True

                # 4. BACKPROPAGATE
                self._backpropagate(child, score)

            # 5. REFLECT on low-scoring nodes
            for child, score in zip(children, scores):
                if score < self.reflection_threshold and not child.is_terminal:
                    child.reflection = await self._reflect(agent, child, task, score)

            # Track best terminal node
            for child in children:
                if child.is_terminal:
                    if best_node is None or child.average_value > best_node.average_value:
                        best_node = child

            await agent._emit(
                AgentEvent.STEP_FINISHED,
                {
                    "step": f"lats_iter_{iteration}",
                    "expanded": len(children),
                    "best_score": max(scores) if scores else 0.0,
                },
            )

            logger.info(
                "LATS iter %d: expanded %d children, best score %.1f",
                iteration,
                len(children),
                max(scores) if scores else 0.0,
            )

            # Early termination: high-confidence terminal node found
            if best_node and best_node.average_value >= 9.0:
                logger.info("LATS early stop: high-confidence node (%.1f)", best_node.average_value)
                break

        await agent._emit(
            AgentEvent.STEP_FINISHED,
            {
                "step": "lats_complete",
                "total_nodes": self._node_counter,
                "best_value": best_node.average_value if best_node else 0.0,
            },
        )

        # Return the best terminal node's response
        best = best_node
        if not best or not best.response:
            best = self._find_best_leaf(root)

        if best and best.response:
            # If the response has content, return it directly
            if best.response.content:
                messages.append(best.response)
                return best.response
            # Response was tool-call-only — synthesize a text answer from
            # the full trajectory so the caller gets usable content.
            messages.extend(best.messages[len(messages):])
            return await agent._call_llm(messages, [])

        # Last resort: direct LLM call
        return await agent._call_llm(messages, [])

    # ------------------------------------------------------------------
    # MCTS phases
    # ------------------------------------------------------------------

    def _select(self, node: LATSNode) -> LATSNode:
        """Select the most promising leaf node using UCT."""
        current = node
        while current.children and not current.is_terminal:
            current = max(
                current.children,
                key=lambda n: n.uct_score(self.exploration_weight),
            )
        return current

    async def _expand(
        self,
        agent: BaseAgent,
        node: LATSNode,
        tools: list[ToolSpec],
        task: str,
    ) -> list[LATSNode]:
        """Generate n_samples candidate child nodes."""
        children: list[LATSNode] = []

        # Build messages for expansion — include reflection if available
        base_messages = list(node.messages)
        if node.reflection:
            base_messages.append(
                ChatMessage.system(f"[Reflection from previous attempt]\n{node.reflection}")
            )

        async def generate_one(sample_idx: int) -> LATSNode | None:
            msgs = list(base_messages)
            if sample_idx > 0:
                # Add diversity prompt for non-first samples
                msgs.append(
                    ChatMessage.user(
                        f"Try a different approach (variation {sample_idx + 1}). "
                        "Consider alternative strategies or tools."
                    )
                )
            try:
                response = await agent._call_llm(msgs, tools)

                # If response has tool calls, execute them and get results
                child_messages = list(node.messages)
                child_messages.append(response)

                if response.tool_calls:
                    results = await asyncio.gather(
                        *(agent._execute_tool(tc) for tc in response.tool_calls)
                    )
                    for tc, result in zip(response.tool_calls, results):
                        child_messages.append(
                            ChatMessage.tool_result(
                                tool_call_id=result.tool_call_id,
                                name=result.name,
                                content=result.error or result.content,
                            )
                        )

                child = self._create_node(
                    parent=node,
                    messages=child_messages,
                )
                child.response = response
                return child
            except Exception:
                logger.exception("LATS expansion failed for sample %d", sample_idx)
                return None

        results = await asyncio.gather(*(generate_one(i) for i in range(self.n_samples)))

        for child in results:
            if child is not None:
                node.children.append(child)
                children.append(child)

        return children

    async def _evaluate_nodes(
        self,
        agent: BaseAgent,
        nodes: list[LATSNode],
        task: str,
    ) -> list[float]:
        """Score multiple nodes in parallel."""
        tasks = [self._evaluate_single(agent, node, task) for node in nodes]
        return list(await asyncio.gather(*tasks))

    async def _evaluate_single(
        self,
        agent: BaseAgent,
        node: LATSNode,
        task: str,
    ) -> float:
        """Evaluate a single node's trajectory."""
        trajectory = self._format_trajectory(node)
        if not trajectory:
            return 0.0

        eval_text = self.eval_prompt.format(task=task, trajectory=trajectory[:2000])
        eval_messages = [ChatMessage.user(eval_text)]

        try:
            response = await agent._call_llm(eval_messages, [])
            return self._parse_score(response.content or "")
        except Exception:
            logger.exception("LATS evaluation failed")
            return 0.0

    def _backpropagate(self, node: LATSNode, value: float) -> None:
        """Propagate a value score up to the root."""
        current: LATSNode | None = node
        while current is not None:
            current.visits += 1
            current.value += value
            current = current.parent

    async def _reflect(
        self,
        agent: BaseAgent,
        node: LATSNode,
        task: str,
        score: float,
    ) -> str:
        """Generate a reflection on a low-scoring trajectory."""
        trajectory = self._format_trajectory(node)
        reflect_text = self.reflect_prompt.format(
            task=task,
            trajectory=trajectory[:2000],
            score=f"{score:.1f}",
        )
        reflect_messages = [ChatMessage.user(reflect_text)]

        try:
            response = await agent._call_llm(reflect_messages, [])
            return response.content or ""
        except Exception:
            logger.exception("LATS reflection failed")
            return ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_node(
        self,
        parent: LATSNode | None,
        messages: list[ChatMessage],
    ) -> LATSNode:
        """Create a new tree node."""
        self._node_counter += 1
        depth = parent.depth + 1 if parent else 0
        return LATSNode(
            id=self._node_counter,
            parent=parent,
            messages=messages,
            depth=depth,
        )

    def _find_best_leaf(self, root: LATSNode) -> LATSNode:
        """Find the leaf with highest average value in the tree."""
        best = root
        stack = [root]
        while stack:
            node = stack.pop()
            if node.average_value > best.average_value and node.response is not None:
                best = node
            stack.extend(node.children)
        return best

    @staticmethod
    def _extract_task(messages: list[ChatMessage]) -> str:
        """Extract the original user task from message history."""
        for msg in messages:
            if msg.role.value == "user" and msg.content:
                return msg.content
        return ""

    @staticmethod
    def _format_trajectory(node: LATSNode) -> str:
        """Format a node's message history as a readable trajectory."""
        parts: list[str] = []
        for msg in node.messages:
            if msg.role.value == "system":
                continue
            prefix = msg.role.value.upper()
            if msg.content:
                parts.append(f"{prefix}: {msg.content[:500]}")
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    parts.append(f"TOOL CALL: {tc.name}({tc.arguments})")
        return "\n".join(parts)

    @staticmethod
    def _parse_score(text: str) -> float:
        """Extract a numeric score (1-10) from LLM evaluation response."""
        text = text.strip()
        for token in text.split():
            cleaned = token.strip(".,;:()[]")
            try:
                score = float(cleaned)
                return max(1.0, min(10.0, score))
            except ValueError:
                continue
        return 5.0
