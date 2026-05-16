# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sagewai.integrations.litellm_proxy."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from sagewai.integrations.litellm_proxy import (
    LiteLLMModel,
    LiteLLMProxyClient,
)


# ── LiteLLMModel dataclass ──────────────────────────────────────────────


class TestLiteLLMModel:
    def test_provider_default(self):
        model = LiteLLMModel(model_name="gpt-4o")
        assert model.provider == "unknown"

    def test_provider_from_params(self):
        model = LiteLLMModel(
            model_name="gpt-4o",
            litellm_params={"custom_llm_provider": "openai"},
        )
        assert model.provider == "openai"

    def test_max_tokens_none(self):
        model = LiteLLMModel(model_name="gpt-4o")
        assert model.max_tokens is None

    def test_max_tokens_present(self):
        model = LiteLLMModel(
            model_name="gpt-4o",
            model_info={"max_tokens": 128000},
        )
        assert model.max_tokens == 128000


# ── LiteLLMProxyClient ──────────────────────────────────────────────────


def _make_response(status_code: int = 200, json_data=None):
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=json_data or {},
        request=httpx.Request("GET", "http://test"),
    )


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self):
        client = LiteLLMProxyClient(proxy_url="http://localhost:4000")

        mock_resp = _make_response(200)
        with patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp
        ):
            result = await client.health_check()

        assert result["healthy"] is True
        assert result["status"] == 200

    @pytest.mark.asyncio
    async def test_unhealthy_status(self):
        client = LiteLLMProxyClient(proxy_url="http://localhost:4000")

        mock_resp = _make_response(503)
        with patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp
        ):
            result = await client.health_check()

        assert result["healthy"] is False
        assert result["status"] == 503

    @pytest.mark.asyncio
    async def test_connection_error(self):
        client = LiteLLMProxyClient(proxy_url="http://localhost:4000")

        with patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            result = await client.health_check()

        assert result["healthy"] is False
        assert "error" in result


class TestListModels:
    @pytest.mark.asyncio
    async def test_parses_response(self):
        client = LiteLLMProxyClient(proxy_url="http://localhost:4000")

        mock_resp = _make_response(
            200,
            {
                "data": [
                    {
                        "model_name": "gpt-4o",
                        "litellm_params": {"custom_llm_provider": "openai"},
                        "model_info": {"max_tokens": 128000},
                    },
                    {
                        "model_name": "claude-sonnet-4-20250514",
                        "litellm_params": {"custom_llm_provider": "anthropic"},
                        "model_info": {"max_tokens": 200000},
                    },
                ]
            },
        )
        with patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp
        ):
            models = await client.list_models()

        assert len(models) == 2
        assert models[0].model_name == "gpt-4o"
        assert models[0].provider == "openai"
        assert models[0].max_tokens == 128000
        assert models[1].model_name == "claude-sonnet-4-20250514"
        assert models[1].provider == "anthropic"

    @pytest.mark.asyncio
    async def test_caching(self):
        client = LiteLLMProxyClient(
            proxy_url="http://localhost:4000", cache_ttl=300
        )

        mock_resp = _make_response(200, {"data": [{"model_name": "gpt-4o"}]})
        with patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_get:
            first = await client.list_models()
            second = await client.list_models()

        # Only one HTTP call — second hit the cache
        assert mock_get.call_count == 1
        assert first is second

    @pytest.mark.asyncio
    async def test_force_refresh(self):
        client = LiteLLMProxyClient(
            proxy_url="http://localhost:4000", cache_ttl=300
        )

        mock_resp = _make_response(200, {"data": [{"model_name": "gpt-4o"}]})
        with patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_get:
            await client.list_models()
            await client.list_models(force_refresh=True)

        assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_cache_expiry(self):
        client = LiteLLMProxyClient(
            proxy_url="http://localhost:4000", cache_ttl=1
        )

        mock_resp = _make_response(200, {"data": [{"model_name": "gpt-4o"}]})
        with patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_get:
            await client.list_models()
            # Artificially expire the cache
            client._cache_updated_at = time.time() - 10
            await client.list_models()

        assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_error_returns_cached(self):
        client = LiteLLMProxyClient(proxy_url="http://localhost:4000")

        mock_resp = _make_response(200, {"data": [{"model_name": "gpt-4o"}]})
        with patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp
        ):
            first = await client.list_models()

        # Now force a refresh that fails
        with patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("down"),
        ):
            second = await client.list_models(force_refresh=True)

        assert len(second) == 1
        assert second[0].model_name == "gpt-4o"

    @pytest.mark.asyncio
    async def test_error_no_cache_returns_empty(self):
        client = LiteLLMProxyClient(proxy_url="http://localhost:4000")

        with patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("down"),
        ):
            result = await client.list_models()

        assert result == []


class TestGetSpend:
    @pytest.mark.asyncio
    async def test_success(self):
        client = LiteLLMProxyClient(proxy_url="http://localhost:4000")

        mock_resp = _make_response(
            200, {"spend": 42.50, "logs": []}
        )
        with patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp
        ):
            result = await client.get_spend()

        assert result["spend"] == 42.50

    @pytest.mark.asyncio
    async def test_failure(self):
        client = LiteLLMProxyClient(proxy_url="http://localhost:4000")

        with patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("down"),
        ):
            result = await client.get_spend()

        assert "error" in result


class TestHeaders:
    def test_no_api_key(self):
        client = LiteLLMProxyClient(proxy_url="http://localhost:4000")
        headers = client._headers()
        assert "Authorization" not in headers

    def test_with_api_key(self):
        client = LiteLLMProxyClient(
            proxy_url="http://localhost:4000", api_key="sk-test"
        )
        headers = client._headers()
        assert headers["Authorization"] == "Bearer sk-test"

    def test_url_trailing_slash_stripped(self):
        client = LiteLLMProxyClient(
            proxy_url="http://localhost:4000/"
        )
        assert client._proxy_url == "http://localhost:4000"


# ── Global Spend tests ─────────────────────────────────────────────


class TestGetGlobalSpend:
    @pytest.mark.asyncio
    async def test_success(self):
        client = LiteLLMProxyClient(proxy_url="http://proxy:4000")
        resp = _make_response(200, {"total_spend": 42.5, "currency": "USD"})
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=resp):
            result = await client.get_global_spend()
        assert result["total_spend"] == 42.5

    @pytest.mark.asyncio
    async def test_failure(self):
        client = LiteLLMProxyClient(proxy_url="http://proxy:4000")
        with patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("fail"),
        ):
            result = await client.get_global_spend()
        assert "error" in result


class TestGetSpendByModel:
    @pytest.mark.asyncio
    async def test_success(self):
        client = LiteLLMProxyClient(proxy_url="http://proxy:4000")
        resp = _make_response(
            200, [{"model": "gpt-4o", "spend": 20.0}, {"model": "claude", "spend": 15.0}]
        )
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=resp):
            result = await client.get_spend_by_model()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_failure_returns_empty(self):
        client = LiteLLMProxyClient(proxy_url="http://proxy:4000")
        with patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("fail"),
        ):
            result = await client.get_spend_by_model()
        assert result == []


# ── Virtual Key tests ───────────────────────────────────────────────


class TestCreateVirtualKey:
    @pytest.mark.asyncio
    async def test_success(self):
        client = LiteLLMProxyClient(proxy_url="http://proxy:4000", api_key="sk-master")
        resp = _make_response(200, {"key": "sk-generated", "key_alias": "proj"})
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp):
            result = await client.create_virtual_key(
                key_alias="proj", max_budget=100.0, models=["gpt-4o"],
            )
        assert result["key"] == "sk-generated"


class TestListVirtualKeys:
    @pytest.mark.asyncio
    async def test_success(self):
        client = LiteLLMProxyClient(proxy_url="http://proxy:4000")
        resp = _make_response(200, {"keys": [{"key_alias": "a"}, {"key_alias": "b"}]})
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=resp):
            result = await client.list_virtual_keys()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_failure_returns_empty(self):
        client = LiteLLMProxyClient(proxy_url="http://proxy:4000")
        with patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("fail"),
        ):
            result = await client.list_virtual_keys()
        assert result == []


class TestDeleteVirtualKey:
    @pytest.mark.asyncio
    async def test_success(self):
        client = LiteLLMProxyClient(proxy_url="http://proxy:4000")
        resp = _make_response(200)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp):
            result = await client.delete_virtual_key("sk-to-delete")
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self):
        client = LiteLLMProxyClient(proxy_url="http://proxy:4000")
        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("fail"),
        ):
            result = await client.delete_virtual_key("sk-fail")
        assert result is False
