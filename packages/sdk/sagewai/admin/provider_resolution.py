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

import os
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

# Default base URL for a self-hosted provider when its config carries no
# explicit base_url. Reads the same env vars as the provider probes
# (OLLAMA_HOST / LMSTUDIO_HOST), so the bundled compose stack — where these
# point at host.docker.internal — drives inference too, not just model
# detection. Defaults to localhost for plain host installs.
_SELF_HOSTED_BASE_URL_ENV = {
    "ollama": ("OLLAMA_HOST", "http://localhost:11434"),
    "lmstudio": ("LMSTUDIO_HOST", "http://localhost:1234/v1"),
}


def self_hosted_default_base_url(provider_name: str) -> str | None:
    """Default inference base URL for a self-hosted provider, env-aware."""
    spec = _SELF_HOSTED_BASE_URL_ENV.get(provider_name)
    if spec is None:
        return None
    env_var, default = spec
    return os.environ.get(env_var, default)


def provider_has_credentials(provider: dict[str, Any]) -> bool:
    provider_name = provider.get("provider_name")
    if provider_name in SELF_HOSTED_PROVIDERS:
        return True
    cfg = provider.get("config") or {}
    return bool(cfg.get("api_key") or provider.get("env_var_set"))


def _runtime_visible_providers(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse inherited providers for execution-time selection.

    A project-scoped provider shadows an org-shared provider with the same
    provider_name. Runtime calls must not pick an inherited provider just because
    it was created first.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for provider in sorted(
        providers,
        key=lambda p: (
            p.get("project_id") is None,
            str(p.get("created_at") or ""),
        ),
    ):
        name = provider.get("provider_name") or provider.get("id") or str(id(provider))
        if name in by_name:
            continue
        by_name[name] = provider
    return list(by_name.values())


def choose_provider(providers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick a runtime provider with tenant precedence.

    Order:
    1. project-scoped default with credentials
    2. project-scoped credentialed provider
    3. org-shared default with credentials
    4. org-shared credentialed provider

    ``providers`` may contain both the project row and an inherited org row for
    the same provider_name; project rows shadow org rows before default
    selection.
    """
    visible = _runtime_visible_providers(providers)

    def _is_project(p: dict[str, Any]) -> bool:
        return p.get("project_id") is not None

    for predicate in (
        lambda p: _is_project(p) and p.get("default"),
        lambda p: _is_project(p),
        lambda p: not _is_project(p) and p.get("default"),
        lambda p: not _is_project(p),
    ):
        match = next(
            (p for p in visible if predicate(p) and provider_has_credentials(p)),
            None,
        )
        if match is not None:
            return match
    return None


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

    base_url = cfg.get("base_url") or self_hosted_default_base_url(provider_name)
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
