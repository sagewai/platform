# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Resolve tenant-scoped LLM provider records into LiteLLM call config."""

from __future__ import annotations

from typing import Any

PROVIDER_DEFAULT_MODEL = {
    "openai": "gpt-4o-mini",
    "anthropic": "anthropic/claude-haiku-4-5",
    "google": "gemini/gemini-2.0-flash",
    "groq": "groq/llama-3.3-70b-versatile",
    "mistral": "mistral/mistral-small-latest",
    "together": "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "xai": "xai/grok-2-latest",
    "perplexity": "perplexity/llama-3.1-sonar-small-128k-online",
    "cohere": "command-r",
}

SELF_HOSTED_PROVIDERS = {"ollama", "lmstudio", "vllm"}

SELF_HOSTED_DEFAULT_BASE_URL = {
    "ollama": "http://localhost:11434",
    "lmstudio": "http://localhost:1234/v1",
}


def provider_has_credentials(provider: dict[str, Any]) -> bool:
    provider_name = provider.get("provider_name")
    if provider_name in SELF_HOSTED_PROVIDERS:
        return True
    cfg = provider.get("config") or {}
    return bool(cfg.get("api_key") or provider.get("env_var_set"))


def choose_provider(providers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick default provider first, then the first configured provider."""
    return next(
        (p for p in providers if p.get("default") and provider_has_credentials(p)),
        None,
    ) or next((p for p in providers if provider_has_credentials(p)), None)


def provider_model(provider: dict[str, Any], requested_model: str | None = None) -> str:
    cfg = provider.get("config") or {}
    provider_name = provider.get("provider_name", "")
    model = requested_model or cfg.get("model") or PROVIDER_DEFAULT_MODEL.get(provider_name, "gpt-4o-mini")
    if provider_name == "ollama" and "/" not in model:
        return f"ollama/{model}"
    if provider_name in {"lmstudio", "vllm"} and "/" not in model:
        return f"openai/{model}"
    return model


def litellm_kwargs_for_provider(
    provider: dict[str, Any], *, requested_model: str | None = None
) -> dict[str, Any]:
    """Return kwargs safe to pass to ``litellm.acompletion``."""
    cfg = provider.get("config") or {}
    provider_name = provider.get("provider_name", "")
    kwargs: dict[str, Any] = {"model": provider_model(provider, requested_model)}

    api_key = cfg.get("api_key")
    if api_key:
        kwargs["api_key"] = api_key

    base_url = cfg.get("base_url") or SELF_HOSTED_DEFAULT_BASE_URL.get(provider_name)
    if base_url:
        kwargs["api_base"] = base_url

    if provider_name == "lmstudio" and "api_key" not in kwargs:
        kwargs["api_key"] = "lm-studio"
    elif provider_name == "vllm" and "api_key" not in kwargs:
        kwargs["api_key"] = "vllm"

    return kwargs


def litellm_kwargs_from_providers(
    providers: list[dict[str, Any]], *, requested_model: str | None = None
) -> dict[str, Any]:
    provider = choose_provider(providers)
    if provider is None:
        return {"model": requested_model or "gpt-4o-mini"}
    return litellm_kwargs_for_provider(provider, requested_model=requested_model)
