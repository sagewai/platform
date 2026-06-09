# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
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
import re
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
        api_key: Optional provider API key passed directly to LiteLLM.
        api_base: Optional provider base URL passed directly to LiteLLM.
        allow_env_fallback: Whether LiteLLM may fall back to process
            environment credentials when no explicit provider config was supplied.
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
    api_key: str | None = None
    api_base: str | None = None
    allow_env_fallback: bool = True
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
        user_message = _build_user_message(context, agent=agent)
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

                raw_tool_calls = assistant_msg.get("tool_calls")
                # Directive-mode fallback: when the model didn't emit
                # native tool_calls but wrote ``/tool.NAME(args)``
                # directives in its prose, parse them out and re-shape
                # them as if they were native tool_calls so the same
                # execution code below handles either path.
                if (
                    not (isinstance(raw_tool_calls, list) and raw_tool_calls)
                    and assistant_msg.get("content")
                    and agent.tools
                    and self._config.tool_registry is not None
                ):
                    directives = _parse_directive_tool_calls(
                        assistant_msg["content"], allowed=tuple(agent.tools)
                    )
                    if directives:
                        raw_tool_calls = [
                            {
                                "id": d["id"],
                                "type": "function",
                                "function": {
                                    "name": d["name"],
                                    "arguments": json.dumps(d["args"]),
                                },
                            }
                            for d in directives
                        ]
                        # Persist on the message so the trace records the
                        # synthesised tool_calls just like native ones.
                        assistant_msg["tool_calls"] = raw_tool_calls
                        logger.debug(
                            "Agent %r: parsed %d directive tool calls from prose",
                            agent.id, len(raw_tool_calls),
                        )

                # If no tool_calls (native or directive), this is the final turn.
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
                        try:
                            tool_result = await registry.execute(tool_name, args)
                        except KeyError:
                            # Small models occasionally emit placeholder
                            # names like 'function_name' or invent tools
                            # outside the agent's declared list. Surface
                            # the unknown-tool error back to the model as
                            # a tool result so it can self-correct on the
                            # next iteration instead of failing the step.
                            available = ", ".join(agent.tools or ())
                            tool_result = (
                                f"[unknown tool {tool_name!r}; valid tools "
                                f"for this agent: {available or '(none)'}. "
                                "Reply directly with the deliverable, or "
                                "call one of the valid tools by name.]"
                            )
                        except Exception as tool_exc:  # noqa: BLE001
                            tool_result = f"[tool error: {type(tool_exc).__name__}: {tool_exc}]"
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
        user_message = _build_user_message(context, agent=agent)
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
                if (
                    not self._config.allow_env_fallback
                    and not self._config.api_key
                    and not self._config.api_base
                ):
                    return StepResult(
                        node_id=agent.id,
                        status="skipped",
                        output_preview=_NO_PROVIDER_SENTINEL,
                    )
                kwargs: dict[str, Any] = {
                    "model": self._config.model,
                    "messages": messages_list,
                    "max_tokens": self._config.max_tokens,
                    "temperature": self._config.temperature,
                }
                if self._config.api_key:
                    kwargs["api_key"] = self._config.api_key
                if self._config.api_base:
                    kwargs["api_base"] = self._config.api_base
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

                # Directive-mode fallback (mirror of harness path): if the
                # model didn't emit native tool_calls but wrote
                # ``/tool.NAME(args)`` directives in prose, parse and
                # promote them into ``tool_calls`` shape so the existing
                # execution loop runs unchanged.
                if (
                    not (isinstance(raw_tool_calls, list) and raw_tool_calls)
                    and assistant_msg.get("content")
                    and agent.tools
                    and self._config.tool_registry is not None
                ):
                    directives = _parse_directive_tool_calls(
                        assistant_msg["content"], allowed=tuple(agent.tools)
                    )
                    if directives:
                        synth = [
                            {
                                "id": d["id"],
                                "type": "function",
                                "function": {
                                    "name": d["name"],
                                    "arguments": json.dumps(d["args"]),
                                },
                            }
                            for d in directives
                        ]
                        assistant_msg["tool_calls"] = synth
                        raw_tool_calls = synth
                        logger.debug(
                            "Agent %r: parsed %d directive tool calls from prose",
                            agent.id, len(synth),
                        )

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
                        try:
                            tool_result = await registry.execute(tool_name, args)
                        except KeyError:
                            # Small models occasionally emit placeholder
                            # names like 'function_name' or invent tools
                            # outside the agent's declared list. Surface
                            # the unknown-tool error back to the model as
                            # a tool result so it can self-correct on the
                            # next iteration instead of failing the step.
                            available = ", ".join(agent.tools or ())
                            tool_result = (
                                f"[unknown tool {tool_name!r}; valid tools "
                                f"for this agent: {available or '(none)'}. "
                                "Reply directly with the deliverable, or "
                                "call one of the valid tools by name.]"
                            )
                        except Exception as tool_exc:  # noqa: BLE001
                            tool_result = f"[tool error: {type(tool_exc).__name__}: {tool_exc}]"
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


# Sagewai-directive tool-call grammar: ``/tool.NAME(key='val', ...)``.
# Mirrors :mod:`sagewai.directives.tokenizer` so the same syntax users
# write at the playground also lands here when an LLM emits it in prose.
_DIRECTIVE_TOOL_CALL_RE = re.compile(
    r"/tool\.(?P<name>\w[\w.\-]*)\((?P<args>[^)]*)\)",
    re.DOTALL,
)
# `key='val'` or `key="val"` — accepts both quote styles.
_DIRECTIVE_KWARG_RE = re.compile(
    r"""(?P<key>\w+)\s*=\s*(?P<val>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")""",
    re.DOTALL,
)
# Bare quoted string as the only argument: `/tool.fetch_url('https://…')`.
_DIRECTIVE_BARE_RE = re.compile(
    r"""^\s*(?P<val>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")\s*$""",
    re.DOTALL,
)


def _parse_directive_tool_calls(
    text: str,
    *,
    allowed: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Extract ``/tool.NAME(args)`` directives from *text*.

    Returns a list of ``{"id", "name", "args"}`` dicts in the same shape
    the executor's tool-call loop already consumes from native
    ``tool_calls``. ``allowed`` (when provided) gates which tool names
    pass — directives referring to anything else are dropped silently
    so a model hallucinating tool names can't crash the step.

    Argument parsing accepts two forms:

    * keyword args — ``/tool.fetch_url(url='https://…')``
    * a single bare quoted string mapped to the first declared parameter
      of the tool, or to ``query`` for ``web_search`` and ``url`` for
      ``fetch_url`` (the two we ship by default)
    """
    out: list[dict[str, Any]] = []
    if not text:
        return out
    for idx, m in enumerate(_DIRECTIVE_TOOL_CALL_RE.finditer(text)):
        name = m.group("name")
        if allowed is not None and name not in allowed:
            continue
        args_raw = m.group("args") or ""
        args: dict[str, Any] = {}
        # Try keyword args first.
        for kw in _DIRECTIVE_KWARG_RE.finditer(args_raw):
            args[kw.group("key")] = _strip_quotes(kw.group("val"))
        if not args:
            bare = _DIRECTIVE_BARE_RE.match(args_raw)
            if bare:
                value = _strip_quotes(bare.group("val"))
                # Map the bare positional to a sensible kwarg by tool name.
                bare_kw = "url" if name == "fetch_url" else "query"
                args[bare_kw] = value
        out.append({"id": f"directive-{idx}", "name": name, "args": args, "raw": m.group(0)})
    return out


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] in "'\"" and s[-1] == s[0]:
        return s[1:-1].encode("utf-8").decode("unicode_escape", errors="replace")
    return s


def _build_user_message(context: dict, agent: Agent | None = None) -> str:
    """Serialise the context dict into a directive briefing for the LLM agent.

    The agent is told (in this order) what role it has, which tools it
    may call, what the goal is, and what's expected of it. The "act,
    don't describe" guardrail is critical — without it, smaller local
    models reliably output a description of the workflow instead of
    actually executing the step.
    """
    goal = (context or {}).get("goal", "")
    other_slots = {
        k: v for k, v in (context or {}).items() if k != "goal" and not k.startswith("__")
    }

    lines: list[str] = []
    if agent is not None:
        agent_id = agent.id
        agent_role = (agent.role or agent.id) if hasattr(agent, "role") else agent_id
        lines.append(f"You are agent '{agent_id}' (role: {agent_role}) in a Sagewai mission.")
        if getattr(agent, "tools", None):
            lines.append(f"Tools you can call: {', '.join(agent.tools)}")
            lines.append(
                "Prefer the native function-calling interface when your model "
                "supports it. If your model does not, you can call tools by "
                "writing a Sagewai directive on its own line:"
            )
            lines.append("    /tool.NAME(arg1='value', arg2='value')")
            lines.append(
                "For example: /tool.fetch_url(url='https://example.com'). "
                "The platform parses the directive, runs the tool, and feeds "
                "the result back as your next input. Use either mechanism — "
                "but actually call tools, do NOT describe what you would do. "
                "Your final assistant message must contain the deliverable "
                "(article, summary, decision, etc.), not a workflow plan."
            )
        else:
            lines.append(
                "You have no tools available. Produce the answer directly from the "
                "input below — do not describe what you would do, write the actual "
                "deliverable."
            )

    if goal:
        lines.append("")
        lines.append(f"Mission goal: {goal}")

    if other_slots:
        lines.append("")
        lines.append("Inputs from upstream agents and slot values:")
        for key, value in other_slots.items():
            preview = str(value)
            if len(preview) > 1500:
                preview = preview[:1500] + "…"
            lines.append(f"  {key}: {preview}")

    if not lines:
        return "No additional context provided. Proceed with the task."
    return "\n".join(lines)
