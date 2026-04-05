# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""HarnessProxy — central orchestrator for the LLM Harness.

Ties together authentication, classification, routing, forwarding,
and spend tracking into a single async request pipeline.

Usage::

    proxy = HarnessProxy(
        store=store,
        router=router,
        backends={"anthropic": anthropic_backend, "openai": openai_backend},
        config=HarnessConfig(),
    )
    identity = await proxy.authenticate(bearer_token)
    response = await proxy.handle_request(
        identity=identity,
        messages=[{"role": "user", "content": "Hello"}],
    )
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from sagewai.harness.backend import LLMBackend
from sagewai.harness.models import (
    HarnessAuditEvent,
    HarnessConfig,
    HarnessIdentity,
    RoutingDecision,
    SpendRecord,
)
from sagewai.harness.router import HarnessRouter
from sagewai.harness.store import InMemoryHarnessStore
from sagewai.observability.costs import (
    MODEL_PRICING,
    CostTracker,
    estimate_tokens_from_text,
)

logger = logging.getLogger(__name__)

# Provider detection prefixes.
_PROVIDER_PREFIXES: list[tuple[str, str]] = [
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("gemini", "google"),
    ("mistral", "mistral"),
    ("llama", "meta"),
]


class HarnessProxy:
    """Central proxy that processes every harness LLM request.

    Orchestrates the full lifecycle of a request:

    1. **Authenticate** — validate the bearer token via the key store.
    2. **Route** — classify complexity, apply policies and budget.
    3. **Forward** — dispatch to the correct LLM backend.
    4. **Record** — persist spend, audit, and optional cost tracking.

    Args:
        store: Harness store (key validation, spend recording, audit).
        router: Routing decision engine.
        backends: Map of provider names to backend implementations
            (e.g. ``{"anthropic": ..., "openai": ...}``).
        config: Global harness configuration.
        cost_tracker: Optional :class:`CostTracker` for integration with
            the observability layer.
    """

    def __init__(
        self,
        *,
        store: InMemoryHarnessStore,
        router: HarnessRouter,
        backends: dict[str, LLMBackend],
        config: HarnessConfig,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._store = store
        self._router = router
        self._backends = backends
        self._config = config
        self._cost_tracker = cost_tracker

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate(self, bearer_token: str) -> HarnessIdentity:
        """Validate a bearer token and return the caller identity.

        Strips the ``Bearer `` prefix before validation.

        Args:
            bearer_token: Raw ``Authorization`` header value.

        Returns:
            The authenticated :class:`HarnessIdentity`.

        Raises:
            HTTPException: 401 if the token is invalid, disabled, or expired.
        """
        token = bearer_token
        if token.startswith("Bearer "):
            token = token[7:]

        identity = await self._store.validate_key(token)
        if identity is None:
            # Lazy import — keep FastAPI optional for pure-SDK usage.
            from fastapi import HTTPException

            raise HTTPException(
                status_code=401,
                detail="Invalid or expired harness key",
            )

        logger.debug(
            "Authenticated user=%s org=%s key=%s",
            identity.user_id,
            identity.org_id,
            identity.key_id,
        )
        return identity

    # ------------------------------------------------------------------
    # Request handling
    # ------------------------------------------------------------------

    async def handle_request(
        self,
        *,
        identity: HarnessIdentity,
        messages: list[dict],
        model: str = "",
        stream: bool = False,
        tools: list[dict] | None = None,
        force_model_header: str | None = None,
        **kwargs: Any,
    ) -> dict | AsyncIterator[dict]:
        """Process a full harness request end-to-end.

        Pipeline:
        1. Route via the classifier + policy engine + budget manager.
        2. Select the provider backend from the resolved model name.
        3. Forward to the backend's ``chat_completion``.
        4. Record spend, audit, and cost tracking metadata.
        5. Attach transparency headers (``_harness`` key) to the response.

        When ``stream=True`` the returned async iterator accumulates
        token counts and records spend after the stream completes.

        Args:
            identity: Authenticated caller identity.
            messages: Conversation messages (OpenAI-format dicts).
            model: Model the client originally requested.
            stream: Whether to stream the response.
            tools: Optional tool/function definitions.
            force_model_header: Explicit model override header value.
            **kwargs: Additional provider-specific parameters forwarded
                to the backend.

        Returns:
            A response dict with an extra ``_harness`` key containing
            transparency metadata, or an ``AsyncIterator[dict]`` when
            streaming.
        """
        start = time.monotonic()

        # Step 1: Route
        decision = await self._router.route(
            identity=identity,
            messages=messages,
            tools=tools,
            requested_model=model,
            force_model_header=force_model_header,
        )

        # Step 2: Select backend
        provider = self._detect_provider(decision.target_model)
        backend = self._backends.get(provider) or self._backends.get("default")
        if backend is None:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=502,
                detail=(
                    f"No backend configured for provider '{provider}' "
                    f"(model={decision.target_model})"
                ),
            )

        # Step 3: Forward
        response = await backend.chat_completion(
            model=decision.target_model,
            messages=messages,
            stream=stream,
            tools=tools,
            **kwargs,
        )

        if stream:
            return self._wrap_stream(
                stream_iter=response,  # type: ignore[arg-type]
                identity=identity,
                decision=decision,
                messages=messages,
                start=start,
            )

        # Step 4: Non-streaming — calculate cost and record
        latency_ms = (time.monotonic() - start) * 1000
        input_tokens, output_tokens = self._extract_tokens(response)
        cost_usd = self._estimate_cost(
            decision.target_model, input_tokens, output_tokens,
        )

        await self._record_spend(
            identity=identity,
            decision=decision,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

        # Step 5: Attach transparency data
        if self._config.transparency_headers:
            response["_harness"] = self._build_transparency_headers(
                decision, cost_usd,
            )

        return response

    # ------------------------------------------------------------------
    # Streaming wrapper
    # ------------------------------------------------------------------

    async def _wrap_stream(
        self,
        *,
        stream_iter: AsyncIterator[dict],
        identity: HarnessIdentity,
        decision: RoutingDecision,
        messages: list[dict],
        start: float,
    ) -> AsyncIterator[dict]:
        """Wrap a streaming response to accumulate tokens and record spend.

        Yields each chunk unmodified while tracking content length for
        cost estimation. After the stream finishes, spend and audit
        records are persisted.

        Args:
            stream_iter: Async iterator of response chunks.
            identity: Authenticated caller identity.
            decision: Routing decision for this request.
            messages: Original request messages (for input token estimation).
            start: Monotonic timestamp when the request started.
        """
        accumulated_content = ""

        async for chunk in stream_iter:
            # Accumulate content for token estimation.
            delta = self._extract_stream_delta(chunk)
            if delta:
                accumulated_content += delta
            yield chunk

        # Stream complete — record spend.
        latency_ms = (time.monotonic() - start) * 1000
        output_tokens = estimate_tokens_from_text(accumulated_content)
        # Estimate input tokens from the original messages.
        input_tokens = sum(
            estimate_tokens_from_text(str(m.get("content", "")))
            for m in messages
        )
        cost_usd = self._estimate_cost(
            decision.target_model, input_tokens, output_tokens,
        )

        await self._record_spend(
            identity=identity,
            decision=decision,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Spend recording
    # ------------------------------------------------------------------

    async def _record_spend(
        self,
        *,
        identity: HarnessIdentity,
        decision: RoutingDecision,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_ms: float,
    ) -> None:
        """Persist spend, audit, and budget records.

        Records are written to:
        1. The spend store (for analytics dashboards).
        2. The audit store (for compliance trails).
        3. The budget manager (for ongoing enforcement).
        4. The optional ``CostTracker`` (for observability).
        """
        # 1. Spend record
        record = SpendRecord(
            user_id=identity.user_id,
            org_id=identity.org_id,
            team_id=identity.team_id,
            project_id=identity.project_id,
            model_requested=decision.original_model,
            model_used=decision.target_model,
            complexity_tier=decision.tier.value,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            policy_applied=decision.policy_applied,
            budget_action=decision.budget_action,
            key_id=identity.key_id,
        )
        await self._store.record_spend(record)

        # 2. Audit event
        audit = HarnessAuditEvent(
            event_type="llm_request",
            user_id=identity.user_id,
            org_id=identity.org_id,
            details={
                "model_requested": decision.original_model,
                "model_used": decision.target_model,
                "tier": decision.tier.value,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost_usd, 6),
                "latency_ms": round(latency_ms, 2),
                "policy": decision.policy_applied,
                "budget_action": decision.budget_action,
            },
        )
        await self._store.record_audit(audit)

        # 3. Budget manager
        self._router._budget_manager.record_spend(
            user_id=identity.user_id,
            team_id=identity.team_id,
            project_id=identity.project_id,
            cost_usd=cost_usd,
        )

        # 4. Optional CostTracker integration
        if self._cost_tracker:
            self._cost_tracker.record_call(
                model=decision.target_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=latency_ms,
            )

        logger.info(
            "Recorded spend: user=%s model=%s tokens=%d+%d cost=$%.4f",
            identity.user_id,
            decision.target_model,
            input_tokens,
            output_tokens,
            cost_usd,
        )

    # ------------------------------------------------------------------
    # Provider detection
    # ------------------------------------------------------------------

    def _detect_provider(self, model: str) -> str:
        """Detect the LLM provider from the model name.

        Returns:
            Provider key matching the ``backends`` dict
            (e.g. ``"anthropic"``, ``"openai"``, ``"google"``).
            Falls back to ``"default"`` if unrecognised.
        """
        lower = model.lower()
        for prefix, provider in _PROVIDER_PREFIXES:
            if lower.startswith(prefix):
                return provider
        return "default"

    # ------------------------------------------------------------------
    # Cost estimation
    # ------------------------------------------------------------------

    def _estimate_cost(
        self, model: str, input_tokens: int, output_tokens: int,
    ) -> float:
        """Estimate USD cost for a request using MODEL_PRICING.

        Performs exact match, then prefix match, then falls back to a
        reasonable default rate.

        Args:
            model: Model identifier.
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.

        Returns:
            Estimated cost in USD.
        """
        # Exact match
        pricing = MODEL_PRICING.get(model)

        # Prefix match
        if pricing is None:
            for key, rates in MODEL_PRICING.items():
                if model.startswith(key):
                    pricing = rates
                    break

        # Default fallback
        if pricing is None:
            pricing = (1.00, 3.00)

        input_rate, output_rate = pricing
        return (
            input_tokens * input_rate + output_tokens * output_rate
        ) / 1_000_000

    # ------------------------------------------------------------------
    # Token extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tokens(response: dict) -> tuple[int, int]:
        """Extract input/output token counts from a provider response.

        Handles both OpenAI (``usage.prompt_tokens``) and Anthropic
        (``usage.input_tokens``) response formats.

        Returns:
            ``(input_tokens, output_tokens)`` tuple; defaults to ``(0, 0)``
            if the response lacks usage data.
        """
        usage = response.get("usage", {})

        # OpenAI format
        input_t = usage.get("prompt_tokens", 0)
        output_t = usage.get("completion_tokens", 0)

        # Anthropic format (takes precedence when present)
        if "input_tokens" in usage:
            input_t = usage["input_tokens"]
        if "output_tokens" in usage:
            output_t = usage["output_tokens"]

        return input_t, output_t

    @staticmethod
    def _extract_stream_delta(chunk: dict) -> str:
        """Extract text content from a single streaming chunk.

        Handles both OpenAI delta format and Anthropic content_block_delta.

        Returns:
            The text delta, or an empty string if the chunk contains no text.
        """
        # OpenAI streaming format
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            content = delta.get("content")
            if content:
                return content

        # Anthropic streaming format
        if chunk.get("type") == "content_block_delta":
            delta = chunk.get("delta", {})
            return delta.get("text", "")

        return ""

    # ------------------------------------------------------------------
    # Transparency headers
    # ------------------------------------------------------------------

    def _build_transparency_headers(
        self, decision: RoutingDecision, cost_usd: float,
    ) -> dict[str, str]:
        """Build transparency metadata for the response.

        These values are attached under the ``_harness`` key in the
        response dict and can also be mapped to HTTP headers by the
        API layer.

        Args:
            decision: The routing decision for this request.
            cost_usd: Estimated cost in USD.

        Returns:
            Dict with transparency keys.
        """
        headers: dict[str, str] = {
            "X-Harness-Model-Used": decision.target_model,
            "X-Harness-Model-Requested": decision.original_model,
            "X-Harness-Complexity-Tier": decision.tier.value,
            "X-Harness-Routing-Reason": decision.reason,
            "X-Harness-Cost-USD": f"{cost_usd:.6f}",
            "X-Harness-Confidence": f"{decision.confidence:.2f}",
        }
        if decision.policy_applied:
            headers["X-Harness-Policy"] = decision.policy_applied
        if decision.budget_action:
            headers["X-Harness-Budget-Action"] = decision.budget_action
        return headers
