"""Tier 7: Model comparison matrix — same tasks across all models.

Scenarios 32-34:
32. Quality/speed/cost comparison
33. Tool-calling compatibility
34. Streaming reliability
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from sagewai.engines.universal import UniversalAgent
from sagewai.models.tool import tool

from .conftest import CHAT_MODELS, TOOL_CALLING_MODELS


@dataclass
class ModelBenchmark:
    provider: str
    model: str
    scenario: str
    passed: bool = True
    latency_ms: float = 0.0
    response_length: int = 0
    error: str = ""


BENCHMARKS: list[ModelBenchmark] = []


@tool
async def get_date() -> str:
    """Get today's date."""
    return "2026-03-01"


# --- Scenario 32: Quality/speed/cost comparison ---


@pytest.mark.integration
@pytest.mark.parametrize("provider,model_id", CHAT_MODELS)
async def test_model_comparison_quality(provider: str, model_id: str):
    """Same prompt -> every model. Compare quality, speed, cost."""
    agent = UniversalAgent(name=f"bench-{provider}", model=model_id)

    prompt = (
        "Explain the concept of 'dependency injection' in software engineering "
        "in exactly 2 sentences."
    )
    start = time.monotonic()
    try:
        response = await agent.chat(prompt)
        elapsed = (time.monotonic() - start) * 1000
        benchmark = ModelBenchmark(
            provider=provider,
            model=model_id,
            scenario="quality",
            passed=True,
            latency_ms=elapsed,
            response_length=len(response),
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        benchmark = ModelBenchmark(
            provider=provider,
            model=model_id,
            scenario="quality",
            passed=False,
            latency_ms=elapsed,
            error=str(e),
        )

    BENCHMARKS.append(benchmark)
    print(
        f"\n[BENCHMARK] {provider}/{model_id}: "
        f"{benchmark.latency_ms:.0f}ms, {benchmark.response_length} chars"
    )
    assert benchmark.passed, f"Model {model_id} failed: {benchmark.error}"


# --- Scenario 33: Tool-calling compatibility ---


@pytest.mark.integration
@pytest.mark.parametrize("provider,model_id", TOOL_CALLING_MODELS)
async def test_model_tool_calling(provider: str, model_id: str):
    """Same tool-calling task -> each model."""
    agent = UniversalAgent(
        name=f"tool-bench-{provider}",
        model=model_id,
        tools=[get_date],
        system_prompt="Use the get_date tool to answer date questions.",
    )

    start = time.monotonic()
    try:
        response = await agent.chat("What is today's date?")
        elapsed = (time.monotonic() - start) * 1000
        passed = "2026" in response or "March" in response or "03-01" in response
        benchmark = ModelBenchmark(
            provider=provider,
            model=model_id,
            scenario="tool_calling",
            passed=passed,
            latency_ms=elapsed,
            response_length=len(response),
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        benchmark = ModelBenchmark(
            provider=provider,
            model=model_id,
            scenario="tool_calling",
            passed=False,
            latency_ms=elapsed,
            error=str(e),
        )

    BENCHMARKS.append(benchmark)
    print(
        f"\n[TOOL BENCH] {provider}/{model_id}: "
        f"{benchmark.latency_ms:.0f}ms, passed={benchmark.passed}"
    )
    assert benchmark.passed, f"Tool calling failed for {model_id}: {benchmark.error}"


# --- Scenario 34: Streaming reliability ---


@pytest.mark.integration
@pytest.mark.parametrize("provider,model_id", CHAT_MODELS)
async def test_model_streaming(provider: str, model_id: str):
    """Streaming reliability across all models."""
    agent = UniversalAgent(name=f"stream-bench-{provider}", model=model_id)

    start = time.monotonic()
    chunks: list[str] = []
    try:
        async for chunk in agent.chat_stream("Count from 1 to 5."):
            chunks.append(chunk)
        elapsed = (time.monotonic() - start) * 1000
        full = "".join(chunks)
        benchmark = ModelBenchmark(
            provider=provider,
            model=model_id,
            scenario="streaming",
            passed=len(chunks) > 0 and len(full) > 0,
            latency_ms=elapsed,
            response_length=len(full),
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        benchmark = ModelBenchmark(
            provider=provider,
            model=model_id,
            scenario="streaming",
            passed=False,
            latency_ms=elapsed,
            error=str(e),
        )

    BENCHMARKS.append(benchmark)
    print(
        f"\n[STREAM BENCH] {provider}/{model_id}: "
        f"{benchmark.latency_ms:.0f}ms, {len(chunks)} chunks"
    )
    assert benchmark.passed, f"Streaming failed for {model_id}: {benchmark.error}"
