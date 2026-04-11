# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Proxy API endpoints for the LLM Harness.

Exposes Anthropic Messages API and OpenAI Chat Completions API formats
so AI coding tools (Claude Code, Cursor, Copilot) can connect directly.

Usage::

    from sagewai.harness.api import create_harness_proxy_router
    from sagewai.harness.proxy import HarnessProxy

    router = create_harness_proxy_router(proxy)
    app.include_router(router)
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator

import httpx
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from sagewai.harness.proxy import HarnessProxy

logger = logging.getLogger(__name__)


# ── Anthropic Messages API models ────────────────────────────────────


class AnthropicMessagesRequest(BaseModel):
    """Anthropic Messages API request body."""

    model: str
    messages: list[dict[str, Any]]
    max_tokens: int = 4096
    stream: bool = False
    system: str | None = None
    tools: list[Any] | None = None
    temperature: float | None = None


class AnthropicMessagesResponse(BaseModel):
    """Anthropic Messages API response body."""

    id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:24]}")
    type: str = "message"
    role: str = "assistant"
    content: list[dict[str, Any]] = Field(default_factory=list)
    model: str = ""
    stop_reason: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)


# ── OpenAI Chat Completions models ──────────────────────────────────


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completions request body."""

    model: str
    messages: list[dict[str, Any]]
    max_tokens: int | None = None
    stream: bool = False
    temperature: float | None = None
    tools: list[Any] | None = None
    tool_choice: Any | None = None
    top_p: float | None = None
    n: int | None = None
    stop: str | list[str] | None = None


# ── SSE formatting helpers ───────────────────────────────────────────


async def _sse_from_anthropic_stream(
    stream: AsyncIterator[dict],
    harness_headers: dict[str, str] | None,
) -> AsyncIterator[str]:
    """Convert an Anthropic-style streaming response to SSE text lines."""
    async for chunk in stream:
        yield f"data: {json.dumps(chunk)}\n\n"
    # Emit final event with harness metadata if available.
    if harness_headers:
        meta = {"type": "harness_metadata", "headers": harness_headers}
        yield f"data: {json.dumps(meta)}\n\n"
    yield "data: [DONE]\n\n"


async def _sse_from_openai_stream(
    stream: AsyncIterator[dict],
    harness_headers: dict[str, str] | None,
) -> AsyncIterator[str]:
    """Convert an OpenAI-style streaming response to SSE text lines."""
    async for chunk in stream:
        yield f"data: {json.dumps(chunk)}\n\n"
    if harness_headers:
        meta = {"harness_metadata": harness_headers}
        yield f"data: {json.dumps(meta)}\n\n"
    yield "data: [DONE]\n\n"


# ── Router factory ───────────────────────────────────────────────────


def create_harness_proxy_router(proxy: HarnessProxy) -> APIRouter:
    """Create a FastAPI router with proxy endpoints for AI coding tools.

    Endpoints:
    - ``POST /v1/messages`` — Anthropic Messages API format
    - ``POST /v1/chat/completions`` — OpenAI Chat Completions format
    - ``GET /v1/models`` — List available models

    Args:
        proxy: Configured :class:`HarnessProxy` instance.

    Returns:
        A FastAPI :class:`APIRouter` ready to mount.
    """
    router = APIRouter(tags=["harness-proxy"])

    # TODO: Add rate limiting middleware per key/user

    # ── Anthropic Messages API ───────────────────────────────────

    @router.post("/v1/messages")
    async def anthropic_messages(
        body: AnthropicMessagesRequest,
        authorization: str = Header(..., alias="Authorization"),
        x_harness_force_model: str | None = Header(
            None, alias="X-Harness-Force-Model",
        ),
    ) -> Any:
        """Proxy endpoint for the Anthropic Messages API format.

        Authenticates the caller via the Bearer token, classifies
        the request complexity, routes to the appropriate model,
        and forwards to the configured backend.
        """
        identity = await proxy.authenticate(authorization)

        # Build messages list — inject system as a system-role message
        # so the proxy pipeline can process it uniformly.
        messages: list[dict[str, Any]] = []
        if body.system:
            messages.append({"role": "system", "content": body.system})
        messages.extend(body.messages)

        response = await proxy.handle_request(
            identity=identity,
            messages=messages,
            model=body.model,
            stream=body.stream,
            tools=body.tools,
            force_model_header=x_harness_force_model,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )

        if body.stream:
            # Extract harness metadata before streaming.
            harness_headers: dict[str, str] | None = None
            if hasattr(proxy, "_config") and proxy._config.transparency_headers:
                harness_headers = {}  # Will be sent at end of stream
            return StreamingResponse(
                _sse_from_anthropic_stream(response, harness_headers),  # type: ignore[arg-type]
                media_type="text/event-stream",
                headers=_transparency_response_headers(None),
            )

        # Non-streaming — build Anthropic-format response.
        harness_meta = response.pop("_harness", {})
        return _with_transparency_headers(
            _to_anthropic_response(response, body.model),
            harness_meta,
        )

    # ── OpenAI Chat Completions ──────────────────────────────────

    @router.post("/v1/chat/completions")
    async def chat_completions(
        body: ChatCompletionRequest,
        authorization: str = Header(..., alias="Authorization"),
        x_harness_force_model: str | None = Header(
            None, alias="X-Harness-Force-Model",
        ),
    ) -> Any:
        """Proxy endpoint for the OpenAI Chat Completions API format.

        Same pipeline as the Anthropic endpoint but speaks OpenAI wire
        format on both input and output.
        """
        identity = await proxy.authenticate(authorization)

        kwargs: dict[str, Any] = {}
        if body.temperature is not None:
            kwargs["temperature"] = body.temperature
        if body.max_tokens is not None:
            kwargs["max_tokens"] = body.max_tokens

        response = await proxy.handle_request(
            identity=identity,
            messages=body.messages,
            model=body.model,
            stream=body.stream,
            tools=body.tools,
            force_model_header=x_harness_force_model,
            **kwargs,
        )

        if body.stream:
            harness_headers: dict[str, str] | None = None
            if hasattr(proxy, "_config") and proxy._config.transparency_headers:
                harness_headers = {}
            return StreamingResponse(
                _sse_from_openai_stream(response, harness_headers),  # type: ignore[arg-type]
                media_type="text/event-stream",
                headers=_transparency_response_headers(None),
            )

        harness_meta = response.pop("_harness", {})
        return _with_transparency_headers(response, harness_meta)

    # ── Models listing ───────────────────────────────────────────

    @router.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        """List models available through the harness.

        Aggregates models from all configured backends and the tier
        config.
        """
        models: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Tier config models are always available.
        tier_config = proxy._config.default_tier_config
        for tier_name in ("simple", "medium", "complex"):
            model_id = getattr(tier_config, tier_name)
            if model_id not in seen:
                seen.add(model_id)
                models.append({
                    "id": model_id,
                    "object": "model",
                    "owned_by": "harness",
                })

        # Backend models.
        for provider, backend in proxy._backends.items():
            try:
                backend_models = await backend.list_models()
                for mid in backend_models:
                    if mid not in seen:
                        seen.add(mid)
                        models.append({
                            "id": mid,
                            "object": "model",
                            "owned_by": provider,
                        })
            except (httpx.HTTPError, OSError):
                logger.warning(
                    "Failed to list models from backend '%s'", provider,
                )

        return {"object": "list", "data": models}

    return router


# ── Helpers ──────────────────────────────────────────────────────────


def _to_anthropic_response(
    response: dict[str, Any], requested_model: str,
) -> dict[str, Any]:
    """Convert a backend response dict to Anthropic Messages format.

    Handles both native Anthropic responses (pass-through) and
    OpenAI-format responses (content extraction + conversion).
    """
    # Already in Anthropic format.
    if response.get("type") == "message":
        return response

    # OpenAI format → Anthropic format.
    content: list[dict[str, Any]] = []
    choices = response.get("choices", [])
    if choices:
        msg = choices[0].get("message", {})
        text = msg.get("content", "")
        if text:
            content.append({"type": "text", "text": text})

    usage = response.get("usage", {})
    return AnthropicMessagesResponse(
        model=response.get("model", requested_model),
        content=content,
        stop_reason=_map_finish_reason(
            choices[0].get("finish_reason") if choices else None,
        ),
        usage={
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    ).model_dump()


def _map_finish_reason(reason: str | None) -> str | None:
    """Map OpenAI finish_reason to Anthropic stop_reason."""
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
    }
    if reason is None:
        return None
    return mapping.get(reason, reason)


def _transparency_response_headers(
    harness_meta: dict[str, str] | None,
) -> dict[str, str]:
    """Build HTTP response headers from harness metadata."""
    headers: dict[str, str] = {}
    if harness_meta:
        for key, value in harness_meta.items():
            if key.startswith("X-Harness-"):
                headers[key] = value
    return headers


def _with_transparency_headers(
    response: dict[str, Any],
    harness_meta: dict[str, str],
) -> dict[str, Any]:
    """Attach harness transparency metadata to a JSON response.

    Copies X-Harness-* keys into the response under ``_harness`` for
    clients that inspect the body rather than headers.
    """
    if harness_meta:
        response["_harness"] = harness_meta
    return response
