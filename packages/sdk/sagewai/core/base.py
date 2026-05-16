# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""BaseAgent — Abstract foundation for all Sagewai agents."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from sagewai.admin.controller import AgentCancelledError, RunController
from sagewai.core.compactor import PromptCompactor, estimate_messages_tokens
from sagewai.core.events import AgentEvent
from sagewai.core.rate_limiter import RateLimiter
from sagewai.core.strategies import ExecutionStrategy, ReActStrategy
from sagewai.errors import SagewaiBudgetExceededError
from sagewai.models.agent import AgentConfig
from sagewai.models.message import ChatMessage, ToolCall
from sagewai.models.tool import ToolResult, ToolSpec
from sagewai.safety.guardrails import Guardrail, GuardrailViolationError

logger = logging.getLogger(__name__)

EventCallback = Callable[[AgentEvent, dict[str, Any]], Awaitable[None] | None]


class BaseAgent(ABC):
    """Abstract agent with an agentic tool-calling loop.

    Subclasses must implement ``_call_llm`` to integrate with a specific LLM provider.
    The reasoning loop is delegated to an :class:`ExecutionStrategy` (default:
    :class:`ReActStrategy`).
    """

    def __init__(
        self,
        name: str,
        model: str = "gpt-4o",
        system_prompt: str = "",
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_iterations: int = 10,
        strategy: ExecutionStrategy | None = None,
        memory: Any = None,
        memory_top_k: int = 5,
        guardrails: list[Guardrail] | None = None,
        max_context_tokens: int | None = None,
        compaction_strategy: str | None = None,
        rate_limiter: RateLimiter | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        custom_llm_provider: str | None = None,
        directives: Any = None,
        hook_runner: Any = None,
        permission_policy: Any = None,
        **kwargs: Any,
    ) -> None:
        tools = tools or []
        self.config = AgentConfig(
            name=name,
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            max_iterations=max_iterations,
            strategy=strategy or ReActStrategy(),
            memory=memory,
            memory_top_k=memory_top_k,
            directives=directives,
        )
        if api_base:
            self.config.inference.api_base = api_base
        if api_key:
            self.config.inference.api_key = api_key
        if custom_llm_provider:
            self.config.inference.custom_llm_provider = custom_llm_provider
        self._tool_registry: dict[str, ToolSpec] = {t.name: t for t in tools}
        self._event_listeners: list[EventCallback] = []
        self._run_controller: RunController | None = None
        self._guardrails: list[Guardrail] = guardrails or []
        self._max_context_tokens = max_context_tokens
        if max_context_tokens:
            self._compactor = self._create_compactor(
                compaction_strategy or "extractive", max_context_tokens
            )
        else:
            self._compactor = None
        self._rate_limiter = rate_limiter
        self._hook_runner = hook_runner
        self._permission_policy = permission_policy
        self._budget_manager: Any = None
        self._accumulated_cost: float = 0.0
        self._directive_engine: Any = None
        self._init_directive_engine()
        self._current_model_override: str | None = None
        self._current_budget_override: float | None = None
        self._turn_count: int = 0
        self._memory_bridge: Any = None
        if kwargs.get("auto_learn") and memory is not None:
            self.config.auto_learn = True
            if "learn_every_n_turns" in kwargs:
                self.config.learn_every_n_turns = kwargs["learn_every_n_turns"]
            try:
                from sagewai.context.memory_bridge import MemoryBridge

                self._memory_bridge = MemoryBridge(
                    context_engine=memory,
                    extract_every_n_turns=self.config.learn_every_n_turns,
                )
            except ImportError:
                logger.debug("MemoryBridge not available, auto_learn disabled")

    # ------------------------------------------------------------------
    # Compaction strategy factory
    # ------------------------------------------------------------------

    @staticmethod
    def _create_compactor(strategy: str, max_tokens: int) -> "PromptCompactor":
        """Create a compactor instance based on the named strategy."""
        if strategy == "rule":
            from sagewai.core.compactor import RuleBasedCompactor

            return RuleBasedCompactor(max_tokens=max_tokens)
        elif strategy == "llm":
            from sagewai.core.compactor import LLMCompactor

            return LLMCompactor(max_tokens=max_tokens)
        elif strategy == "extractive":
            return PromptCompactor(max_tokens=max_tokens)
        elif strategy == "pipeline":
            from sagewai.core.compactor import CompactionPipeline

            return CompactionPipeline(max_tokens=max_tokens)
        else:
            return PromptCompactor(max_tokens=max_tokens)

    # ------------------------------------------------------------------
    # Directive Engine initialization
    # ------------------------------------------------------------------

    def _init_directive_engine(self) -> None:
        """Initialize the Directive Engine if configured."""
        directives = self.config.directives
        if not directives:
            return

        from sagewai.directives.engine import DirectiveEngine

        if isinstance(directives, DirectiveEngine):
            self._directive_engine = directives
        elif directives is True:
            # Auto-create from agent's existing services
            tool_registry = getattr(self, "_tool_registry", None) or {
                t.name: t for t in self.config.tools
            }
            # Lazy proxy for agent registry — resolves agents at lookup time
            # so agents created after this one are still discoverable.
            # Uses the registry instance from the agent's own registration,
            # NOT the singleton (which may be a different instance).
            from sagewai.core.registry import AgentRegistry

            _self_name = self.config.name
            # Find the registry this agent is registered in
            _reg = AgentRegistry.get_instance()
            # Check if this agent exists in the singleton; if not, the app
            # may use a custom instance — store it for lazy lookup
            if hasattr(self, '_registry_ref'):
                _reg = self._registry_ref

            class _LazyAgentMap:
                """Mapping-like proxy that delegates to AgentRegistry at access time."""

                def __init__(self, registry):
                    self._reg = registry

                def get(self, key, default=None):
                    if key == _self_name:
                        return default
                    agent = self._reg.get(key)
                    return agent if agent is not None else default

                def __getitem__(self, key):
                    result = self.get(key)
                    if result is None:
                        raise KeyError(key)
                    return result

                def __contains__(self, key):
                    if key == _self_name:
                        return False
                    return self._reg.get(key) is not None

                def __bool__(self):
                    return True

                def __len__(self):
                    return len(self._reg.list_agents())

            self._directive_engine = DirectiveEngine(
                context=self.config.memory,
                tools=tool_registry,
                agents=_LazyAgentMap(_reg),
                model=self.config.model,
                max_context_tokens=getattr(self, "_max_context_tokens", None),
                resolution_timeout=120.0,  # 2 min — agent delegation can be slow with local models
            )

    # ------------------------------------------------------------------
    # Dynamic tool management
    # ------------------------------------------------------------------

    def add_tools(self, tools: list["ToolSpec"]) -> None:
        """Add tools to the agent's registry after construction."""
        for tool in tools:
            self._tool_registry[tool.name] = tool
            if tool not in self.config.tools:
                self.config.tools.append(tool)

    # ------------------------------------------------------------------
    # Event hooks
    # ------------------------------------------------------------------

    def on_event(self, callback: EventCallback) -> None:
        """Register a listener for agent lifecycle events.

        The callback receives ``(event_type, data)`` where *event_type* is an
        :class:`AgentEvent` and *data* is a dict with event-specific payload.
        Both sync and async callbacks are supported.
        """
        self._event_listeners.append(callback)

    async def _emit(self, event: AgentEvent, data: dict[str, Any] | None = None) -> None:
        """Fire an event to all registered listeners.

        Errors in individual listeners are logged and swallowed so that a
        faulty listener never disrupts the agent loop.
        """
        if not self._event_listeners:
            return
        payload = data or {}
        for listener in self._event_listeners:
            try:
                result = listener(event, payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Event listener error for %s", event.value)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(self, message: str) -> str:
        """Send a single user message and return the agent's text response.

        This is the primary public interface. It builds a minimal conversation
        (system prompt + user message), runs the agent loop, and returns the
        final assistant text.
        """
        messages = await self._build_messages(message)

        await self._emit(AgentEvent.RUN_STARTED, {"agent": self.config.name, "input": message})
        try:
            await self._check_input_guardrails(message)
            result = await self._agent_loop(messages)
            # Store messages from last run for post-run inspection (usage, etc.)
            self._last_run_messages = messages
            result_text = result.content or ""
            await self._check_output_guardrails(result_text)
            await self._emit(
                AgentEvent.RUN_FINISHED,
                {"agent": self.config.name, "result": result_text},
            )
            # Auto-learn: extract facts from conversation in background
            self._turn_count += 1
            if self._memory_bridge and self._memory_bridge.should_extract(self._turn_count):
                task = asyncio.create_task(
                    self._auto_extract_memories(messages),
                    name=f"auto_learn_{self.config.name}_{self._turn_count}",
                )
                task.add_done_callback(self._on_auto_learn_done)
            return result_text
        except AgentCancelledError:
            await self._emit(
                AgentEvent.RUN_CANCELLED,
                {"agent": self.config.name},
            )
            raise
        except Exception as exc:
            await self._emit(
                AgentEvent.RUN_ERROR,
                {"agent": self.config.name, "error": str(exc)},
            )
            raise

    async def chat_with_history(self, messages: list[ChatMessage]) -> ChatMessage:
        """Run the agent loop with an explicit conversation history.

        Useful for multi-turn conversations where the caller manages state.
        """
        msgs = list(messages)
        if self.config.memory:
            await self._inject_memory_context(msgs)

        msgs = await self._auto_compact(msgs)

        # Extract the last user message for guardrail checks
        last_user_msg = ""
        for m in reversed(msgs):
            if m.role == "user":
                last_user_msg = m.content or ""
                break

        await self._emit(AgentEvent.RUN_STARTED, {"agent": self.config.name})
        try:
            if last_user_msg:
                await self._check_input_guardrails(last_user_msg)
            result = await self._agent_loop(msgs)
            result_text = result.content or ""
            await self._check_output_guardrails(result_text)
            await self._emit(
                AgentEvent.RUN_FINISHED,
                {"agent": self.config.name, "result": result_text},
            )
            return result
        except AgentCancelledError:
            await self._emit(
                AgentEvent.RUN_CANCELLED,
                {"agent": self.config.name},
            )
            raise
        except Exception as exc:
            await self._emit(
                AgentEvent.RUN_ERROR,
                {"agent": self.config.name, "error": str(exc)},
            )
            raise

    async def chat_stream(self, message: str) -> AsyncGenerator[str, None]:
        """Stream text chunks for a single user message.

        Handles tool calls internally — when the LLM requests tool calls,
        they are executed and the loop continues, streaming the next response.
        Only text content is yielded to the caller.
        """
        await self._check_input_guardrails(message)
        messages = await self._build_messages(message)

        await self._emit(AgentEvent.RUN_STARTED, {"agent": self.config.name, "input": message})
        try:
            collected: list[str] = []
            async for chunk in self._stream_agent_loop(messages):
                collected.append(chunk)
                yield chunk
            full_response = "".join(collected)
            await self._check_output_guardrails(full_response)
            await self._emit(AgentEvent.RUN_FINISHED, {"agent": self.config.name})
        except Exception as exc:
            await self._emit(
                AgentEvent.RUN_ERROR,
                {"agent": self.config.name, "error": str(exc)},
            )
            raise

    # ------------------------------------------------------------------
    # Memory helpers
    # ------------------------------------------------------------------

    async def _build_messages(self, message: str) -> list[ChatMessage]:
        """Build the initial message list with system prompt and memory context.

        When the Directive Engine is active, directives in the system prompt
        (``{{ }}`` templates) and user message (``@``/``/``/``#`` sigils) are
        resolved before the LLM sees the prompt. This replaces the legacy
        memory injection path.
        """
        messages: list[ChatMessage] = []

        # Reset per-turn overrides so they don't persist across turns
        self._current_model_override = None
        self._current_budget_override = None

        logger.info(
            "[DIRECTIVE DEBUG] _build_messages called. engine=%s, message_preview=%s",
            self._directive_engine is not None,
            message[:80] if message else "(empty)",
        )

        if self._directive_engine:
            # Resolve developer templates in system prompt
            if self.config.system_prompt:
                resolved_sys = await self._directive_engine.resolve_template(
                    self.config.system_prompt
                )
                messages.append(ChatMessage.system(resolved_sys.prompt))

            # Resolve user sigil directives
            logger.info("[DIRECTIVE DEBUG] Resolving user directives...")
            try:
                resolved_user = await self._directive_engine.resolve(message)
                logger.info(
                    "[DIRECTIVE DEBUG] Resolved: clean_prompt=%s, context_blocks=%d, overrides=%s",
                    resolved_user.clean_prompt[:80] if resolved_user.clean_prompt else "(empty)",
                    len(resolved_user.context_blocks),
                    resolved_user.overrides,
                )
            except Exception as exc:
                logger.error("[DIRECTIVE DEBUG] resolve() FAILED: %s", exc, exc_info=True)
                resolved_user = None

            if resolved_user is None:
                # Fallback if resolution failed
                if self.config.system_prompt:
                    messages.clear()
                    messages.append(ChatMessage.system(self.config.system_prompt))
                messages.append(ChatMessage.user(message))
                return messages

            # Inject resolved context as system messages (KV-cache friendly:
            # static system prompt first, then semi-static context, then user msg)
            for block in resolved_user.context_blocks:
                messages.append(ChatMessage.system(block.content))

            # Inject tool descriptions for prompt-based calling (small models)
            if resolved_user.tool_descriptions:
                messages.append(ChatMessage.system(resolved_user.tool_descriptions))

            messages.append(ChatMessage.user(resolved_user.clean_prompt))

            # Apply execution overrides from # meta-directives
            if resolved_user.overrides:
                overrides = resolved_user.overrides
                if overrides.model:
                    self._current_model_override = overrides.model
                    logger.info("Directive override: model=%s", overrides.model)
                if overrides.budget is not None:
                    self._current_budget_override = overrides.budget
                    logger.info("Directive override: budget=%s", overrides.budget)
                await self._emit(
                    AgentEvent.PROMPT_LOGGED,
                    {"directive_overrides": overrides.model_dump(exclude_none=True)},
                )

            return messages

        # Legacy path: no directive engine
        if self.config.system_prompt:
            messages.append(ChatMessage.system(self.config.system_prompt))
        messages.append(ChatMessage.user(message))

        if self.config.memory:
            await self._inject_memory_context(messages, query=message)

        return messages

    async def _inject_memory_context(
        self,
        messages: list[ChatMessage],
        query: str | None = None,
    ) -> None:
        """Retrieve relevant context from memory and inject as a system message.

        The context is added after the existing system prompt but before user
        messages, so the LLM sees it as additional instructions.
        """
        memory = self.config.memory
        if memory is None:
            return

        # Determine search query: use provided query or last user message
        if query is None:
            for msg in reversed(messages):
                if msg.role.value == "user" and msg.content:
                    query = msg.content
                    break
        if not query:
            return

        try:
            context_items = await memory.retrieve(query, top_k=self.config.memory_top_k)
        except Exception:
            logger.exception("Memory retrieval failed for agent %s", self.config.name)
            return

        if not context_items:
            return

        context_text = "\n\n".join(context_items)
        context_msg = ChatMessage.system(f"[Relevant context from memory]\n{context_text}")

        # Insert after existing system prompt(s), before user messages
        insert_idx = 0
        for i, msg in enumerate(messages):
            if msg.role.value == "system":
                insert_idx = i + 1
            else:
                break
        messages.insert(insert_idx, context_msg)

    @staticmethod
    def _on_auto_learn_done(task: asyncio.Task) -> None:
        """Log exceptions from background auto-learn tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("Auto-learn task %s failed: %s", task.get_name(), exc)

    async def _auto_extract_memories(self, messages: list[ChatMessage]) -> None:
        """Background task: extract facts from conversation via MemoryBridge."""
        try:
            from sagewai.context.models import ContextScope

            from sagewai.core.context import resolve_project_id

            await self._memory_bridge.extract_from_conversation(
                messages=messages,
                scope=ContextScope.PROJECT,
                scope_id=resolve_project_id(),
            )
        except (ImportError, OSError, RuntimeError, ValueError, ConnectionError):
            logger.debug("Auto memory extraction failed", exc_info=True)

    # ------------------------------------------------------------------
    # Abstract — must be implemented by engine subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    async def _invoke_llm(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        model_override: str | None = None,
    ) -> ChatMessage:
        """Engine-specific LLM call. Subclasses must implement this.

        Args:
            model_override: If set, use this model instead of ``self.config.model``.
                Used by budget throttling to temporarily downgrade to a cheaper model
                without mutating shared agent state.

        Returns a ChatMessage which may contain text content, tool_calls, or both.
        """
        ...

    async def _check_budget_pre_call(self) -> str | None:
        """Run pre-call budget check. Returns fallback model or None."""
        bm = self._budget_manager
        if bm is None:
            return None

        try:
            result = bm.check_budget(self.config.name)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.exception("Budget check failed for %s", self.config.name)
            return None

        # Normalize dict (PostgresBudgetManager) to attribute access
        allowed = result.get("allowed") if isinstance(result, dict) else result.allowed
        action = result.get("action") if isinstance(result, dict) else result.action
        reason = (
            result.get("reason", "") if isinstance(result, dict) else (result.reason or "")
        )

        if allowed or action == "allow":
            return None

        if action == "stop":
            await self._emit(AgentEvent.BUDGET_EXCEEDED, {
                "agent": self.config.name,
                "reason": reason,
            })
            raise SagewaiBudgetExceededError(
                f"Budget exceeded for agent '{self.config.name}': {reason}",
                agent_name=self.config.name,
                reason=reason,
            )

        if action == "throttle":
            try:
                fallback_result = bm.get_fallback_model(
                    self.config.name, self.config.model
                )
                if inspect.isawaitable(fallback_result):
                    fallback_result = await fallback_result
            except Exception:
                logger.exception(
                    "get_fallback_model failed for %s", self.config.name
                )
                fallback_result = None

            if fallback_result:
                await self._emit(AgentEvent.BUDGET_THROTTLED, {
                    "agent": self.config.name,
                    "reason": reason,
                    "original_model": self.config.model,
                    "fallback_model": fallback_result,
                })
                return fallback_result

            # No fallback available — emit warning and continue
            await self._emit(AgentEvent.BUDGET_WARNING, {
                "agent": self.config.name,
                "reason": reason,
            })
            return None

        if action == "warn":
            await self._emit(AgentEvent.BUDGET_WARNING, {
                "agent": self.config.name,
                "reason": reason,
            })
            return None

        return None

    async def _record_budget_spend(self, cost: float) -> None:
        """Record spend with the budget manager after an LLM call."""
        bm = self._budget_manager
        if bm is None or cost <= 0:
            return
        try:
            result = bm.record_spend(agent_name=self.config.name, cost_usd=cost)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Budget record_spend failed for %s", self.config.name)

    async def _call_llm(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
    ) -> ChatMessage:
        """Instrumented LLM call — delegates to ``_invoke_llm`` and emits usage telemetry.

        All strategies should call this method (not ``_invoke_llm`` directly) so
        that every LLM call is automatically tracked with token counts, cost,
        and duration.
        """
        from sagewai.observability.costs import calculate_cost

        if self._rate_limiter:
            await self._rate_limiter.check_llm()

        # Pre-call budget enforcement — returns fallback model or None
        fallback_model = await self._check_budget_pre_call()
        # Directive engine model override takes precedence over budget fallback
        model_override = self._current_model_override or fallback_model
        effective_model = model_override or self.config.model

        t0 = time.perf_counter()
        response = await self._invoke_llm(messages, tools, model_override=model_override)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        input_tokens = 0
        output_tokens = 0
        cost = 0.0
        if response.usage:
            response.usage.model = effective_model
            response.usage.duration_ms = elapsed_ms
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = calculate_cost(input_tokens, output_tokens, effective_model)
            self._accumulated_cost += cost

        await self._emit(
            AgentEvent.LLM_CALL_FINISHED,
            {
                "agent": self.config.name,
                "model": effective_model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
                "duration_ms": elapsed_ms,
            },
        )

        # Post-call budget spend recording
        await self._record_budget_spend(cost)

        return response

    async def _stream_llm(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        model_override: str | None = None,
    ) -> AsyncGenerator[str | ToolCall, None]:
        """Stream LLM response chunks.

        Yields ``str`` for text content and :class:`ToolCall` objects when the
        LLM requests tool invocations (emitted after all text chunks).

        Args:
            model_override: If set, use this model instead of ``self.config.model``.
                Passed through from budget throttling in ``_stream_agent_loop``.

        The default implementation falls back to a non-streaming ``_call_llm``
        call, yielding the full response at once.  Engine subclasses should
        override this for true token-level streaming.
        """
        response = await self._call_llm(messages, tools)
        if response.content:
            yield response.content
        if response.tool_calls:
            for tc in response.tool_calls:
                yield tc

    # ------------------------------------------------------------------
    # Agent loop
    # ------------------------------------------------------------------

    async def _agent_loop(self, messages: list[ChatMessage]) -> ChatMessage:
        """Delegate to the configured :class:`ExecutionStrategy`."""
        strategy: ExecutionStrategy = self.config.strategy
        return await strategy.execute(
            agent=self,
            messages=messages,
            tools=self.config.tools,
            max_iterations=self.config.max_iterations,
        )

    async def _stream_agent_loop(self, messages: list[ChatMessage]) -> AsyncGenerator[str, None]:
        """Streaming agent loop: stream text, execute tools, repeat."""
        tools = self.config.tools
        message_counter = 0

        for iteration in range(self.config.max_iterations):
            # Check for pause/cancel signals from admin controls
            if self._run_controller is not None:
                await self._run_controller.checkpoint()

            await self._emit(AgentEvent.STEP_STARTED, {"step": f"iteration_{iteration}"})
            collected_text = ""
            collected_tool_calls: list[ToolCall] = []
            msg_id = f"msg_{message_counter}"
            text_started = False

            # Pre-call budget enforcement for streaming
            fallback_model = await self._check_budget_pre_call()

            async for event in self._stream_llm(
                messages, tools, model_override=fallback_model
            ):
                if isinstance(event, str):
                    if not text_started:
                        await self._emit(AgentEvent.TEXT_MESSAGE_START, {"message_id": msg_id})
                        text_started = True
                    collected_text += event
                    await self._emit(
                        AgentEvent.TEXT_MESSAGE_CONTENT,
                        {"message_id": msg_id, "delta": event},
                    )
                    yield event
                elif isinstance(event, ToolCall):
                    collected_tool_calls.append(event)

            if text_started:
                await self._emit(AgentEvent.TEXT_MESSAGE_END, {"message_id": msg_id})
                message_counter += 1

            # Build full response for conversation history
            response = ChatMessage.assistant(
                content=collected_text or None,
                tool_calls=collected_tool_calls or None,
            )
            messages.append(response)

            if not collected_tool_calls:
                await self._emit(AgentEvent.STEP_FINISHED, {"step": f"iteration_{iteration}"})
                return

            # Emit tool call events and execute tools
            for tc in collected_tool_calls:
                await self._emit(
                    AgentEvent.TOOL_CALL_START,
                    {"tool_call_id": tc.id, "tool_name": tc.name, "arguments": tc.arguments},
                )

            results = await asyncio.gather(*(self._execute_tool(tc) for tc in collected_tool_calls))
            for tc, result in zip(collected_tool_calls, results):
                await self._emit(AgentEvent.TOOL_CALL_END, {"tool_call_id": tc.id})
                await self._emit(
                    AgentEvent.TOOL_CALL_RESULT,
                    {
                        "tool_call_id": result.tool_call_id,
                        "tool_name": result.name,
                        "content": result.content,
                        "error": result.error,
                    },
                )
                messages.append(
                    ChatMessage.tool_result(
                        tool_call_id=result.tool_call_id,
                        name=result.name,
                        content=result.error or result.content,
                    )
                )

            await self._emit(AgentEvent.STEP_FINISHED, {"step": f"iteration_{iteration}"})

        logger.warning(
            "Agent %s hit max_iterations (%d) during streaming",
            self.config.name,
            self.config.max_iterations,
        )

    async def _execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """Look up a tool by name and execute it.

        Checks permission policy first (declarative, no I/O), then runs
        pre-tool hooks (imperative, may do I/O). After execution, runs
        post-tool hooks which may modify the result.
        """
        if self._rate_limiter:
            await self._rate_limiter.check_tool()

        spec = self._tool_registry.get(tool_call.name)
        if spec is None or spec.handler is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content="",
                error=f"Unknown tool: {tool_call.name}",
            )

        arguments = dict(tool_call.arguments)

        # Permission policy check (declarative, fast)
        if self._permission_policy is not None:
            perm_result = await self._permission_policy.check_and_approve(
                tool_call.name, arguments
            )
            if not perm_result.allowed:
                await self._emit(AgentEvent.PERMISSION_DENIED, {
                    "tool_name": tool_call.name,
                    "reason": perm_result.reason,
                })
                return ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    content="",
                    error=f"Permission denied: {perm_result.reason}",
                )

        # Pre-tool hooks (imperative, may modify args or deny)
        if self._hook_runner is not None and self._hook_runner.has_hooks:
            from sagewai.core.hooks import HookAction, HookContext

            hook_ctx = HookContext(
                tool_name=tool_call.name,
                arguments=arguments,
                agent_name=self.config.name,
            )
            hook_result = await self._hook_runner.run_pre_hooks(hook_ctx)
            if hook_result.action == HookAction.DENY:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    content="",
                    error=f"Blocked by hook: {hook_result.message}",
                )
            if hook_result.modified_arguments is not None:
                arguments = hook_result.modified_arguments

        try:
            result = spec.handler(**arguments)
            if asyncio.iscoroutine(result):
                result = await result
            content = result if isinstance(result, str) else json.dumps(result)
        except Exception as exc:
            logger.exception("Tool %s failed", tool_call.name)
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content="",
                error=str(exc),
            )

        # Post-tool hooks (may modify result)
        if self._hook_runner is not None and self._hook_runner.has_hooks:
            from sagewai.core.hooks import HookContext

            post_ctx = HookContext(
                tool_name=tool_call.name,
                arguments=arguments,
                agent_name=self.config.name,
            )
            post_result = await self._hook_runner.run_post_hooks(post_ctx, content)
            if post_result.modified_result is not None:
                content = post_result.modified_result

        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            content=content,
        )

    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> ToolResult:
        """Execute a named tool directly (public API for trigger dispatch)."""
        from sagewai.models.message import ToolCall

        tool_call = ToolCall(
            id=uuid.uuid4().hex[:12],
            name=tool_name,
            arguments=arguments or {},
        )
        return await self._execute_tool(tool_call)

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------

    async def _check_input_guardrails(self, message: str) -> None:
        """Run input guardrails. Raises GuardrailViolationError if blocked."""
        for guard in self._guardrails:
            result = await guard.check_input(
                message, {"cost_usd_so_far": self._accumulated_cost}
            )
            if not result.passed:
                if result.action == "escalate":
                    await self._emit(AgentEvent.GUARDRAIL_ESCALATION, {
                        "type": "input",
                        "violation": result.violation,
                        "guardrail": guard.__class__.__name__,
                    })
                    continue
                raise GuardrailViolationError(result)

    async def _check_output_guardrails(self, response: str) -> None:
        """Run output guardrails. Raises GuardrailViolationError if blocked."""
        for guard in self._guardrails:
            result = await guard.check_output(
                response, {"cost_usd_so_far": self._accumulated_cost}
            )
            if not result.passed:
                if result.action == "escalate":
                    await self._emit(AgentEvent.GUARDRAIL_ESCALATION, {
                        "type": "output",
                        "violation": result.violation,
                        "guardrail": guard.__class__.__name__,
                    })
                    continue
                raise GuardrailViolationError(result)

    async def save_session(
        self,
        messages: list[ChatMessage],
        session_id: str | None = None,
        stop_reason: str = "completed",
        session_store: Any = None,
    ) -> str:
        """Save conversation state as a checkpoint.

        Parameters
        ----------
        messages:
            The conversation messages to persist.
        session_id:
            Optional explicit session ID. Generated if omitted.
        stop_reason:
            Why the session ended (completed, max_turns, etc.).
        session_store:
            Store backend. Defaults to a file-based ``SessionStore``.

        Returns
        -------
        str
            The session ID that can be used to restore later.
        """
        from sagewai.core.session_store import (
            SessionCheckpoint,
            SessionStore,
        )

        checkpoint = SessionCheckpoint.create(
            agent_name=self.config.name,
            model=self.config.model,
            system_prompt=self.config.system_prompt,
            messages=[m.model_dump() for m in messages],
            session_id=session_id,
            token_count=estimate_messages_tokens(messages),
            turn_count=self._turn_count,
            accumulated_cost=self._accumulated_cost,
            stop_reason=stop_reason,
        )
        store = session_store or SessionStore()
        await store.save(checkpoint)
        await self._emit(AgentEvent.SESSION_SAVED, {
            "session_id": checkpoint.session_id,
            "turn_count": checkpoint.turn_count,
        })
        return checkpoint.session_id

    async def restore_session(
        self,
        session_id: str,
        session_store: Any = None,
    ) -> list[ChatMessage] | None:
        """Restore a conversation from a checkpoint.

        Parameters
        ----------
        session_id:
            The session ID to restore.
        session_store:
            Store backend. Defaults to a file-based ``SessionStore``.

        Returns
        -------
        list[ChatMessage] | None
            The restored messages, or ``None`` if not found.
        """
        from sagewai.core.session_store import SessionStore

        store = session_store or SessionStore()
        checkpoint = await store.load(session_id)
        if checkpoint is None:
            return None

        messages = [ChatMessage(**m) for m in checkpoint.messages]
        self._turn_count = checkpoint.turn_count
        self._accumulated_cost = checkpoint.accumulated_cost

        await self._emit(AgentEvent.SESSION_RESUMED, {
            "session_id": session_id,
            "turn_count": checkpoint.turn_count,
        })
        return messages

    async def _auto_compact(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Compact messages if they exceed max_context_tokens."""
        if not self._compactor or not self._max_context_tokens:
            return messages
        if not self._compactor.needs_compaction(messages):
            return messages
        # Use async compaction if available (e.g. LLMCompactor)
        if hasattr(self._compactor, "compact_async"):
            compacted = await self._compactor.compact_async(messages)
        else:
            compacted = self._compactor.compact(messages)
        await self._emit(AgentEvent.CONTEXT_COMPACTED, {
            "original_count": len(messages),
            "compacted_count": len(compacted),
            "original_tokens": estimate_messages_tokens(messages),
            "compacted_tokens": estimate_messages_tokens(compacted),
        })
        return compacted
