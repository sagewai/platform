# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
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
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.db.rate_limit import RateLimiter, build_rate_limiter
from sagewai.harness.models import HarnessIdentity
from sagewai.harness.proxy import HarnessProxy

logger = logging.getLogger(__name__)


# ── Per-key request-rate limiting (env-configurable) ─────────────────


def _harness_rate_limit() -> int:
    """Max requests per key per window. ``<= 0`` disables (default 120)."""
    try:
        return int(os.environ.get("SAGEWAI_HARNESS_RATE_LIMIT", "120"))
    except ValueError:
        return 120


def _harness_rate_window() -> float:
    """The rate-limit window in seconds (default 60)."""
    try:
        return float(os.environ.get("SAGEWAI_HARNESS_RATE_WINDOW", "60"))
    except ValueError:
        return 60.0


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


def create_harness_proxy_router(
    proxy: HarnessProxy,
    *,
    rate_limiter: RateLimiter | None = None,
    engine: AsyncEngine | None = None,
) -> APIRouter:
    """Create a FastAPI router with proxy endpoints for AI coding tools.

    Endpoints:
    - ``POST /v1/messages`` — Anthropic Messages API format
    - ``POST /v1/chat/completions`` — OpenAI Chat Completions format
    - ``GET /v1/models`` — List available models

    A per-key request-rate limit (separate from the per-key *budget* caps the
    proxy already enforces) guards every request-serving endpoint: on exceed the
    caller gets HTTP 429 with a ``Retry-After`` header. The limit/window are
    env-configurable (``SAGEWAI_HARNESS_RATE_LIMIT`` / ``_WINDOW``); a limit
    ``<= 0`` disables it. The limiter is shared across worker processes when an
    engine is provided in multi-tenant mode (Postgres-backed), single-process
    in-memory otherwise — see :func:`~sagewai.db.rate_limit.build_rate_limiter`.

    Args:
        proxy: Configured :class:`HarnessProxy` instance.
        rate_limiter: Explicit limiter (tests / DI). Defaults to one chosen by
            :func:`build_rate_limiter` from ``engine``.
        engine: Optional shared engine for a distributed limiter in multi-tenant
            deployments. Ignored when ``rate_limiter`` is given.

    Returns:
        A FastAPI :class:`APIRouter` ready to mount.
    """
    router = APIRouter(tags=["harness-proxy"])

    limiter = rate_limiter if rate_limiter is not None else build_rate_limiter(engine)

    async def _enforce_rate_limit(identity: HarnessIdentity) -> None:
        """429 if ``identity``'s key is over its per-key request-rate budget.

        Keyed by the stable, non-secret ``key_id`` (not the bearer token), so the
        plaintext key never lands in the limiter/store. A limit ``<= 0`` disables
        the check (``RateLimiter.hit`` short-circuits to always-allowed)."""
        limit = _harness_rate_limit()
        window = _harness_rate_window()
        allowed = await limiter.hit(
            f"harness-key:{identity.key_id}", limit=limit, window=window
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded for this key",
                headers={"Retry-After": str(int(window))},
            )

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
        await _enforce_rate_limit(identity)

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
        await _enforce_rate_limit(identity)

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
