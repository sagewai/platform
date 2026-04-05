# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Execution strategies for agent reasoning loops.

An ``ExecutionStrategy`` defines **how** an agent iterates between LLM calls
and tool execution.  The default :class:`ReActStrategy` implements the classic
Reason → Act → Observe cycle that was previously hard-wired into
:pymethod:`BaseAgent._agent_loop`.

Future strategies (Tree-of-Thoughts, LATS, Plan-and-Execute, …) can be added
by implementing the same protocol.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec
from sagewai.observability.costs import calculate_cost
from sagewai.observability.tracing import llm_span, tool_span

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent

logger = logging.getLogger(__name__)


@runtime_checkable
class ExecutionStrategy(Protocol):
    """Protocol that all execution strategies must satisfy.

    An execution strategy orchestrates the loop between LLM inference and
    tool execution.  Implementations receive an *agent* reference so they can
    call ``agent._call_llm()`` and ``agent._execute_tool()`` without
    duplicating provider-specific logic.
    """

    async def execute(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_iterations: int,
    ) -> ChatMessage:
        """Run the reasoning loop and return the final assistant message."""
        ...


class ReActStrategy:
    """Reason-Act-Observe loop (the default).

    1. Call the LLM with the current conversation + available tools.
    2. If the response contains tool calls → execute them, append results,
       and go back to step 1.
    3. If the response is pure text → return it as the final answer.
    4. If ``max_iterations`` is exhausted → return a guard message.

    Args:
        max_tool_calls_per_name: Maximum times any single tool can be called
            per run before it is removed from the tool list to prevent
            infinite retry loops.
        max_error_streak: Consecutive iterations where ALL tool calls fail
            before forcing a text-only response.
    """

    def __init__(
        self,
        *,
        max_tool_calls_per_name: int = 3,
        max_error_streak: int = 2,
    ) -> None:
        self.max_tool_calls_per_name = max_tool_calls_per_name
        self.max_error_streak = max_error_streak

    async def execute(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_iterations: int,
    ) -> ChatMessage:
        tool_call_counts: dict[str, int] = {}
        error_streak: int = 0
        available_tools = list(tools)

        for iteration in range(max_iterations):
            # Check for pause/cancel signals from admin controls
            if hasattr(agent, "_run_controller") and agent._run_controller is not None:
                await agent._run_controller.checkpoint()

            await agent._emit(AgentEvent.STEP_STARTED, {"step": f"iteration_{iteration}"})

            # LLM call with OTel span and usage tracking
            logger.info(
                "ReAct iteration %d/%d: %d tools available (%s), %d messages",
                iteration + 1,
                max_iterations,
                len(available_tools),
                [t.name for t in available_tools],
                len(messages),
            )
            t0 = time.perf_counter()
            with llm_span(agent.config.model) as span:
                # _call_llm emits LLM_CALL_FINISHED with usage automatically.
                response = await agent._call_llm(messages, available_tools)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            # Extract usage for OTel span and prompt log
            input_tokens = 0
            output_tokens = 0
            cost = 0.0
            if response.usage:
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                cost = calculate_cost(input_tokens, output_tokens, agent.config.model)
                span.set_attribute("llm.input_tokens", input_tokens)
                span.set_attribute("llm.output_tokens", output_tokens)
                span.set_attribute("llm.cost_usd", cost)

            # Emit prompt log for per-step observability
            await agent._emit(
                AgentEvent.PROMPT_LOGGED,
                {
                    "agent": agent.config.name,
                    "run_id": getattr(agent, "_current_run_id", ""),
                    "step_index": iteration,
                    "model": agent.config.model,
                    "messages": [m.model_dump(mode="json") for m in messages],
                    "response": response.model_dump(mode="json"),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost,
                    "duration_ms": elapsed_ms,
                    "strategy": "react",
                },
            )

            messages.append(response)

            logger.info(
                "ReAct iteration %d response: content=%d chars, tool_calls=%s",
                iteration + 1,
                len(response.content or ""),
                [tc.name for tc in response.tool_calls] if response.tool_calls else None,
            )

            if response.content:
                await agent._emit(
                    AgentEvent.TEXT_MESSAGE_CONTENT,
                    {"message_id": f"msg_{iteration}", "delta": response.content},
                )

            if not response.tool_calls:
                # Prompt-based tool calling: for small models without native
                # function calling, parse TOOL_CALL: {...} from text output
                prompt_tool_call = _extract_prompt_tool_call(agent, response)
                if prompt_tool_call:
                    tool_name, tool_args = prompt_tool_call
                    logger.info(
                        "ReAct prompt-based tool call detected: %s(%s)",
                        tool_name,
                        tool_args,
                    )
                    # Build a synthetic ToolCall and execute it
                    from sagewai.models.message import ToolCall as ToolCallModel

                    tc_id = f"prompt_tc_{iteration}"
                    synthetic_tc = ToolCallModel(
                        id=tc_id, name=tool_name, arguments=tool_args,
                    )
                    await agent._emit(
                        AgentEvent.TOOL_CALL_START,
                        {"tool_call_id": tc_id, "tool_name": tool_name, "arguments": tool_args},
                    )
                    result = await agent._execute_tool(synthetic_tc)
                    await agent._emit(AgentEvent.TOOL_CALL_END, {"tool_call_id": tc_id})
                    await agent._emit(
                        AgentEvent.TOOL_CALL_RESULT,
                        {
                            "tool_call_id": tc_id,
                            "tool_name": tool_name,
                            "content": result.content,
                            "error": result.error,
                        },
                    )
                    # Strip the TOOL_CALL: line from the response and re-add
                    # the cleaned text + tool result for the next iteration
                    clean_content = _strip_tool_call_from_text(response.content or "")
                    if clean_content.strip():
                        messages[-1] = ChatMessage.assistant(content=clean_content)
                    else:
                        messages.pop()  # remove empty assistant message
                    messages.append(
                        ChatMessage.tool_result(
                            tool_call_id=tc_id,
                            name=tool_name,
                            content=result.error or result.content,
                        )
                    )
                    tool_call_counts[tool_name] = tool_call_counts.get(tool_name, 0) + 1
                    await agent._emit(AgentEvent.STEP_FINISHED, {"step": f"iteration_{iteration}"})
                    continue

            if not response.tool_calls:
                await agent._emit(AgentEvent.STEP_FINISHED, {"step": f"iteration_{iteration}"})
                return response

            # Filter out calls to removed tools (LLM may hallucinate them from context)
            available_names = {t.name for t in available_tools}
            valid_calls = []
            for tc in response.tool_calls:
                if tc.name not in available_names and tc.name not in agent._tool_registry:
                    # Completely unknown tool — return error without executing
                    from sagewai.models.tool import ToolResult
                    messages.append(
                        ChatMessage.tool_result(
                            tool_call_id=tc.id,
                            name=tc.name,
                            content=f"Unknown tool: {tc.name}. Available tools: {sorted(available_names)}",
                        )
                    )
                elif tc.name not in available_names:
                    # Known tool but removed (hit call limit)
                    from sagewai.models.tool import ToolResult
                    messages.append(
                        ChatMessage.tool_result(
                            tool_call_id=tc.id,
                            name=tc.name,
                            content=f"Tool '{tc.name}' has been disabled (called too many times). Provide your answer as text.",
                        )
                    )
                else:
                    valid_calls.append(tc)

            if not valid_calls:
                # All tool calls were invalid — count as error iteration
                error_streak += 1
                if error_streak >= self.max_error_streak:
                    messages.append(
                        ChatMessage.user(
                            "Stop calling tools. Provide your answer directly as text."
                        )
                    )
                    text_resp = await agent._call_llm(messages, [])
                    if text_resp.content:
                        messages.append(text_resp)
                        await agent._emit(
                            AgentEvent.TEXT_MESSAGE_CONTENT,
                            {"message_id": f"msg_{iteration}_forced", "delta": text_resp.content},
                        )
                        await agent._emit(AgentEvent.STEP_FINISHED, {"step": f"iteration_{iteration}"})
                        return text_resp
                await agent._emit(AgentEvent.STEP_FINISHED, {"step": f"iteration_{iteration}"})
                continue

            # Emit tool call events
            for tc in valid_calls:
                await agent._emit(
                    AgentEvent.TOOL_CALL_START,
                    {"tool_call_id": tc.id, "tool_name": tc.name, "arguments": tc.arguments},
                )

            # Execute all tool calls concurrently with OTel spans
            async def _run_tool_with_span(tc):
                with tool_span(tc.name):
                    return await agent._execute_tool(tc)

            results = await asyncio.gather(
                *(_run_tool_with_span(tc) for tc in valid_calls)
            )
            for tc, result in zip(valid_calls, results):
                await agent._emit(AgentEvent.TOOL_CALL_END, {"tool_call_id": tc.id})
                await agent._emit(
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

                # Track per-tool call counts and remove exhausted tools
                tool_call_counts[tc.name] = tool_call_counts.get(tc.name, 0) + 1
                if tool_call_counts[tc.name] >= self.max_tool_calls_per_name:
                    available_tools = [t for t in available_tools if t.name != tc.name]
                    logger.info(
                        "Tool %r hit call limit (%d) — removed from available tools",
                        tc.name,
                        self.max_tool_calls_per_name,
                    )

            # Detect repeated tool errors (unknown tools, sandbox errors, etc.)
            all_failed = all(r.error for r in results)
            if all_failed:
                error_streak += 1
                tool_names = [t.name for t in available_tools]
                has_unknown = any("Unknown tool" in (r.error or "") for r in results)

                if error_streak >= self.max_error_streak:
                    # Force a text-only response — strip tools to break the loop
                    logger.warning(
                        "Agent %s: %d consecutive all-error iterations — forcing text response",
                        agent.config.name,
                        error_streak,
                    )
                    correction = (
                        "Your tool calls have been failing repeatedly. "
                        "Stop calling tools and provide your answer directly as text. "
                        "You already have the information you need from earlier tool results."
                    )
                    if has_unknown:
                        correction += f" Your available tools are: {tool_names}."
                    messages.append(ChatMessage.user(correction))
                    # One text-only LLM call
                    text_response = await agent._call_llm(messages, [])
                    if text_response.content:
                        messages.append(text_response)
                        await agent._emit(
                            AgentEvent.TEXT_MESSAGE_CONTENT,
                            {"message_id": f"msg_{iteration}_correction", "delta": text_response.content},
                        )
                        await agent._emit(AgentEvent.STEP_FINISHED, {"step": f"iteration_{iteration}"})
                        return text_response
                elif has_unknown:
                    messages.append(
                        ChatMessage.user(
                            f"Those tools do not exist. Your available tools are: {tool_names}. "
                            f"If you don't need a tool, respond with text directly."
                        )
                    )
            else:
                error_streak = 0

            await agent._emit(AgentEvent.STEP_FINISHED, {"step": f"iteration_{iteration}"})

        # Max iterations reached
        logger.warning(
            "Agent %s hit max_iterations (%d) with strategy %s",
            agent.config.name,
            max_iterations,
            type(self).__name__,
        )
        return ChatMessage.assistant(
            content=f"[Agent reached maximum iterations ({max_iterations})]"
        )


# ---------------------------------------------------------------------------
# Prompt-based tool calling helpers (for small models)
# ---------------------------------------------------------------------------


def _extract_prompt_tool_call(
    agent: BaseAgent,
    response: ChatMessage,
) -> tuple[str, dict] | None:
    """Check if a text-only response contains a prompt-based tool call.

    Only active when the agent's directive engine uses ``prompt_based``
    tool-call mode (small models). Returns ``(tool_name, arguments)`` or
    ``None``.
    """
    if not response.content:
        return None

    directive_engine = getattr(agent, "_directive_engine", None)
    if directive_engine is None:
        return None

    profile = getattr(directive_engine, "_profile", None)
    if profile is None or getattr(profile, "tool_call_mode", "native") != "prompt_based":
        return None

    from sagewai.directives.formatter import parse_tool_call_from_output

    result = parse_tool_call_from_output(response.content)
    if result is None:
        return None

    tool_name, tool_args = result

    # Security: respect allowed_tools gate from the resolver
    resolver = getattr(directive_engine, "_resolver", None)
    if resolver and not getattr(resolver, "_allow_all_tools", False):
        allowed = getattr(resolver, "_allowed_tools", None)
        if allowed is not None and tool_name not in allowed:
            logger.warning(
                "Prompt-based tool call blocked: %r not in allowed_tools", tool_name
            )
            return None
        elif allowed is None:
            logger.warning(
                "Prompt-based tool call blocked: no allowed_tools configured"
            )
            return None

    # Check tool exists in registry
    if tool_name not in agent._tool_registry:
        logger.warning("Prompt-based tool call: unknown tool %r", tool_name)
        return None

    return tool_name, tool_args


def _strip_tool_call_from_text(text: str) -> str:
    """Remove the TOOL_CALL: {...} line from model output text."""
    import re

    # Remove the TOOL_CALL line and everything after it
    return re.sub(r"TOOL_CALL:\s*\{.*", "", text, flags=re.DOTALL).rstrip()
