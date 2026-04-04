"""Integration tests: custom inference endpoints + UniversalAgent.

Verifies that api_base, api_key, and custom_llm_provider correctly flow
through InferenceParams → _build_litellm_kwargs → litellm.acompletion.
These tests target the capability added in #311 and use direct kwargs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sagewai.engines.universal import UniversalAgent


def _make_response(content: str = "Hello") -> MagicMock:
    resp = MagicMock()
    choice = MagicMock()
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = None
    choice.message = msg
    resp.choices = [choice]
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    return resp


async def _run_agent_with_capture(agent: UniversalAgent) -> dict:
    """Run agent.chat and capture what was passed to litellm."""
    captured: dict = {}

    async def mock_completion(**kwargs):
        captured.update(kwargs)
        return _make_response("ok")

    with patch("litellm.acompletion", side_effect=mock_completion):
        await agent.chat("Hello")

    return captured


@pytest.mark.asyncio
async def test_ollama_api_base_end_to_end():
    """Ollama-style: api_base flows through to litellm."""
    agent = UniversalAgent(
        "ollama-bot", model="ollama/llama3.1:8b",
        api_base="http://localhost:11434",
    )
    captured = await _run_agent_with_capture(agent)
    assert captured["model"] == "ollama/llama3.1:8b"
    assert captured["api_base"] == "http://localhost:11434"


@pytest.mark.asyncio
async def test_lm_studio_end_to_end():
    """LM Studio-style: api_base + api_key flow through."""
    agent = UniversalAgent(
        "lms-bot", model="openai/Mistral-7B",
        api_base="http://localhost:1234/v1",
        api_key="lm-studio",
    )
    captured = await _run_agent_with_capture(agent)
    assert captured["model"] == "openai/Mistral-7B"
    assert captured["api_key"] == "lm-studio"
    assert captured["api_base"] == "http://localhost:1234/v1"


@pytest.mark.asyncio
async def test_llama_cpp_end_to_end():
    """llama.cpp-style: api_base + api_key flow through."""
    agent = UniversalAgent(
        "llcpp-bot", model="openai/local",
        api_base="http://localhost:8080/v1",
        api_key="llama-cpp",
    )
    captured = await _run_agent_with_capture(agent)
    assert captured["api_key"] == "llama-cpp"
    assert "localhost:8080" in captured["api_base"]


@pytest.mark.asyncio
async def test_custom_key_and_base():
    """api_key + api_base both flow to litellm."""
    agent = UniversalAgent(
        "custom-bot", model="openai/my-model",
        api_base="http://myserver:9000/v1",
        api_key="my-secret",
    )
    captured = await _run_agent_with_capture(agent)
    assert captured["api_base"] == "http://myserver:9000/v1"
    assert captured["api_key"] == "my-secret"


@pytest.mark.asyncio
async def test_no_api_base_when_not_needed():
    """Standard OpenAI model does not include api_base in litellm call."""
    agent = UniversalAgent("openai-bot", model="gpt-4o")
    captured = await _run_agent_with_capture(agent)
    assert "api_base" not in captured


@pytest.mark.asyncio
async def test_custom_llm_provider_flows_through():
    """custom_llm_provider forwarded to litellm when set."""
    agent = UniversalAgent(
        "custom-provider-bot", model="my-model",
        api_base="http://myendpoint/v1",
        custom_llm_provider="openai",
    )
    captured = await _run_agent_with_capture(agent)
    assert captured.get("custom_llm_provider") == "openai"


@pytest.mark.asyncio
async def test_two_agents_different_endpoints_independent():
    """Two agents with different api_base values each use their own."""
    agent_a = UniversalAgent(
        "a", model="ollama/llama3", api_base="http://localhost:11434",
    )
    agent_b = UniversalAgent(
        "b", model="openai/mistral", api_base="http://localhost:1234/v1", api_key="lm-studio",
    )

    calls: list[dict] = []

    async def capture(**kwargs):
        calls.append(dict(kwargs))
        return _make_response()

    with patch("litellm.acompletion", side_effect=capture):
        await agent_a.chat("Hi")
        await agent_b.chat("Hi")

    assert calls[0]["api_base"] == "http://localhost:11434"
    assert calls[1]["api_base"] == "http://localhost:1234/v1"
    assert calls[1]["api_key"] == "lm-studio"
