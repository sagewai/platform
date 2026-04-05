# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LiteLLM Proxy client for model discovery and spend tracking."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class LiteLLMModel:
    """A model available on the LiteLLM proxy."""

    model_name: str = ""
    litellm_params: dict[str, Any] = field(default_factory=dict)
    model_info: dict[str, Any] = field(default_factory=dict)

    @property
    def provider(self) -> str:
        """Return the LLM provider name (e.g. 'openai', 'anthropic')."""
        return self.litellm_params.get("custom_llm_provider", "unknown")

    @property
    def max_tokens(self) -> int | None:
        """Return the model's max token limit, if known."""
        return self.model_info.get("max_tokens")


class LiteLLMProxyClient:
    """Client for LiteLLM Proxy API interactions.

    Parameters
    ----------
    proxy_url:
        Base URL of the LiteLLM proxy (e.g., ``http://localhost:4000``).
    api_key:
        Master API key for the proxy.
    cache_ttl:
        TTL in seconds for model list cache. Default: 300 (5 minutes).
    """

    def __init__(
        self,
        *,
        proxy_url: str,
        api_key: str = "",
        cache_ttl: int = 300,
    ) -> None:
        self._proxy_url = proxy_url.rstrip("/")
        self._api_key = api_key
        self._cache_ttl = cache_ttl
        self._model_cache: list[LiteLLMModel] | None = None
        self._cache_updated_at: float = 0

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def health_check(self) -> dict[str, Any]:
        """Check proxy health.

        Returns ``{"healthy": True/False, "status": ...}``.
        """
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(
                    f"{self._proxy_url}/health",
                    headers=self._headers(),
                )
                return {
                    "healthy": resp.status_code == 200,
                    "status": resp.status_code,
                }
            except httpx.HTTPError as exc:
                return {"healthy": False, "error": str(exc)}

    async def list_models(
        self, force_refresh: bool = False
    ) -> list[LiteLLMModel]:
        """List available models from the proxy with caching."""
        now = time.time()
        if (
            not force_refresh
            and self._model_cache is not None
            and (now - self._cache_updated_at) < self._cache_ttl
        ):
            return self._model_cache

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.get(
                    f"{self._proxy_url}/model/info",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError:
                logger.warning(
                    "Failed to fetch models from LiteLLM proxy"
                )
                return self._model_cache or []

        models: list[LiteLLMModel] = []
        for item in data.get("data", []):
            models.append(
                LiteLLMModel(
                    model_name=item.get("model_name", ""),
                    litellm_params=item.get("litellm_params", {}),
                    model_info=item.get("model_info", {}),
                )
            )

        self._model_cache = models
        self._cache_updated_at = now
        return models

    async def get_spend(self) -> dict[str, Any]:
        """Get spend data from the proxy."""
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(
                    f"{self._proxy_url}/spend/logs",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError:
                return {"error": "Failed to fetch spend data"}

    async def get_global_spend(self) -> dict[str, Any]:
        """Get aggregated global spend from the proxy."""
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(
                    f"{self._proxy_url}/global/spend",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError:
                return {"error": "Failed to fetch global spend"}

    async def get_spend_by_model(self) -> list[dict[str, Any]]:
        """Get spend breakdown by model."""
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(
                    f"{self._proxy_url}/spend/logs",
                    headers=self._headers(),
                    params={"group_by": "model"},
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError:
                return []

    # ── Virtual Key Management ─────────────────────────────────────────

    async def create_virtual_key(
        self,
        *,
        key_alias: str,
        max_budget: float | None = None,
        models: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a virtual key on the LiteLLM proxy.

        Args:
            key_alias: Human-readable name (e.g., project name).
            max_budget: Optional spending cap in USD.
            models: Optional list of allowed models.
            metadata: Optional metadata dict (e.g., {"project_id": "..."}).

        Returns:
            Dict with "key" (the generated API key) and other metadata.
        """
        payload: dict[str, Any] = {"key_alias": key_alias}
        if max_budget is not None:
            payload["max_budget"] = max_budget
        if models:
            payload["models"] = models
        if metadata:
            payload["metadata"] = metadata

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self._proxy_url}/key/generate",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def list_virtual_keys(self) -> list[dict[str, Any]]:
        """List all virtual keys on the proxy."""
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(
                    f"{self._proxy_url}/key/info",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json().get("keys", [])
            except httpx.HTTPError:
                return []

    async def delete_virtual_key(self, key: str) -> bool:
        """Delete a virtual key.

        Args:
            key: The API key string to delete.

        Returns:
            True if the key was deleted, False on failure.
        """
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.post(
                    f"{self._proxy_url}/key/delete",
                    headers=self._headers(),
                    json={"keys": [key]},
                )
                return resp.status_code == 200
            except httpx.HTTPError:
                return False
