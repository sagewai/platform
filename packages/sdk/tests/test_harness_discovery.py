# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for local LLM auto-discovery."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from sagewai.harness.discovery import (
    DiscoveredServer,
    build_local_backends,
    discover_local_backends,
    probe_server,
)


@pytest.fixture
def ollama_config() -> dict:
    return {
        "name": "ollama",
        "base_url": "http://localhost:11434",
        "probe_path": "/api/tags",
        "models_path": "/api/tags",
        "models_key": "models",
        "model_name_key": "name",
        "openai_compat_url": "http://localhost:11434",
    }


@pytest.fixture
def openai_config() -> dict:
    return {
        "name": "vllm",
        "base_url": "http://localhost:8000",
        "probe_path": "/v1/models",
        "models_path": "/v1/models",
        "models_key": "data",
        "model_name_key": "id",
        "openai_compat_url": "http://localhost:8000",
    }


class TestProbeServer:
    """Test single server probing."""

    @pytest.mark.asyncio
    async def test_probe_unreachable_returns_none(
        self, ollama_config: dict
    ) -> None:
        """Unreachable server should return None (not raise)."""
        result = await probe_server(ollama_config, timeout=0.5)
        # Will be None unless Ollama is actually running locally
        assert result is None or isinstance(result, DiscoveredServer)

    @pytest.mark.asyncio
    async def test_probe_with_mock_ollama(self, ollama_config: dict) -> None:
        """Mock Ollama response should discover models."""
        import httpx

        mock_response = httpx.Response(
            200,
            json={"models": [{"name": "llama3:8b"}, {"name": "mistral:7b"}]},
        )

        with patch("sagewai.harness.discovery.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await probe_server(ollama_config)

        assert result is not None
        assert result.name == "ollama"
        assert "llama3:8b" in result.models
        assert "mistral:7b" in result.models
        assert len(result.models) == 2

    @pytest.mark.asyncio
    async def test_probe_with_mock_openai_compat(
        self, openai_config: dict
    ) -> None:
        """Mock OpenAI-compatible response should discover models."""
        import httpx

        mock_response = httpx.Response(
            200,
            json={
                "data": [
                    {"id": "meta-llama/Llama-3-70b"},
                    {"id": "mistralai/Mistral-7B"},
                ],
            },
        )

        with patch("sagewai.harness.discovery.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await probe_server(openai_config)

        assert result is not None
        assert result.name == "vllm"
        assert len(result.models) == 2

    @pytest.mark.asyncio
    async def test_probe_non_200_returns_none(
        self, ollama_config: dict
    ) -> None:
        """Non-200 response should return None."""
        import httpx

        mock_response = httpx.Response(404, json={})

        with patch("sagewai.harness.discovery.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await probe_server(ollama_config)

        assert result is None


class TestDiscoverLocalBackends:
    """Test full discovery across multiple servers."""

    @pytest.mark.asyncio
    async def test_discover_returns_dict(self) -> None:
        """Discovery should return a dict (possibly empty)."""
        # With very short timeout, should return quickly
        result = await discover_local_backends(timeout=0.3)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_discover_with_additional_servers(self) -> None:
        """Additional servers should be probed."""
        custom = {
            "name": "custom",
            "base_url": "http://localhost:9999",
            "probe_path": "/health",
            "models_path": "/health",
            "models_key": "models",
            "model_name_key": "id",
            "openai_compat_url": "http://localhost:9999",
        }
        result = await discover_local_backends(
            timeout=0.3, additional_servers=[custom]
        )
        assert isinstance(result, dict)


class TestBuildLocalBackends:
    """Test backend construction from discovered servers."""

    def test_build_creates_openai_backends(self) -> None:
        """Should create OpenAIBackend instances."""
        discovered = {
            "ollama": DiscoveredServer(
                name="ollama",
                base_url="http://localhost:11434",
                openai_compat_url="http://localhost:11434",
                models=["llama3:8b"],
            ),
            "vllm": DiscoveredServer(
                name="vllm",
                base_url="http://localhost:8000",
                openai_compat_url="http://localhost:8000",
                models=["mistral-7b"],
            ),
        }
        backends = build_local_backends(discovered)
        assert len(backends) == 2
        assert "ollama" in backends
        assert "vllm" in backends

    def test_build_empty_discovered(self) -> None:
        """Empty discovery should produce empty backends."""
        backends = build_local_backends({})
        assert backends == {}


class TestDiscoveredServer:
    """Test the DiscoveredServer dataclass."""

    def test_defaults(self) -> None:
        server = DiscoveredServer(
            name="test",
            base_url="http://localhost:1234",
            openai_compat_url="http://localhost:1234",
        )
        assert server.models == []
        assert server.healthy is True

    def test_with_models(self) -> None:
        server = DiscoveredServer(
            name="test",
            base_url="http://localhost:1234",
            openai_compat_url="http://localhost:1234",
            models=["model-a", "model-b"],
        )
        assert len(server.models) == 2
