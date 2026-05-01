# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""AgentExecutor — runs a single agent node against a mission context.

This module bridges the autopilot graph-walk loop with real LLM
inference via LiteLLM. For deterministic nodes it short-circuits
immediately; for LLM nodes it calls ``litellm.acompletion`` and
gracefully degrades to a "skipped" result when no API key is configured,
or to a "failed" result when the call raises an unexpected error.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sagewai.autopilot._types import AgentKind
from sagewai.autopilot.agent_graph import Agent

from .tool_registry import ToolRegistry
from .types import StepResult

logger = logging.getLogger(__name__)

_NO_PROVIDER_SENTINEL = "No LLM provider configured"

_GENERIC_SYSTEM_PROMPT = (
    "You are an AI agent operating within the Sagewai autopilot framework. "
    "Complete the task described in the user message as thoroughly as possible."
)


class ExecutorConfig(BaseModel):
    """Injectable configuration for :class:`AgentExecutor`.

    Attributes:
        model: Default model name for the direct-litellm fallback
            path (used when ``harness_proxy`` is ``None``).
        max_tokens: Default ``max_tokens`` for the fallback path.
        temperature: Default ``temperature`` for the fallback path.
        harness_proxy: Optional :class:`~sagewai.harness.HarnessProxy`
            instance. When set together with ``harness_identity``, the
            executor routes LLM calls through the proxy and gains
            budget enforcement, classification, routing, policy,
            audit, and cost tracking. Defaults to ``None`` for
            backward compatibility (direct-litellm path).
        harness_identity: Optional
            :class:`~sagewai.harness.HarnessIdentity` for the proxy
            calls. Required if ``harness_proxy`` is set; ignored
            otherwise. Defaults to ``None``.
        tool_registry: Optional :class:`~sagewai.autopilot.controller.ToolRegistry`
            supplying the callable side of tool execution. When ``None``,
            agents with non-empty ``tools`` tuples will raise
            :class:`KeyError` during spec resolution. Defaults to
            ``None`` (tool-calling disabled).
        max_tool_iterations: Maximum number of tool-call rounds per
            agent step before the loop is forcibly broken. Prevents
            runaway loops when a model keeps requesting tools without
            ever producing a final answer. Defaults to ``5``.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    model: str = "gpt-4o-mini"
    max_tokens: int = 2048
    temperature: float = 0.3
    harness_proxy: Any | None = None
    harness_identity: Any | None = None
    tool_registry: ToolRegistry | None = None
    max_tool_iterations: int = Field(default=5, ge=1)


class AgentExecutor:
    """Executes a single agent node and returns a :class:`StepResult`.

    Calling :meth:`execute` is the only public entry point.  The method
    never raises — all errors are caught and reflected in the returned
    ``StepResult.status``.

    Args:
        config: Optional :class:`ExecutorConfig`.  Defaults are used if
            not provided.
    """

    def __init__(self, config: ExecutorConfig | None = None) -> None:
        self._config = config or ExecutorConfig()

    async def execute(self, agent: Agent, context: dict) -> StepResult:
        """Execute *agent* given the current *context* dict.

        Args:
            agent: The :class:`~sagewai.autopilot.agent_graph.Agent` node
                to execute.
            context: Accumulating context dict.  For LLM nodes this is
                serialised into the user message.

        Returns:
            A :class:`StepResult` with ``status`` of ``"completed"``,
            ``"skipped"``, or ``"failed"``.
        """
        if agent.kind is AgentKind.DETERMINISTIC:
            return StepResult(
                node_id=agent.id,
                status="completed",
                output_preview="deterministic pass-through",
            )

        # LLM path
        return await self._run_llm(agent, context)

    # ── private ─────────────────────────────────────────────────────

    async def _run_llm(self, agent: Agent, context: dict) -> StepResult:
        """Execute an LLM-kind agent node and return a StepResult.

        Branches between two paths:

        - **Harness path** — when ``ExecutorConfig.harness_proxy`` and
          ``harness_identity`` are both set, calls
          :meth:`~sagewai.harness.HarnessProxy.handle_request`. Full
          output, conversation messages, and telemetry (cost, tokens,
          model_used, latency) are captured on the returned
          :class:`StepResult`.

        - **Direct-litellm fallback path** — when either field is
          ``None``, calls ``litellm.acompletion`` directly. Output is
          truncated to 200 chars on ``output_preview``; new optional
          fields (``output``, ``messages``, ``telemetry``) stay
          ``None``.

        Never raises — all errors are caught and reflected in
        ``StepResult.status``.
        """
        if self._config.harness_proxy is not None and self._config.harness_identity is not None:
            return await self._run_llm_harness(agent, context)
        return await self._run_llm_direct(agent, context)

    async def _run_llm_harness(self, agent: Agent, context: dict) -> StepResult:
        """Harness-routed LLM path. See :meth:`_run_llm` for context.

        When the agent has a non-empty ``tools`` tuple and a
        :class:`ToolRegistry` is configured, this method runs a
        tool-call loop: the LLM may request tools, they are executed,
        and the conversation continues until the model returns a final
        response (no ``tool_calls``) or :attr:`ExecutorConfig.max_tool_iterations`
        is reached.
        """
        import time

        from .types import StepTelemetry

        system_prompt = self._load_prompt(agent.prompt_ref)
        user_message = _build_user_message(context)
        messages_list: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        proxy = self._config.harness_proxy
        identity = self._config.harness_identity

        # Resolve tool specs once if the agent declares tools.
        tool_specs: list[dict[str, Any]] | None = None
        if agent.tools and self._config.tool_registry is not None:
            tool_specs = self._config.tool_registry.specs_for(agent.tools)

        accumulated_tool_calls: list[str] = []
        last_response: dict[str, Any] = {}
        latency_ms: float = 0.0
        iteration = 0

        try:
            while iteration < self._config.max_tool_iterations:
                t0 = time.monotonic()
                kwargs: dict[str, Any] = {
                    "identity": identity,
                    "messages": messages_list,
                    "model": self._config.model,
                    "stream": False,
                }
                if tool_specs is not None:
                    kwargs["tools"] = tool_specs

                response = await proxy.handle_request(**kwargs)
                latency_ms = (time.monotonic() - t0) * 1000.0
                last_response = response if isinstance(response, dict) else {}

                # Extract the assistant message from the OpenAI-compat response,
                # always normalising to include role=assistant so callers can
                # rely on the role key even when the proxy omits it.
                try:
                    raw_msg: dict[str, Any] = response["choices"][0]["message"]
                except (KeyError, IndexError, TypeError):
                    raw_msg = {}
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": raw_msg.get("content") or "",
                }
                # Preserve tool_calls if present in the raw message.
                raw_tool_calls_value = raw_msg.get("tool_calls")
                if isinstance(raw_tool_calls_value, list) and raw_tool_calls_value:
                    assistant_msg["tool_calls"] = raw_tool_calls_value

                messages_list.append(assistant_msg)

                # If no valid tool_calls list, this is the final turn.
                raw_tool_calls = assistant_msg.get("tool_calls")
                if not isinstance(raw_tool_calls, list) or not raw_tool_calls:
                    break

                # Execute each tool call and append results as tool messages.
                registry = self._config.tool_registry
                for tc in raw_tool_calls:
                    tool_name: str = tc["function"]["name"]
                    raw_args = tc["function"].get("arguments", "{}")
                    if isinstance(raw_args, str):
                        try:
                            args: dict[str, Any] = json.loads(raw_args)
                        except json.JSONDecodeError:
                            args = {}
                    else:
                        args = raw_args or {}

                    if registry is not None:
                        tool_result = await registry.execute(tool_name, args)
                    else:
                        tool_result = f"[Tool {tool_name!r} unavailable: no registry configured]"

                    messages_list.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": str(tool_result),
                    })
                    accumulated_tool_calls.append(tool_name)

                iteration += 1

        except Exception as exc:  # noqa: BLE001
            logger.error("Agent %r harness call failed: %s", agent.id, exc)
            return StepResult(
                node_id=agent.id,
                status="failed",
                output_preview=str(exc)[:200],
            )

        # Extract final text from the last assistant message.
        try:
            text: str = messages_list[-1].get("content") or ""
        except (IndexError, AttributeError):
            text = ""

        # Extract telemetry from the last LLM response. The harness
        # attaches a ``_harness`` transparency block; usage tokens are
        # at ``usage`` (OpenAI-compat). Both may be missing in tests.
        usage = last_response.get("usage", {}) if last_response else {}
        harness_meta = last_response.get("_harness", {}) if last_response else {}
        model_used = last_response.get("model") if last_response else None
        if not model_used:
            model_used = self._config.model

        telemetry = StepTelemetry(
            cost_usd=float(harness_meta.get("cost_usd", 0.0)),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            model_used=str(model_used),
            latency_ms=float(harness_meta.get("latency_ms", latency_ms)),
        )

        preview = text[:200]
        logger.debug("Agent %r completed via harness (preview=%r)", agent.id, preview[:60])

        return StepResult(
            node_id=agent.id,
            status="completed",
            output_preview=preview,
            output=text,
            messages=tuple(messages_list),
            telemetry=telemetry,
            tool_calls=tuple(accumulated_tool_calls) if accumulated_tool_calls else None,
        )

    async def _run_llm_direct(self, agent: Agent, context: dict) -> StepResult:
        """Direct-litellm fallback path. Renamed from _run_llm.

        See :meth:`_run_llm` for context. ``output`` and ``telemetry``
        stay ``None`` on this path (no harness), but ``messages`` and
        ``tool_calls`` are populated when tools are used.

        When the agent has a non-empty ``tools`` tuple and a
        :class:`ToolRegistry` is configured, this method runs the same
        tool-call loop as :meth:`_run_llm_harness`.
        """
        try:
            import litellm  # local import — optional dependency
        except ModuleNotFoundError:
            logger.warning("litellm not installed — skipping LLM agent %r", agent.id)
            return StepResult(
                node_id=agent.id,
                status="skipped",
                output_preview=_NO_PROVIDER_SENTINEL,
            )

        system_prompt = self._load_prompt(agent.prompt_ref)
        user_message = _build_user_message(context)
        messages_list: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        # Resolve tool specs once if the agent declares tools.
        tool_specs: list[dict[str, Any]] | None = None
        if agent.tools and self._config.tool_registry is not None:
            tool_specs = self._config.tool_registry.specs_for(agent.tools)

        accumulated_tool_calls: list[str] = []
        iteration = 0

        try:
            while iteration < self._config.max_tool_iterations:
                kwargs: dict[str, Any] = {
                    "model": self._config.model,
                    "messages": messages_list,
                    "max_tokens": self._config.max_tokens,
                    "temperature": self._config.temperature,
                }
                if tool_specs is not None:
                    kwargs["tools"] = tool_specs

                response = await litellm.acompletion(**kwargs)

                # Convert litellm's object-style response to a plain dict for
                # uniform handling, mirroring the harness path.
                raw_message = response.choices[0].message
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": raw_message.content or "",
                }
                raw_tool_calls = getattr(raw_message, "tool_calls", None)
                # Guard: only treat it as a real tool-calls list when it's
                # actually a list (not a MagicMock attribute in tests).
                if isinstance(raw_tool_calls, list) and raw_tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in raw_tool_calls
                    ]

                messages_list.append(assistant_msg)

                if not isinstance(raw_tool_calls, list) or not raw_tool_calls:
                    break  # final response (no tool calls)

                # Execute tool calls.
                registry = self._config.tool_registry
                for tc_dict in assistant_msg["tool_calls"]:
                    tool_name: str = tc_dict["function"]["name"]
                    raw_args = tc_dict["function"].get("arguments", "{}")
                    if isinstance(raw_args, str):
                        try:
                            args: dict[str, Any] = json.loads(raw_args)
                        except json.JSONDecodeError:
                            args = {}
                    else:
                        args = raw_args or {}

                    if registry is not None:
                        tool_result = await registry.execute(tool_name, args)
                    else:
                        tool_result = f"[Tool {tool_name!r} unavailable: no registry configured]"

                    messages_list.append({
                        "role": "tool",
                        "tool_call_id": tc_dict.get("id", ""),
                        "content": str(tool_result),
                    })
                    accumulated_tool_calls.append(tool_name)

                iteration += 1

            text: str = messages_list[-1].get("content") or ""
            preview = text[:200]
            logger.debug("Agent %r completed (preview=%r)", agent.id, preview[:60])
            return StepResult(
                node_id=agent.id,
                status="completed",
                output_preview=preview,
                tool_calls=tuple(accumulated_tool_calls) if accumulated_tool_calls else None,
            )
        except litellm.exceptions.AuthenticationError:
            logger.warning("No API key for agent %r — skipping (AuthenticationError)", agent.id)
            return StepResult(
                node_id=agent.id,
                status="skipped",
                output_preview=_NO_PROVIDER_SENTINEL,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Agent %r LLM call failed: %s", agent.id, exc)
            return StepResult(
                node_id=agent.id,
                status="failed",
                output_preview=str(exc)[:200],
            )

    @staticmethod
    def _load_prompt(prompt_ref: str | None) -> str:
        """Load prompt text from *prompt_ref* file path, or return generic."""
        if prompt_ref is None:
            return _GENERIC_SYSTEM_PROMPT
        path = pathlib.Path(prompt_ref)
        if path.exists():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        logger.debug("prompt_ref %r not found — using generic prompt", prompt_ref)
        return _GENERIC_SYSTEM_PROMPT


def _build_user_message(context: dict) -> str:
    """Serialise the context dict into a human-readable user message."""
    if not context:
        return "No additional context provided. Proceed with the task."
    lines = ["Current mission context:"]
    for key, value in context.items():
        if key.startswith("__"):
            continue  # skip internal slots like __blueprint_json__
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)
