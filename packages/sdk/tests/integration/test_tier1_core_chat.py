"""Tier 1: Core agent loop — real LLM calls across all providers.

Scenarios 1-5 from validation plan:
1. Simple chat across all providers
2. Streaming chat across all providers
3. GoogleNativeAgent with Gemini
4. Multi-turn conversation
5. Tool calling with real functions
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from sagewai.core.conversation import ConversationManager
from sagewai.core.session import InMemorySessionStore
from sagewai.engines.google_native import GoogleNativeAgent
from sagewai.engines.universal import UniversalAgent
from sagewai.models.tool import tool

from .conftest import CHAT_MODELS, TOOL_CALLING_MODELS


@dataclass
class ValidationResult:
    scenario: str
    model: str
    passed: bool
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""
    response_preview: str = ""


# --- Scenario 1: Simple chat ---


@pytest.mark.integration
@pytest.mark.parametrize("provider,model_id", CHAT_MODELS)
async def test_simple_chat(provider: str, model_id: str):
    """Each provider returns a coherent response to a simple question."""
    agent = UniversalAgent(name=f"test-{provider}", model=model_id)
    start = time.monotonic()
    response = await agent.chat("What is 2 + 2? Reply with just the number.")
    elapsed = (time.monotonic() - start) * 1000

    assert response is not None
    assert len(response.strip()) > 0
    assert "4" in response, f"Expected '4' in response, got: {response!r}"
    print(f"\n[{provider}/{model_id}] {elapsed:.0f}ms — {response.strip()[:100]}")


# --- Scenario 2: Streaming chat ---


@pytest.mark.integration
@pytest.mark.parametrize("provider,model_id", CHAT_MODELS)
async def test_streaming_chat(provider: str, model_id: str):
    """Streaming returns chunks that assemble into a coherent response."""
    agent = UniversalAgent(name=f"stream-{provider}", model=model_id)
    chunks: list[str] = []
    start = time.monotonic()
    async for chunk in agent.chat_stream("Say hello in exactly 3 words."):
        chunks.append(chunk)
    elapsed = (time.monotonic() - start) * 1000

    full_response = "".join(chunks)
    assert len(chunks) >= 1, "Expected at least one streaming chunk"
    assert len(full_response.strip()) > 0
    print(
        f"\n[{provider}/{model_id}] stream {elapsed:.0f}ms"
        f" — {len(chunks)} chunks — {full_response.strip()[:100]}"
    )


# --- Scenario 3: GoogleNativeAgent ---


@pytest.mark.integration
async def test_google_native_agent(google_key: str):
    """GoogleNativeAgent works with Gemini 2.0 Flash."""
    agent = GoogleNativeAgent(
        name="gemini-native",
        model="gemini-2.5-flash",
    )
    start = time.monotonic()
    response = await agent.chat("What is the capital of France? Reply in one word.")
    elapsed = (time.monotonic() - start) * 1000

    assert "Paris" in response or "paris" in response.lower()
    print(f"\n[google-native] {elapsed:.0f}ms — {response.strip()[:100]}")


# --- Scenario 4: Multi-turn conversation ---


@pytest.mark.integration
@pytest.mark.parametrize(
    "provider,model_id",
    [
        ("openai", "gpt-4o-mini"),
        ("anthropic", "claude-haiku-4-5-20251001"),
        ("google", "gemini/gemini-2.5-flash"),
    ],
)
async def test_multi_turn_conversation(provider: str, model_id: str):
    """ConversationManager maintains context across turns."""
    agent = UniversalAgent(name=f"conv-{provider}", model=model_id)
    session_store = InMemorySessionStore()
    mgr = ConversationManager(agent=agent, session_store=session_store)

    r1 = await mgr.send("My name is Alice.")
    assert r1 is not None and len(r1) > 0

    r2 = await mgr.send("What is my name?")
    assert "Alice" in r2 or "alice" in r2.lower(), f"Expected 'Alice' in: {r2!r}"
    print(f"\n[{provider}/{model_id}] multi-turn OK — r2: {r2.strip()[:100]}")


# --- Scenario 5: Tool calling ---


@tool
async def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    weather_data = {
        "paris": "15\u00b0C, partly cloudy",
        "london": "12\u00b0C, rainy",
        "tokyo": "22\u00b0C, sunny",
    }
    return weather_data.get(city.lower(), f"No data for {city}")


@pytest.mark.integration
@pytest.mark.parametrize("provider,model_id", TOOL_CALLING_MODELS)
async def test_tool_calling(provider: str, model_id: str):
    """Agent uses @tool-decorated function to answer a question."""
    agent = UniversalAgent(
        name=f"tools-{provider}",
        model=model_id,
        tools=[get_weather],
        system_prompt="Use the get_weather tool to answer weather questions.",
    )
    start = time.monotonic()
    response = await agent.chat("What's the weather in Paris?")
    elapsed = (time.monotonic() - start) * 1000

    assert (
        "15" in response or "cloudy" in response.lower() or "paris" in response.lower()
    ), f"Expected weather data in response: {response!r}"
    print(f"\n[{provider}/{model_id}] tool call {elapsed:.0f}ms — {response.strip()[:150]}")
