# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""LLM health probes for fleet workers.

Periodically checks whether workers' declared LLM endpoints are actually
reachable. This is used by the fleet dashboard to show probe status and
by the anomaly detector to flag workers with unreachable models.

Usage::

    from sagewai.fleet.probe import LLMHealthProbe, LLMProbeResult

    probe = LLMHealthProbe(timeout=5.0)
    results = await probe.probe_ollama("http://localhost:11434")
    for r in results:
        print(f"{r.model}: reachable={r.reachable}, latency={r.latency_ms}ms")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class LLMProbeResult:
    """Result of probing a single LLM model endpoint."""

    model: str
    reachable: bool
    latency_ms: float | None = None
    error: str | None = None


class LLMHealthProbe:
    """Probes LLM endpoints to verify worker model declarations.

    Supports Ollama (``/api/tags``) and OpenAI-compatible (``/v1/models``)
    endpoints. Used by the fleet scheduler to update ``WorkerRecord.probe_status``.

    Args:
        timeout: HTTP request timeout in seconds.
    """

    def __init__(self, timeout: float = 5.0) -> None:
        self._timeout = timeout

    async def probe_ollama(
        self,
        base_url: str = "http://localhost:11434",
    ) -> list[LLMProbeResult]:
        """Check Ollama ``/api/tags`` for available models.

        Returns one :class:`LLMProbeResult` per model found, or a single
        result with ``reachable=False`` if the endpoint is unreachable.
        """
        url = f"{base_url.rstrip('/')}/api/tags"
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url)
            elapsed = (time.monotonic() - start) * 1000
            resp.raise_for_status()
            data = resp.json()
            models = data.get("models", [])
            if not models:
                return [
                    LLMProbeResult(
                        model="(none)",
                        reachable=True,
                        latency_ms=elapsed,
                        error="No models found",
                    )
                ]
            return [
                LLMProbeResult(
                    model=m.get("name", "unknown"),
                    reachable=True,
                    latency_ms=elapsed,
                )
                for m in models
            ]
        except httpx.HTTPStatusError as exc:
            elapsed = (time.monotonic() - start) * 1000
            return [
                LLMProbeResult(
                    model="(ollama)",
                    reachable=False,
                    latency_ms=elapsed,
                    error=f"HTTP {exc.response.status_code}",
                )
            ]
        except httpx.RequestError as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.debug("Ollama probe failed for %s: %s", base_url, exc)
            return [
                LLMProbeResult(
                    model="(ollama)",
                    reachable=False,
                    latency_ms=elapsed,
                    error=str(exc),
                )
            ]

    async def probe_openai_compatible(
        self,
        base_url: str,
        api_key: str | None = None,
        model: str = "gpt-4o",
    ) -> LLMProbeResult:
        """Check an OpenAI-compatible ``/v1/models`` endpoint.

        Verifies that the endpoint is reachable and optionally that the
        specific *model* is listed in the response.

        Args:
            base_url: Base URL of the API (e.g. ``https://api.openai.com``).
            api_key: Optional bearer token for authentication.
            model: Model name to look for in the response.
        """
        url = f"{base_url.rstrip('/')}/v1/models"
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
            elapsed = (time.monotonic() - start) * 1000
            resp.raise_for_status()
            data = resp.json()

            # OpenAI format: {"data": [{"id": "gpt-4o", ...}, ...]}
            model_ids = {m.get("id", "") for m in data.get("data", [])}
            if model in model_ids:
                return LLMProbeResult(
                    model=model, reachable=True, latency_ms=elapsed
                )
            return LLMProbeResult(
                model=model,
                reachable=True,
                latency_ms=elapsed,
                error=f"Model '{model}' not found in endpoint",
            )
        except httpx.HTTPStatusError as exc:
            elapsed = (time.monotonic() - start) * 1000
            return LLMProbeResult(
                model=model,
                reachable=False,
                latency_ms=elapsed,
                error=f"HTTP {exc.response.status_code}",
            )
        except httpx.RequestError as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.debug("OpenAI probe failed for %s: %s", base_url, exc)
            return LLMProbeResult(
                model=model,
                reachable=False,
                latency_ms=elapsed,
                error=str(exc),
            )

    async def probe_worker_models(
        self,
        models: list[str],
        endpoints: dict[str, str] | None = None,
    ) -> list[LLMProbeResult]:
        """Probe all models declared by a worker.

        For each model, determines the appropriate probe strategy:

        - Models starting with ``ollama/`` or containing no ``/`` prefix
          are probed via :meth:`probe_ollama` on the endpoint mapped to
          ``"ollama"`` (default: ``http://localhost:11434``).
        - Other models are probed via :meth:`probe_openai_compatible`
          if an endpoint mapping exists for the provider prefix.
        - Models with no matching endpoint return ``reachable=False``
          with an ``"unknown endpoint"`` error.

        Args:
            models: List of model names (e.g. ``["ollama/llama3:8b", "gpt-4o"]``).
            endpoints: Optional mapping of provider prefix to base URL.
                Example: ``{"ollama": "http://gpu-box:11434",
                            "openai": "https://api.openai.com"}``.
        """
        endpoints = endpoints or {}
        results: list[LLMProbeResult] = []

        # Group ollama models to probe once
        ollama_models: set[str] = set()
        other_models: list[tuple[str, str]] = []  # (provider, model_name)

        for m in models:
            if "/" in m:
                provider, name = m.split("/", 1)
                provider = provider.lower()
            else:
                # Bare model names (no provider prefix) — unknown provider.
                # Use the endpoints mapping to route, or report as unconfigured.
                provider = "unknown"
                name = m

            if provider == "ollama":
                ollama_models.add(name)
            else:
                other_models.append((provider, name))

        # Probe Ollama if any ollama models declared
        if ollama_models:
            ollama_url = endpoints.get("ollama", "http://localhost:11434")
            probe_results = await self.probe_ollama(ollama_url)
            found_models = {r.model for r in probe_results if r.reachable}
            for name in ollama_models:
                if name in found_models:
                    # Use the probe result directly
                    match = next(
                        r for r in probe_results if r.model == name
                    )
                    results.append(match)
                else:
                    # Model not found in ollama
                    error = (
                        "Ollama unreachable"
                        if not any(r.reachable for r in probe_results)
                        else f"Model '{name}' not found in Ollama"
                    )
                    results.append(
                        LLMProbeResult(
                            model=f"ollama/{name}",
                            reachable=False,
                            error=error,
                        )
                    )

        # Probe other providers
        for provider, name in other_models:
            base_url = endpoints.get(provider)
            if not base_url:
                results.append(
                    LLMProbeResult(
                        model=f"{provider}/{name}",
                        reachable=False,
                        error=f"No endpoint configured for provider '{provider}'",
                    )
                )
            else:
                result = await self.probe_openai_compatible(
                    base_url=base_url, model=name
                )
                results.append(result)

        return results
