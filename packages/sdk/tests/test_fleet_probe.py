# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for sagewai.fleet.probe — LLM health probes."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from sagewai.fleet.probe import LLMHealthProbe, LLMProbeResult


# ------------------------------------------------------------------
# LLMProbeResult dataclass
# ------------------------------------------------------------------


class TestLLMProbeResult:
    def test_reachable_result(self):
        r = LLMProbeResult(model="gpt-4o", reachable=True, latency_ms=42.5)
        assert r.model == "gpt-4o"
        assert r.reachable is True
        assert r.latency_ms == 42.5
        assert r.error is None

    def test_unreachable_result(self):
        r = LLMProbeResult(model="gpt-4o", reachable=False, error="timeout")
        assert r.reachable is False
        assert r.error == "timeout"
        assert r.latency_ms is None

    def test_defaults(self):
        r = LLMProbeResult(model="x", reachable=True)
        assert r.latency_ms is None
        assert r.error is None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _mock_response(status_code: int, json_data: dict) -> httpx.Response:
    """Create a mock httpx.Response with JSON body."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "http://test"),
    )


# ------------------------------------------------------------------
# probe_ollama
# ------------------------------------------------------------------


class TestProbeOllama:
    @pytest.mark.asyncio
    async def test_success_with_models(self):
        resp = _mock_response(200, {
            "models": [
                {"name": "llama3:8b", "size": 4000000000},
                {"name": "mistral:7b", "size": 3500000000},
            ]
        })
        probe = LLMHealthProbe(timeout=2.0)
        with patch.object(
            httpx.AsyncClient,
            "get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            results = await probe.probe_ollama("http://localhost:11434")

        assert len(results) == 2
        assert results[0].model == "llama3:8b"
        assert results[0].reachable is True
        assert results[0].latency_ms is not None
        assert results[1].model == "mistral:7b"

    @pytest.mark.asyncio
    async def test_success_no_models(self):
        resp = _mock_response(200, {"models": []})
        probe = LLMHealthProbe()
        with patch.object(
            httpx.AsyncClient,
            "get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            results = await probe.probe_ollama()

        assert len(results) == 1
        assert results[0].model == "(none)"
        assert results[0].reachable is True
        assert results[0].error == "No models found"

    @pytest.mark.asyncio
    async def test_http_error(self):
        resp = _mock_response(500, {"error": "internal"})
        probe = LLMHealthProbe()
        with patch.object(
            httpx.AsyncClient,
            "get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            results = await probe.probe_ollama()

        assert len(results) == 1
        assert results[0].reachable is False
        assert "500" in (results[0].error or "")

    @pytest.mark.asyncio
    async def test_connection_error(self):
        probe = LLMHealthProbe(timeout=0.1)
        with patch.object(
            httpx.AsyncClient,
            "get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            results = await probe.probe_ollama()

        assert len(results) == 1
        assert results[0].reachable is False
        assert results[0].error is not None


# ------------------------------------------------------------------
# probe_openai_compatible
# ------------------------------------------------------------------


class TestProbeOpenAICompatible:
    @pytest.mark.asyncio
    async def test_model_found(self):
        resp = _mock_response(200, {
            "data": [
                {"id": "gpt-4o", "object": "model"},
                {"id": "gpt-3.5-turbo", "object": "model"},
            ]
        })
        probe = LLMHealthProbe()
        with patch.object(
            httpx.AsyncClient,
            "get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            result = await probe.probe_openai_compatible(
                "https://api.openai.com", model="gpt-4o"
            )

        assert result.reachable is True
        assert result.model == "gpt-4o"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_model_not_found(self):
        resp = _mock_response(200, {
            "data": [{"id": "gpt-3.5-turbo", "object": "model"}]
        })
        probe = LLMHealthProbe()
        with patch.object(
            httpx.AsyncClient,
            "get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            result = await probe.probe_openai_compatible(
                "https://api.openai.com", model="gpt-4o"
            )

        assert result.reachable is True
        assert "not found" in (result.error or "")

    @pytest.mark.asyncio
    async def test_auth_failure(self):
        resp = _mock_response(401, {"error": "unauthorized"})
        probe = LLMHealthProbe()
        with patch.object(
            httpx.AsyncClient,
            "get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            result = await probe.probe_openai_compatible(
                "https://api.openai.com", api_key="bad-key"
            )

        assert result.reachable is False
        assert "401" in (result.error or "")

    @pytest.mark.asyncio
    async def test_connection_error(self):
        probe = LLMHealthProbe()
        with patch.object(
            httpx.AsyncClient,
            "get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            result = await probe.probe_openai_compatible(
                "https://api.example.com"
            )

        assert result.reachable is False


# ------------------------------------------------------------------
# probe_worker_models
# ------------------------------------------------------------------


class TestProbeWorkerModels:
    @pytest.mark.asyncio
    async def test_ollama_models_grouped(self):
        """Ollama models should be probed via a single /api/tags call."""
        resp = _mock_response(200, {
            "models": [
                {"name": "llama3:8b"},
                {"name": "mistral:7b"},
            ]
        })
        probe = LLMHealthProbe()
        with patch.object(
            httpx.AsyncClient,
            "get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            results = await probe.probe_worker_models(
                ["ollama/llama3:8b", "ollama/mistral:7b"]
            )

        assert len(results) == 2
        assert all(r.reachable for r in results)

    @pytest.mark.asyncio
    async def test_unknown_provider(self):
        """Models with no endpoint mapping should return 'unknown endpoint'."""
        probe = LLMHealthProbe()
        results = await probe.probe_worker_models(["anthropic/claude-3"])
        assert len(results) == 1
        assert results[0].reachable is False
        assert "No endpoint configured" in (results[0].error or "")

    @pytest.mark.asyncio
    async def test_mixed_providers(self):
        """Mix of ollama and openai models."""
        ollama_resp = _mock_response(200, {
            "models": [{"name": "llama3:8b"}]
        })
        openai_resp = _mock_response(200, {
            "data": [{"id": "gpt-4o"}]
        })

        call_count = 0

        async def mock_get(self_client, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "/api/tags" in url:
                return ollama_resp
            return openai_resp

        probe = LLMHealthProbe()
        with patch.object(httpx.AsyncClient, "get", mock_get):
            results = await probe.probe_worker_models(
                ["ollama/llama3:8b", "openai/gpt-4o"],
                endpoints={
                    "ollama": "http://localhost:11434",
                    "openai": "https://api.openai.com",
                },
            )

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_bare_model_treated_as_unknown(self):
        """Models without a provider prefix are treated as unknown provider."""
        probe = LLMHealthProbe()
        results = await probe.probe_worker_models(["llama3:8b"])

        assert len(results) == 1
        assert results[0].reachable is False
        assert "No endpoint configured" in (results[0].error or "")
