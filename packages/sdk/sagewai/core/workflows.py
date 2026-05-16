# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Deterministic workflow agent patterns for multi-step orchestration.

Workflow agents compose sub-agents into deterministic execution patterns
without requiring LLM calls at the orchestration level.  Each sub-agent
may itself use an LLM (via :class:`UniversalAgent`, etc.) or be another
workflow agent for nested composition.

Four patterns are provided:

- :class:`SequentialAgent` — run sub-agents one after another, piping output
  from each step as input to the next.
- :class:`ParallelAgent` — run sub-agents concurrently and merge their outputs.
- :class:`LoopAgent` — repeat a sub-agent until a condition is met or
  ``max_iterations`` is exhausted.
- :class:`ConditionalAgent` — route input to different agents based on a
  sync or async condition function.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING, Any

from sagewai.core.base import BaseAgent
from sagewai.core.durability import DurabilityMode
from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec

if TYPE_CHECKING:
    from sagewai.core.state import WorkflowStore

logger = logging.getLogger(__name__)


class SequentialAgent(BaseAgent):
    """Execute sub-agents in order, passing each output as input to the next.

    Usage::

        researcher = UniversalAgent(name="researcher", model="gpt-4o")
        writer = UniversalAgent(name="writer", model="gpt-4o")
        pipeline = SequentialAgent(name="pipeline", agents=[researcher, writer])
        result = await pipeline.chat("Write about quantum computing")
    """

    def __init__(
        self,
        name: str,
        agents: list[BaseAgent],
        *,
        durability: DurabilityMode = DurabilityMode.NONE,
        workflow_store: WorkflowStore | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, **kwargs)
        if not agents:
            raise ValueError("SequentialAgent requires at least one sub-agent")
        self.agents = agents
        self._durability = durability
        self._workflow_store = workflow_store
        self._last_run_id: str | None = None

    async def _invoke_llm(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        model_override: str | None = None,
    ) -> ChatMessage:
        raise NotImplementedError("SequentialAgent does not call LLMs directly")

    async def _agent_loop(self, messages: list[ChatMessage]) -> ChatMessage:
        """Run sub-agents sequentially, piping output → input."""
        user_messages = [m for m in messages if m.role.value == "user"]
        current_input = user_messages[-1].content if user_messages else ""

        if self._durability == DurabilityMode.CHECKPOINT:
            from sagewai.core.durability import DurableRunner

            runner = DurableRunner(store=self._workflow_store)
            result = await runner.run_sequential(
                self.agents, current_input or "", run_id=self._last_run_id
            )
            return ChatMessage.assistant(result)

        last_response = ChatMessage.assistant("")
        for agent in self.agents:
            result = await agent.chat(current_input or "")
            current_input = result
            last_response = ChatMessage.assistant(result)

        return last_response

    async def chat_stream(self, message: str) -> AsyncGenerator[str, None]:
        """Stream output from sequential sub-agents."""
        await self._check_input_guardrails(message)
        messages = await self._build_messages(message)

        await self._emit(
            AgentEvent.RUN_STARTED,
            {"agent": self.config.name, "input": message},
        )
        try:
            user_messages = [m for m in messages if m.role.value == "user"]
            current_input = user_messages[-1].content if user_messages else ""

            collected_all: list[str] = []
            for i, agent in enumerate(self.agents):
                await self._emit(
                    AgentEvent.STEP_STARTED,
                    {"step": f"agent_{i}_{agent.config.name}"},
                )
                collected: list[str] = []
                async for chunk in agent.chat_stream(current_input or ""):
                    collected.append(chunk)
                    yield chunk
                current_input = "".join(collected)
                collected_all.extend(collected)
                await self._emit(
                    AgentEvent.STEP_FINISHED,
                    {"step": f"agent_{i}_{agent.config.name}"},
                )

            full_response = "".join(collected_all)
            await self._check_output_guardrails(full_response)
            await self._emit(
                AgentEvent.RUN_FINISHED, {"agent": self.config.name}
            )
        except Exception as exc:
            await self._emit(
                AgentEvent.RUN_ERROR,
                {"agent": self.config.name, "error": str(exc)},
            )
            raise

    async def resume(self, run_id: str) -> str:
        """Resume a checkpointed sequential run from the last completed step.

        Args:
            run_id: The run ID to resume.

        Returns:
            Final output text.
        """
        self._last_run_id = run_id
        self._durability = DurabilityMode.CHECKPOINT
        try:
            return await self.chat("")
        finally:
            self._last_run_id = None


class ParallelAgent(BaseAgent):
    """Execute sub-agents concurrently and merge their outputs.

    By default, outputs are joined with newline separators.  A custom
    ``merge`` function can be provided for domain-specific merging.

    Usage::

        analyst = UniversalAgent(name="analyst", model="gpt-4o")
        critic = UniversalAgent(name="critic", model="gpt-4o")
        panel = ParallelAgent(name="panel", agents=[analyst, critic])
        result = await panel.chat("Evaluate this proposal")
    """

    def __init__(
        self,
        name: str,
        agents: list[BaseAgent],
        merge: Callable[[list[str]], str] | None = None,
        *,
        durability: DurabilityMode = DurabilityMode.NONE,
        workflow_store: WorkflowStore | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, **kwargs)
        if not agents:
            raise ValueError("ParallelAgent requires at least one sub-agent")
        self.agents = agents
        self._merge = merge or self._default_merge
        self._durability = durability
        self._workflow_store = workflow_store
        self._last_run_id: str | None = None

    async def _invoke_llm(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        model_override: str | None = None,
    ) -> ChatMessage:
        raise NotImplementedError("ParallelAgent does not call LLMs directly")

    async def _agent_loop(self, messages: list[ChatMessage]) -> ChatMessage:
        """Run all sub-agents concurrently, then merge results."""
        user_messages = [m for m in messages if m.role.value == "user"]
        user_input = user_messages[-1].content if user_messages else ""

        if self._durability == DurabilityMode.CHECKPOINT:
            from sagewai.core.durability import DurableRunner

            runner = DurableRunner(store=self._workflow_store)
            result = await runner.run_parallel(
                self.agents, user_input or "", merge=self._merge, run_id=self._last_run_id
            )
            return ChatMessage.assistant(result)

        results = await asyncio.gather(*(agent.chat(user_input or "") for agent in self.agents))
        merged = self._merge(list(results))
        return ChatMessage.assistant(merged)

    @staticmethod
    def _default_merge(results: list[str]) -> str:
        """Join results with double newlines."""
        return "\n\n".join(results)

    async def chat_stream(self, message: str) -> AsyncGenerator[str, None]:
        """Stream merged output from parallel sub-agents."""
        await self._check_input_guardrails(message)

        await self._emit(
            AgentEvent.RUN_STARTED,
            {"agent": self.config.name, "input": message},
        )
        try:
            user_messages = await self._build_messages(message)
            user_input = ""
            for m in user_messages:
                if m.role.value == "user":
                    user_input = m.content or ""

            results = await asyncio.gather(
                *(agent.chat(user_input) for agent in self.agents)
            )
            merged = self._merge(list(results))

            # Stream the merged result in chunks
            chunk_size = 100
            for i in range(0, len(merged), chunk_size):
                yield merged[i : i + chunk_size]

            await self._check_output_guardrails(merged)
            await self._emit(
                AgentEvent.RUN_FINISHED, {"agent": self.config.name}
            )
        except Exception as exc:
            await self._emit(
                AgentEvent.RUN_ERROR,
                {"agent": self.config.name, "error": str(exc)},
            )
            raise

    async def resume(self, run_id: str) -> str:
        """Resume a checkpointed parallel run."""
        self._last_run_id = run_id
        self._durability = DurabilityMode.CHECKPOINT
        try:
            return await self.chat("")
        finally:
            self._last_run_id = None


class LoopAgent(BaseAgent):
    """Repeat a sub-agent until a condition is met or max iterations reached.

    The ``should_stop`` callback receives the latest result string and the
    current iteration (0-indexed).  If not provided, the agent loops exactly
    ``max_iterations`` times (default 10, inherited from BaseAgent).

    Usage::

        refiner = UniversalAgent(name="refiner", model="gpt-4o")
        loop = LoopAgent(
            name="refinement-loop",
            agent=refiner,
            max_iterations=3,
            should_stop=lambda result, i: "DONE" in result,
        )
        result = await loop.chat("Improve this text iteratively")
    """

    def __init__(
        self,
        name: str,
        agent: BaseAgent,
        should_stop: Callable[[str, int], bool] | None = None,
        *,
        durability: DurabilityMode = DurabilityMode.NONE,
        workflow_store: WorkflowStore | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, **kwargs)
        self.agent = agent
        self._should_stop = should_stop
        self._durability = durability
        self._workflow_store = workflow_store
        self._last_run_id: str | None = None

    async def _invoke_llm(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        model_override: str | None = None,
    ) -> ChatMessage:
        raise NotImplementedError("LoopAgent does not call LLMs directly")

    async def _agent_loop(self, messages: list[ChatMessage]) -> ChatMessage:
        """Loop the sub-agent, feeding output back as input."""
        user_messages = [m for m in messages if m.role.value == "user"]
        current_input = user_messages[-1].content if user_messages else ""

        if self._durability == DurabilityMode.CHECKPOINT:
            from sagewai.core.durability import DurableRunner

            runner = DurableRunner(store=self._workflow_store)
            result = await runner.run_loop(
                self.agent,
                current_input or "",
                max_iterations=self.config.max_iterations,
                should_stop=self._should_stop,
                run_id=self._last_run_id,
            )
            return ChatMessage.assistant(result)

        last_result = ""
        for iteration in range(self.config.max_iterations):
            last_result = await self.agent.chat(current_input or "")

            if self._should_stop and self._should_stop(last_result, iteration):
                logger.debug(
                    "LoopAgent %s stopped at iteration %d",
                    self.config.name,
                    iteration,
                )
                break

            # Feed output back as next input
            current_input = last_result

        return ChatMessage.assistant(last_result)

    async def chat_stream(self, message: str) -> AsyncGenerator[str, None]:
        """Stream output from each loop iteration."""
        await self._check_input_guardrails(message)
        messages = await self._build_messages(message)

        await self._emit(
            AgentEvent.RUN_STARTED,
            {"agent": self.config.name, "input": message},
        )
        try:
            user_messages = [m for m in messages if m.role.value == "user"]
            current_input = (
                user_messages[-1].content if user_messages else ""
            )

            last_result = ""
            for iteration in range(self.config.max_iterations):
                await self._emit(
                    AgentEvent.STEP_STARTED,
                    {"step": f"iteration_{iteration}"},
                )
                collected: list[str] = []
                async for chunk in self.agent.chat_stream(
                    current_input or ""
                ):
                    collected.append(chunk)
                    yield chunk
                last_result = "".join(collected)
                await self._emit(
                    AgentEvent.STEP_FINISHED,
                    {"step": f"iteration_{iteration}"},
                )

                if self._should_stop and self._should_stop(
                    last_result, iteration
                ):
                    break
                current_input = last_result

            await self._check_output_guardrails(last_result)
            await self._emit(
                AgentEvent.RUN_FINISHED, {"agent": self.config.name}
            )
        except Exception as exc:
            await self._emit(
                AgentEvent.RUN_ERROR,
                {"agent": self.config.name, "error": str(exc)},
            )
            raise

    async def resume(self, run_id: str) -> str:
        """Resume a checkpointed loop run from the last completed iteration."""
        self._last_run_id = run_id
        self._durability = DurabilityMode.CHECKPOINT
        try:
            return await self.chat("")
        finally:
            self._last_run_id = None


class ConditionalAgent(BaseAgent):
    """Route input to different agents based on a condition.

    Supports rule-based conditions (keyword/regex match, field comparison)
    and LLM-based classification via an async router function.

    The *condition* callable receives the user input string and returns
    a branch key (``str``).  It may be synchronous or asynchronous — if
    it returns a coroutine the agent will ``await`` it automatically.

    Usage::

        router = ConditionalAgent(
            name="sentiment-router",
            condition=lambda result: (
                "negative" if "bad" in result.lower() else "positive"
            ),
            branches={
                "negative": escalation_agent,
                "positive": auto_response_agent,
            },
            default_branch=auto_response_agent,
        )
        result = await router.chat("This product is terrible!")
    """

    def __init__(
        self,
        name: str,
        condition: Callable[[str], str],
        branches: dict[str, BaseAgent],
        default_branch: BaseAgent | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, **kwargs)
        self._condition = condition
        self._branches = branches
        self._default = default_branch

    async def _invoke_llm(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        model_override: str | None = None,
    ) -> ChatMessage:
        raise NotImplementedError(
            "ConditionalAgent does not call LLMs directly"
        )

    async def _resolve_branch_key(self, user_input: str) -> str:
        """Evaluate the condition, handling both sync and async callables."""
        result = self._condition(user_input)
        if asyncio.iscoroutine(result):
            return await result
        return result  # type: ignore[return-value]

    async def _agent_loop(
        self, messages: list[ChatMessage]
    ) -> ChatMessage:
        """Evaluate the condition and delegate to the matching branch."""
        user_messages = [m for m in messages if m.role.value == "user"]
        user_input = user_messages[-1].content if user_messages else ""

        branch_key = await self._resolve_branch_key(user_input or "")

        await self._emit(
            AgentEvent.ROUTE_SELECTED,
            {
                "branch": branch_key,
                "available": list(self._branches.keys()),
            },
        )

        agent = self._branches.get(branch_key, self._default)
        if agent is None:
            return ChatMessage.assistant(
                f"No branch matched condition result '{branch_key}' "
                f"and no default branch configured."
            )

        result = await agent.chat(user_input or "")
        return ChatMessage.assistant(result)

    async def chat_stream(
        self, message: str
    ) -> AsyncGenerator[str, None]:
        """Stream from the selected branch agent."""
        await self._check_input_guardrails(message)
        messages = await self._build_messages(message)

        await self._emit(
            AgentEvent.RUN_STARTED,
            {"agent": self.config.name, "input": message},
        )
        try:
            user_messages = [
                m for m in messages if m.role.value == "user"
            ]
            user_input = (
                user_messages[-1].content if user_messages else ""
            )

            branch_key = await self._resolve_branch_key(
                user_input or ""
            )
            await self._emit(
                AgentEvent.ROUTE_SELECTED,
                {
                    "branch": branch_key,
                    "available": list(self._branches.keys()),
                },
            )

            agent = self._branches.get(branch_key, self._default)
            if agent is None:
                msg = f"No branch matched '{branch_key}'"
                yield msg
                return

            collected: list[str] = []
            async for chunk in agent.chat_stream(user_input or ""):
                collected.append(chunk)
                yield chunk

            full = "".join(collected)
            await self._check_output_guardrails(full)
            await self._emit(
                AgentEvent.RUN_FINISHED,
                {"agent": self.config.name},
            )
        except Exception as exc:
            await self._emit(
                AgentEvent.RUN_ERROR,
                {"agent": self.config.name, "error": str(exc)},
            )
            raise
