# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""GoogleNativeAgent — Google GenAI SDK optimized agent.

Uses the unified ``google-genai`` package (replaces the deprecated
``google-generativeai`` package).  Provides direct access to Gemini
features without the LiteLLM proxy layer.

Usage::

    agent = GoogleNativeAgent(name="gemini", model="gemini-2.5-flash")
    response = await agent.chat("Summarize this document.")
"""

from __future__ import annotations

import uuid
from typing import Any

from google import genai
from google.genai import types

from sagewai.core.base import BaseAgent
from sagewai.errors import (
    SagewaiAuthError,
    SagewaiContextLengthError,
    SagewaiLLMError,
    SagewaiModelNotFoundError,
    SagewaiRateLimitError,
)
from sagewai.models.message import ChatMessage, Role, ToolCall, UsageInfo
from sagewai.models.tool import ToolSpec


class GoogleNativeAgent(BaseAgent):
    """Agent using Google's unified GenAI SDK for Gemini models.

    Supports gemini-2.5-flash, gemini-2.5-pro, etc.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = genai.Client()

    async def _invoke_llm(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        model_override: str | None = None,
    ) -> ChatMessage:
        contents = self._build_contents(messages)
        config = self._build_config(messages, tools)

        try:
            response = await self._client.aio.models.generate_content(
                model=model_override or self.config.model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            raise self._wrap_provider_error(exc) from exc
        return self._parse_response(response)

    # ------------------------------------------------------------------
    # Config builder
    # ------------------------------------------------------------------

    def _build_config(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
    ) -> types.GenerateContentConfig:
        """Build GenerateContentConfig with system instruction, tools, and generation params."""
        system_parts = [msg.content for msg in messages if msg.role == Role.system and msg.content]
        system_instruction = "\n".join(system_parts) if system_parts else None

        genai_tools = None
        if tools:
            declarations = [self._tool_to_declaration(t) for t in tools]
            genai_tools = [types.Tool(function_declarations=declarations)]  # type: ignore[arg-type]

        inf = self.config.inference
        config_kwargs: dict[str, Any] = {
            "temperature": inf.temperature,
            # Disable automatic function calling — we handle tool execution
            # in BaseAgent._agent_loop() ourselves.
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        if inf.max_tokens:
            config_kwargs["max_output_tokens"] = inf.max_tokens
        if inf.top_p is not None:
            config_kwargs["top_p"] = inf.top_p
        if inf.top_k is not None:
            config_kwargs["top_k"] = inf.top_k
        if inf.stop_sequences:
            config_kwargs["stop_sequences"] = inf.stop_sequences
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if genai_tools:
            config_kwargs["tools"] = genai_tools

        return types.GenerateContentConfig(**config_kwargs)

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_contents(messages: list[ChatMessage]) -> list[types.Content]:
        """Convert ChatMessage list to Gemini Content objects.

        System messages are extracted separately (handled via
        ``system_instruction`` in the config), so they are skipped here.
        """
        contents: list[types.Content] = []

        for msg in messages:
            if msg.role == Role.system:
                continue

            if msg.role == Role.user:
                parts: list[types.Part] = []
                if msg.parts:
                    from sagewai.intelligence.multimodal.message import ContentType

                    for cp in msg.parts:
                        if cp.is_text:
                            parts.append(types.Part.from_text(text=cp.text or ""))
                        elif cp.type == ContentType.IMAGE:
                            if cp.media_base64:
                                import base64 as _b64

                                parts.append(
                                    types.Part.from_bytes(
                                        data=_b64.b64decode(cp.media_base64),
                                        mime_type=cp.mime_type or "image/png",
                                    )
                                )
                            elif cp.media_url:
                                parts.append(
                                    types.Part.from_uri(
                                        file_uri=cp.media_url,
                                        mime_type=cp.mime_type or "image/png",
                                    )
                                )
                elif msg.content:
                    parts.append(types.Part.from_text(text=msg.content))
                if parts:
                    contents.append(types.Content(role="user", parts=parts))

            elif msg.role == Role.assistant:
                parts = []
                if msg.content:
                    parts.append(types.Part.from_text(text=msg.content))
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        parts.append(
                            types.Part(
                                function_call=types.FunctionCall(
                                    name=tc.name,
                                    args=tc.arguments,
                                )
                            )
                        )
                if parts:
                    contents.append(types.Content(role="model", parts=parts))

            elif msg.role == Role.tool:
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=msg.name or "",
                                response={"result": msg.content or ""},
                            )
                        ],
                    )
                )

        return contents

    @staticmethod
    def _tool_to_declaration(spec: ToolSpec) -> dict[str, Any]:
        """Convert ToolSpec to a Gemini function declaration dict."""
        return {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters.copy(),
        }

    @staticmethod
    def _parse_response(response: Any) -> ChatMessage:
        """Parse a GenerateContentResponse into a ChatMessage."""
        if not response.candidates:
            return ChatMessage(role=Role.assistant, content=None)

        candidate = response.candidates[0]
        content_parts = candidate.content.parts

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for part in content_parts:
            if part.text:
                text_parts.append(part.text)
            elif part.function_call:
                fc = part.function_call
                args = dict(fc.args) if fc.args else {}
                tool_calls.append(
                    ToolCall(
                        id=uuid.uuid4().hex[:8],
                        name=fc.name,
                        arguments=args,
                    )
                )

        # Extract token usage from response metadata
        usage = None
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = UsageInfo(
                input_tokens=getattr(response.usage_metadata, "prompt_token_count", 0) or 0,
                output_tokens=getattr(response.usage_metadata, "candidates_token_count", 0) or 0,
            )

        return ChatMessage(
            role=Role.assistant,
            content="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls if tool_calls else None,
            usage=usage,
        )

    # ------------------------------------------------------------------
    # Error wrapping
    # ------------------------------------------------------------------

    def _wrap_provider_error(self, exc: Exception) -> SagewaiLLMError:
        """Map a Google GenAI exception to the appropriate SagewaiError subclass."""
        lower = str(exc).lower()
        kwargs = {
            "provider": "google",
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
                msg, **kwargs, retry_after=retry_after,
            )

        if any(tok in lower for tok in ("auth", "api_key", "api key", "401", "unauthorized")):
            return SagewaiAuthError(msg, **kwargs)

        if any(tok in lower for tok in ("not_found", "not found", "404", "does not exist")):
            return SagewaiModelNotFoundError(msg, **kwargs)

        if any(tok in lower for tok in (
            "context_length", "context length", "max_tokens", "maximum context",
            "token limit", "too long",
        )):
            return SagewaiContextLengthError(msg, **kwargs)

        return SagewaiLLMError(msg, **kwargs)
