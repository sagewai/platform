# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LLM provider detection and connection testing.

Probes local servers (Ollama, LM Studio) and tests cloud provider
API keys.  All HTTP calls use a 5-second timeout so the admin UI
never hangs.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

_TIMEOUT = 5.0  # seconds

# ── Ollama ───────────────────────────────────────────────────────────

OLLAMA_DEFAULT = "http://localhost:11434"


async def detect_ollama(
    endpoint: str | None = None,
) -> dict[str, Any]:
    """Probe Ollama and return its model list.

    Returns ``{connected: bool, models: list, error?: str}``.
    """
    url = endpoint or os.environ.get("OLLAMA_HOST", OLLAMA_DEFAULT)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = []
            for m in data.get("models", []):
                details = m.get("details", {})
                models.append(
                    {
                        "name": m.get("name", ""),
                        "size": m.get("size", 0),
                        "modified_at": m.get("modified_at", ""),
                        "parameter_size": details.get(
                            "parameter_size", ""
                        ),
                        "quantization": details.get(
                            "quantization_level", ""
                        ),
                    }
                )
            return {"connected": True, "models": models}
    except Exception as exc:
        return {"connected": False, "models": [], "error": str(exc)}


# ── LM Studio ───────────────────────────────────────────────────────

LMSTUDIO_DEFAULT = "http://localhost:1234/v1"


async def detect_lmstudio(
    endpoint: str | None = None,
) -> dict[str, Any]:
    """Probe LM Studio and return its model list.

    Returns ``{connected: bool, endpoint: str, models: list, error?: str}``.
    """
    url = endpoint or os.environ.get("LMSTUDIO_HOST", LMSTUDIO_DEFAULT)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{url}/models")
            resp.raise_for_status()
            data = resp.json()
            models = [
                {"id": m.get("id", ""), "owned_by": m.get("owned_by", "")}
                for m in data.get("data", [])
            ]
            return {
                "connected": True,
                "endpoint": url,
                "models": models,
            }
    except Exception as exc:
        return {
            "connected": False,
            "endpoint": url,
            "models": [],
            "error": str(exc),
        }


# ── Cloud provider testing ───────────────────────────────────────────

# Map provider name → (base_url, models_path, key_header_name)
_CLOUD_PROVIDERS: dict[str, tuple[str, str, str]] = {
    "openai": (
        "https://api.openai.com/v1",
        "/models",
        "Authorization",
    ),
    "anthropic": (
        "https://api.anthropic.com/v1",
        "/models",
        "x-api-key",
    ),
    "google": (
        "https://generativelanguage.googleapis.com/v1beta",
        "/models",
        "x-goog-api-key",
    ),
    "mistral": (
        "https://api.mistral.ai/v1",
        "/models",
        "Authorization",
    ),
    "groq": (
        "https://api.groq.com/openai/v1",
        "/models",
        "Authorization",
    ),
    "together": (
        "https://api.together.xyz/v1",
        "/models",
        "Authorization",
    ),
    "xai": (
        "https://api.x.ai/v1",
        "/models",
        "Authorization",
    ),
    "perplexity": (
        "https://api.perplexity.ai",
        "/models",
        "Authorization",
    ),
    "cohere": (
        "https://api.cohere.com/v2",
        "/models",
        "Authorization",
    ),
}


async def test_cloud_provider(
    provider_name: str,
    config: dict[str, str],
) -> dict[str, Any]:
    """Test a cloud LLM provider by hitting its models endpoint.

    Returns ``{connected, latency_ms, models?, error?, note?}``.
    """
    info = _CLOUD_PROVIDERS.get(provider_name)
    base_url = config.get("base_url") or (info[0] if info else "")
    models_path = info[1] if info else "/models"
    key_header = info[2] if info else "Authorization"
    api_key = config.get("api_key", "")

    if not base_url:
        return {
            "connected": False,
            "latency_ms": 0,
            "error": f"Unknown provider: {provider_name}",
        }

    headers: dict[str, str] = {}
    if api_key:
        if key_header == "Authorization":
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            headers[key_header] = api_key

    # Anthropic needs an extra header
    if provider_name == "anthropic":
        headers["anthropic-version"] = "2023-06-01"

    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{base_url}{models_path}", headers=headers
            )
        latency = (time.monotonic() - t0) * 1000

        if resp.status_code == 200:
            body = resp.json()
            # Most providers return {"data": [...]} or {"models": [...]}
            models_raw = body.get("data", body.get("models", []))
            model_ids = [
                m.get("id", m.get("name", "")) for m in models_raw
            ][:20]
            return {
                "connected": True,
                "latency_ms": round(latency, 1),
                "models": model_ids,
            }
        else:
            return {
                "connected": False,
                "latency_ms": round(latency, 1),
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
    except Exception as exc:
        return {
            "connected": False,
            "latency_ms": 0,
            "error": str(exc),
        }


# ── Model aggregation ────────────────────────────────────────────────


async def aggregate_available_models(
    providers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collect all available models from configured providers + local.

    Returns a list of ``AvailableModel``-shaped dicts.
    """
    models: list[dict[str, Any]] = []

    # Cloud providers — check env vars for API keys
    for p in providers:
        pname = p.get("provider_name", "")
        api_key = p.get("config", {}).get("api_key", "")
        env_key = p.get("env_var_key", "")
        effective_key = api_key or os.environ.get(env_key, "")
        if not effective_key:
            continue
        result = await test_cloud_provider(
            pname, {"api_key": effective_key}
        )
        if result.get("connected"):
            for mid in result.get("models", []):
                models.append({"id": mid, "provider": pname})

    # Local — Ollama
    ollama = await detect_ollama()
    if ollama["connected"]:
        for m in ollama["models"]:
            models.append(
                {
                    "id": f"ollama/{m['name']}",
                    "provider": "ollama",
                }
            )

    # Local — LM Studio
    lmstudio = await detect_lmstudio()
    if lmstudio["connected"]:
        for m in lmstudio["models"]:
            models.append(
                {
                    "id": m["id"],
                    "provider": "lmstudio",
                }
            )

    return models
