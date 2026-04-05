# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LLM backend abstraction for the harness proxy.

Provides a protocol and three implementations for forwarding classified
+ routed requests to LLM providers:

- **AnthropicBackend** — Anthropic Messages API
- **OpenAIBackend** — OpenAI-compatible chat completions API
- **LiteLLMProxyBackend** — delegates to a LiteLLM proxy instance
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)

# Known Anthropic models (updated periodically).
_ANTHROPIC_MODELS = [
    "claude-opus-4-6",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
]


# ── Protocol ──────────────────────────────────────────────────────────


@runtime_checkable
class LLMBackend(Protocol):
    """Protocol for LLM provider backends used by the harness proxy."""

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict],
        stream: bool = False,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict | AsyncIterator[dict]:
        """Send a chat completion request to the provider.

        Args:
            model: Model identifier (e.g. ``claude-sonnet-4-5-20250929``).
            messages: Conversation messages in the provider's format.
            stream: If True, return an async iterator of SSE chunks.
            tools: Optional tool definitions.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            **kwargs: Additional provider-specific parameters.

        Returns:
            Full response dict when ``stream=False``, or an
            ``AsyncIterator[dict]`` of parsed SSE chunks when
            ``stream=True``.
        """
        ...

    async def list_models(self) -> list[str]:
        """Return available model identifiers from the provider."""
        ...


# ── SSE helpers ───────────────────────────────────────────────────────


async def _iter_sse(response: httpx.Response) -> AsyncIterator[dict]:
    """Parse a Server-Sent Events stream into dicts.

    Handles the standard SSE wire format::

        data: {"type": "content_block_delta", ...}
        data: [DONE]
    """
    async for line in response.aiter_lines():
        line = line.strip()
        if not line or line.startswith(":"):
            # Empty keep-alive or comment line.
            continue
        if line.startswith("data: "):
            payload = line[6:]
            if payload == "[DONE]":
                return
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                logger.debug("Skipping non-JSON SSE payload: %s", payload[:120])


# ── Anthropic ─────────────────────────────────────────────────────────


class AnthropicBackend:
    """Forwards requests to the Anthropic Messages API.

    Args:
        api_key: Anthropic API key.
        base_url: Base URL (default ``https://api.anthropic.com``).
        timeout: HTTP timeout in seconds.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        timeout: float = 120,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    # ── Format conversion ─────────────────────────────────────────────

    @staticmethod
    def _to_anthropic_messages(
        messages: list[dict],
    ) -> tuple[str | None, list[dict]]:
        """Convert internal messages to Anthropic format.

        Returns ``(system_prompt, messages)`` since Anthropic expects the
        system prompt as a top-level parameter, not inside messages.
        """
        system: str | None = None
        converted: list[dict] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system = content
            elif role == "assistant":
                converted.append({"role": "assistant", "content": content})
            else:
                converted.append({"role": "user", "content": content})
        return system, converted

    @staticmethod
    def _to_anthropic_tools(tools: list[dict] | None) -> list[dict] | None:
        """Convert OpenAI-style tool defs to Anthropic tool format."""
        if not tools:
            return None
        result: list[dict] = []
        for t in tools:
            if t.get("type") == "function":
                func = t["function"]
                result.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                })
            else:
                # Already in Anthropic format or unknown — pass through.
                result.append(t)
        return result or None

    # ── API calls ─────────────────────────────────────────────────────

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict],
        stream: bool = False,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict | AsyncIterator[dict]:
        """Send a chat completion to the Anthropic Messages API."""
        system, converted = self._to_anthropic_messages(messages)

        payload: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "max_tokens": max_tokens or 4096,
        }
        if system:
            payload["system"] = system
        if temperature is not None:
            payload["temperature"] = temperature
        anthropic_tools = self._to_anthropic_tools(tools)
        if anthropic_tools:
            payload["tools"] = anthropic_tools
        if stream:
            payload["stream"] = True
        payload.update(kwargs)

        if stream:
            return self._stream_completion(payload)
        return await self._sync_completion(payload)

    async def _sync_completion(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/v1/messages",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def _stream_completion(
        self, payload: dict
    ) -> AsyncIterator[dict]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/messages",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for chunk in _iter_sse(resp):
                    yield chunk

    async def list_models(self) -> list[str]:
        """Return known Anthropic models."""
        return list(_ANTHROPIC_MODELS)


# ── OpenAI-compatible ─────────────────────────────────────────────────


class OpenAIBackend:
    """Forwards requests to an OpenAI-compatible chat completions API.

    Works with OpenAI, Azure OpenAI, vLLM, Ollama, and any provider
    that implements the ``/v1/chat/completions`` endpoint.

    Args:
        api_key: API key (sent as ``Bearer`` token).
        base_url: Base URL (default ``https://api.openai.com``).
        timeout: HTTP timeout in seconds.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com",
        timeout: float = 120,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict],
        stream: bool = False,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict | AsyncIterator[dict]:
        """Send a chat completion to an OpenAI-compatible endpoint."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True
        payload.update(kwargs)

        if stream:
            return self._stream_completion(payload)
        return await self._sync_completion(payload)

    async def _sync_completion(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/v1/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def _stream_completion(
        self, payload: dict
    ) -> AsyncIterator[dict]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for chunk in _iter_sse(resp):
                    yield chunk

    async def list_models(self) -> list[str]:
        """Fetch available models from the ``/v1/models`` endpoint."""
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.get(
                    f"{self._base_url}/v1/models",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return [m["id"] for m in data.get("data", [])]
            except httpx.HTTPError:
                logger.warning(
                    "Failed to fetch models from %s", self._base_url
                )
                return []


# ── LiteLLM Proxy ────────────────────────────────────────────────────


class LiteLLMProxyBackend:
    """Forwards requests through a LiteLLM proxy instance.

    Uses the proxy's OpenAI-compatible ``/chat/completions`` endpoint for
    inference, and delegates model listing to
    :class:`~sagewai.integrations.litellm_proxy.LiteLLMProxyClient`.

    Args:
        proxy_url: Base URL of the LiteLLM proxy
            (e.g. ``http://localhost:4000``).
        api_key: Proxy master API key. Default empty.
        timeout: HTTP timeout in seconds.
    """

    def __init__(
        self,
        *,
        proxy_url: str,
        api_key: str = "",
        timeout: float = 120,
    ) -> None:
        self._proxy_url = proxy_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict],
        stream: bool = False,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict | AsyncIterator[dict]:
        """Send a chat completion through the LiteLLM proxy."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True
        payload.update(kwargs)

        if stream:
            return self._stream_completion(payload)
        return await self._sync_completion(payload)

    async def _sync_completion(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._proxy_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def _stream_completion(
        self, payload: dict
    ) -> AsyncIterator[dict]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                f"{self._proxy_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for chunk in _iter_sse(resp):
                    yield chunk

    async def list_models(self) -> list[str]:
        """List models available on the LiteLLM proxy.

        Delegates to :class:`LiteLLMProxyClient` for cached model
        discovery with TTL.
        """
        from sagewai.integrations.litellm_proxy import LiteLLMProxyClient

        client = LiteLLMProxyClient(
            proxy_url=self._proxy_url,
            api_key=self._api_key,
        )
        models = await client.list_models()
        return [m.model_name for m in models if m.model_name]
