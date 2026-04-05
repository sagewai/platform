# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""UniversalAgent — LiteLLM-based multi-model agent."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import litellm

from sagewai.core.base import BaseAgent
from sagewai.errors import (
    SagewaiAuthError,
    SagewaiContextLengthError,
    SagewaiLLMError,
    SagewaiModelNotFoundError,
    SagewaiRateLimitError,
    SagewaiTimeoutError,
)
from sagewai.models.message import ChatMessage, Role, ToolCall, UsageInfo
from sagewai.models.tool import ToolSpec

logger = logging.getLogger(__name__)


class UniversalAgent(BaseAgent):
    """Agent backed by LiteLLM, supporting 100+ LLM providers.

    Works with any model LiteLLM supports: OpenAI, Anthropic, Google (via proxy),
    Mistral, Cohere, Azure, etc.

    Usage::

        agent = UniversalAgent(name="helper", model="gpt-4o")
        response = await agent.chat("Hello!")
    """

    async def _invoke_llm(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        model_override: str | None = None,
    ) -> ChatMessage:
        litellm_messages = [self._message_to_dict(m) for m in messages]

        kwargs = self._build_litellm_kwargs(litellm_messages, tools, model_override=model_override)
        timeout = self.config.inference.timeout

        try:
            if timeout:
                response = await asyncio.wait_for(
                    litellm.acompletion(**kwargs),
                    timeout=timeout,
                )
            else:
                response = await litellm.acompletion(**kwargs)
        except asyncio.TimeoutError as exc:
            raise SagewaiTimeoutError(
                f"LLM call timed out after {timeout:.0f}s for model {self.config.model}"
            ) from exc
        except Exception as exc:
            raise self._wrap_provider_error(exc) from exc

        return self._parse_response(response)

    async def _stream_llm(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        model_override: str | None = None,
    ) -> AsyncGenerator[str | ToolCall, None]:
        """Stream response chunks via LiteLLM.

        Yields text chunks as strings, then any accumulated tool calls
        as ToolCall objects after the stream completes.
        """
        litellm_messages = [self._message_to_dict(m) for m in messages]

        kwargs = self._build_litellm_kwargs(
            litellm_messages, tools, stream=True, model_override=model_override
        )
        timeout = self.config.inference.timeout

        try:
            if timeout:
                response = await asyncio.wait_for(
                    litellm.acompletion(**kwargs),
                    timeout=timeout,
                )
            else:
                response = await litellm.acompletion(**kwargs)
        except asyncio.TimeoutError:
            logger.error(
                "Streaming LLM call timed out after %.0fs for model %s",
                timeout,
                self.config.model,
            )
            yield f"[LLM call timed out after {timeout:.0f}s]"
            return

        # Accumulate tool call fragments across chunks
        tool_calls_acc: dict[int, dict[str, str]] = {}

        async for chunk in response:
            delta = chunk.choices[0].delta

            if hasattr(delta, "content") and delta.content:
                yield delta.content

            if hasattr(delta, "tool_calls") and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": getattr(tc, "id", None) or "",
                            "name": "",
                            "args": "",
                        }
                    if tc.function and tc.function.name:
                        tool_calls_acc[idx]["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        tool_calls_acc[idx]["args"] += tc.function.arguments

        # Yield accumulated tool calls
        for tc_data in tool_calls_acc.values():
            try:
                args = json.loads(tc_data["args"]) if tc_data["args"] else {}
            except json.JSONDecodeError:
                args = {"raw": tc_data["args"]}
            yield ToolCall(
                id=tc_data["id"] or uuid.uuid4().hex[:8],
                name=tc_data["name"],
                arguments=args,
            )

    # ------------------------------------------------------------------
    # Request builder
    # ------------------------------------------------------------------

    def _build_litellm_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        stream: bool = False,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        """Build kwargs dict for litellm.acompletion from inference params."""
        inf = self.config.inference
        kwargs: dict[str, Any] = {
            "model": model_override or self.config.model,
            "messages": messages,
            "temperature": inf.temperature,
        }
        if inf.max_tokens:
            kwargs["max_tokens"] = inf.max_tokens
        if inf.top_p is not None:
            kwargs["top_p"] = inf.top_p
        if inf.top_k is not None:
            kwargs["top_k"] = inf.top_k
        if inf.frequency_penalty is not None:
            kwargs["frequency_penalty"] = inf.frequency_penalty
        if inf.presence_penalty is not None:
            kwargs["presence_penalty"] = inf.presence_penalty
        if inf.stop_sequences:
            kwargs["stop"] = inf.stop_sequences
        if stream:
            kwargs["stream"] = True
        if tools:
            kwargs["tools"] = [self._tool_to_openai(t) for t in tools]
        if inf.api_base:
            kwargs["api_base"] = inf.api_base
        if inf.api_key:
            kwargs["api_key"] = inf.api_key
        if inf.custom_llm_provider:
            kwargs["custom_llm_provider"] = inf.custom_llm_provider
        if inf.timeout:
            kwargs["timeout"] = inf.timeout
        if inf.fallback_models:
            kwargs["fallbacks"] = [{"model": m} for m in inf.fallback_models]

        # Apply worker-level credential overrides (set via ContextVar
        # by WorkflowWorker._execute_workflow). This allows workers on
        # different machines to inject their own API keys, model names,
        # and endpoints without modifying workflow or agent definitions.
        from sagewai.core.worker import get_worker_credentials

        worker_creds = get_worker_credentials()
        if worker_creds is not None:
            # Model override: worker's "default" replaces agent's model
            # unless an explicit model_override was passed to this call.
            if not model_override and "default" in worker_creds.model_overrides:
                kwargs["model"] = worker_creds.model_overrides["default"]

            # Inference overrides: api_base, api_key, provider, timeout
            if worker_creds.inference_overrides:
                wo = worker_creds.inference_overrides
                if wo.api_base:
                    kwargs["api_base"] = wo.api_base
                if wo.api_key:
                    kwargs["api_key"] = wo.api_key
                if wo.custom_llm_provider:
                    kwargs["custom_llm_provider"] = wo.custom_llm_provider
                if wo.timeout:
                    kwargs["timeout"] = wo.timeout

        return kwargs

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _message_to_dict(msg: ChatMessage) -> dict[str, Any]:
        """Convert ChatMessage to the dict format LiteLLM expects.

        When the message contains multimodal ``parts``, the content is
        converted to the OpenAI vision-style list format that LiteLLM
        forwards to vision-capable models.
        """
        d: dict[str, Any] = {"role": msg.role.value}

        if msg.parts:
            from sagewai.intelligence.multimodal.message import ContentType

            content_parts: list[dict[str, Any]] = []
            for part in msg.parts:
                if part.is_text:
                    content_parts.append({"type": "text", "text": part.text or ""})
                elif part.type == ContentType.IMAGE:
                    if part.media_base64:
                        mime = part.mime_type or "image/png"
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{part.media_base64}",
                            },
                        })
                    elif part.media_url:
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": part.media_url},
                        })
            d["content"] = content_parts
        elif msg.content is not None:
            d["content"] = msg.content

        if msg.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in msg.tool_calls
            ]

        if msg.tool_call_id:
            d["tool_call_id"] = msg.tool_call_id

        if msg.name:
            d["name"] = msg.name

        return d

    @staticmethod
    def _tool_to_openai(spec: ToolSpec) -> dict[str, Any]:
        """Convert ToolSpec to OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }

    @staticmethod
    def _parse_response(response: Any) -> ChatMessage:
        """Parse a LiteLLM ModelResponse into a ChatMessage."""
        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] | None = None
        if hasattr(message, "tool_calls") and message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                tool_calls.append(
                    ToolCall(
                        id=tc.id or uuid.uuid4().hex[:8],
                        name=tc.function.name,
                        arguments=args,
                    )
                )

        # Extract token usage from response
        usage = None
        if hasattr(response, "usage") and response.usage:
            usage = UsageInfo(
                input_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
            )

        return ChatMessage(
            role=Role.assistant,
            content=getattr(message, "content", None),
            tool_calls=tool_calls,
            usage=usage,
        )

    # ------------------------------------------------------------------
    # Error wrapping
    # ------------------------------------------------------------------

    def _provider_name(self) -> str:
        """Infer the LLM provider name from the model string."""
        model = self.config.model
        if "/" in model:
            return model.split("/", 1)[0]
        for prefix, provider in (
            ("gpt-", "openai"),
            ("o1", "openai"),
            ("o3", "openai"),
            ("claude-", "anthropic"),
            ("gemini-", "google"),
            ("mistral", "mistral"),
            ("command", "cohere"),
        ):
            if model.startswith(prefix):
                return provider
        return "unknown"

    def _wrap_provider_error(self, exc: Exception) -> SagewaiLLMError:
        """Map a provider exception to the appropriate SagewaiError subclass."""
        lower = str(exc).lower()
        kwargs = {
            "provider": self._provider_name(),
            "model": self.config.model,
            "agent_name": self.config.name,
        }
        msg = (
            f"{type(exc).__name__} from {kwargs['provider']} while running "
            f"agent '{kwargs['agent_name']}' (model={kwargs['model']}): {exc}"
        )

        if any(tok in lower for tok in ("rate_limit", "rate limit", "429", "too many requests")):
            retry_after: float | None = None
            if hasattr(exc, "retry_after"):
                retry_after = float(exc.retry_after)
            return SagewaiRateLimitError(
                msg,
                **kwargs,
                retry_after=retry_after,
            )

        if any(tok in lower for tok in ("auth", "api_key", "api key", "401", "unauthorized")):
            return SagewaiAuthError(msg, **kwargs)

        if any(tok in lower for tok in ("not_found", "not found", "404", "does not exist")):
            return SagewaiModelNotFoundError(msg, **kwargs)

        if any(
            tok in lower
            for tok in (
                "context_length",
                "context length",
                "max_tokens",
                "maximum context",
                "token limit",
                "too long",
            )
        ):
            return SagewaiContextLengthError(msg, **kwargs)

        return SagewaiLLMError(msg, **kwargs)
