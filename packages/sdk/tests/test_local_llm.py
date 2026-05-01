# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for local LLM and custom inference endpoint passthrough."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sagewai.models.inference import InferenceParams


def test_inference_params_has_api_base():
    params = InferenceParams(api_base="http://localhost:11434")
    assert params.api_base == "http://localhost:11434"


def test_inference_params_has_api_key():
    params = InferenceParams(api_key="my-secret")
    assert params.api_key == "my-secret"


def test_inference_params_has_custom_llm_provider():
    params = InferenceParams(custom_llm_provider="ollama")
    assert params.custom_llm_provider == "ollama"


def test_inference_params_defaults_none():
    params = InferenceParams()
    assert params.api_base is None
    assert params.api_key is None
    assert params.custom_llm_provider is None


def _make_mock_response(content: str = "Hello") -> MagicMock:
    response = MagicMock()
    choice = MagicMock()
    message = MagicMock()
    message.content = content
    message.tool_calls = None
    choice.message = message
    choice.finish_reason = "stop"
    response.choices = [choice]
    response.usage = MagicMock()
    response.usage.prompt_tokens = 10
    response.usage.completion_tokens = 5
    return response


@pytest.mark.asyncio
async def test_api_base_passed_to_litellm():
    """api_base set on agent is forwarded to litellm.acompletion."""
    from sagewai.engines.universal import UniversalAgent

    captured = {}

    async def mock_acompletion(**kwargs):
        captured.update(kwargs)
        return _make_mock_response()

    with patch("litellm.acompletion", side_effect=mock_acompletion):
        agent = UniversalAgent(
            name="test",
            model="ollama/llama3.1:8b",
            api_base="http://localhost:11434",
        )
        await agent.chat("Hello")

    assert captured.get("api_base") == "http://localhost:11434"


@pytest.mark.asyncio
async def test_api_key_passed_to_litellm():
    """api_key set on agent is forwarded to litellm.acompletion."""
    from sagewai.engines.universal import UniversalAgent

    captured = {}

    async def mock_acompletion(**kwargs):
        captured.update(kwargs)
        return _make_mock_response()

    with patch("litellm.acompletion", side_effect=mock_acompletion):
        agent = UniversalAgent(
            name="test",
            model="openai/my-model",
            api_base="http://localhost:1234/v1",
            api_key="lm-studio",
        )
        await agent.chat("Hello")

    assert captured.get("api_key") == "lm-studio"
    assert captured.get("api_base") == "http://localhost:1234/v1"


@pytest.mark.asyncio
async def test_ollama_provider_kwargs_work():
    """providers.ollama() kwargs produce correct litellm call."""
    from sagewai import providers
    from sagewai.engines.universal import UniversalAgent

    captured = {}

    async def mock_acompletion(**kwargs):
        captured.update(kwargs)
        return _make_mock_response("Ollama response")

    with patch("litellm.acompletion", side_effect=mock_acompletion):
        agent = UniversalAgent("bot", **providers.ollama("mistral:7b"))
        await agent.chat("Hello")

    assert captured.get("model") == "ollama/mistral:7b"
    assert "localhost:11434" in captured.get("api_base", "")


@pytest.mark.asyncio
async def test_lm_studio_provider_kwargs_work():
    """providers.lm_studio() kwargs produce correct litellm call."""
    from sagewai import providers
    from sagewai.engines.universal import UniversalAgent

    captured = {}

    async def mock_acompletion(**kwargs):
        captured.update(kwargs)
        return _make_mock_response()

    with patch("litellm.acompletion", side_effect=mock_acompletion):
        agent = UniversalAgent("bot", **providers.lm_studio("Mistral-7B"))
        await agent.chat("Hello")

    assert captured.get("model") == "openai/Mistral-7B"
    assert captured.get("api_key") == "lm-studio"
    assert "/v1" in captured.get("api_base", "")


@pytest.mark.asyncio
async def test_no_api_base_not_sent():
    """When api_base is None, it is NOT included in litellm kwargs."""
    from sagewai.engines.universal import UniversalAgent

    captured = {}

    async def mock_acompletion(**kwargs):
        captured.update(kwargs)
        return _make_mock_response()

    with patch("litellm.acompletion", side_effect=mock_acompletion):
        agent = UniversalAgent("bot", model="gpt-4o")
        await agent.chat("Hello")

    assert "api_base" not in captured
    assert "api_key" not in captured
