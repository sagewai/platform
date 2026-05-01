# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Tests for AgentExecutor's harness-routed path.

When ``ExecutorConfig.harness_proxy`` and ``harness_identity`` are
both set, ``AgentExecutor._run_llm`` calls
``HarnessProxy.handle_request`` instead of ``litellm.acompletion``
directly. Full output, conversation messages, and per-step telemetry
are populated on the returned :class:`StepResult`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sagewai.autopilot._types import AgentKind
from sagewai.autopilot.agent_graph import Agent
from sagewai.autopilot.controller.executor import AgentExecutor, ExecutorConfig
from sagewai.harness.models import HarnessIdentity


def _llm_agent(node_id: str = "scout", prompt_ref: str = "p/test.md") -> Agent:
    return Agent(id=node_id, kind=AgentKind.LLM, prompt_ref=prompt_ref)


def _identity() -> HarnessIdentity:
    return HarnessIdentity(key_id="autopilot-default", user_id="autopilot")


def _harness_response(
    text: str = "full response content",
    model: str = "claude-haiku-4-5-20251001",
    input_tokens: int = 10,
    output_tokens: int = 20,
) -> dict:
    """Build a HarnessProxy response dict mirroring OpenAI-compat shape."""
    return {
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "model": model,
        "_harness": {
            "tier": "simple",
            "policy_applied": None,
            "cost_usd": 0.0042,
            "latency_ms": 850.0,
        },
    }


@pytest.mark.asyncio
async def test_harness_path_invoked_when_proxy_configured():
    """When proxy + identity are set, _run_llm calls proxy.handle_request."""
    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(return_value=_harness_response())

    cfg = ExecutorConfig(harness_proxy=proxy, harness_identity=_identity())
    executor = AgentExecutor(cfg)

    agent = _llm_agent()
    result = await executor.execute(agent, context={"goal": "test"})

    proxy.handle_request.assert_awaited_once()
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_direct_path_still_works_when_no_proxy():
    """When proxy or identity is None, _run_llm falls back to direct litellm."""
    from unittest.mock import patch

    cfg = ExecutorConfig()  # no harness_proxy / harness_identity
    executor = AgentExecutor(cfg)
    agent = _llm_agent()

    # Mock litellm.acompletion at the import path used inside _run_llm_direct
    fake_response = type("R", (), {})()
    fake_response.choices = [type("C", (), {"message": type("M", (), {"content": "direct path text"})()})()]

    with patch("litellm.acompletion", new=AsyncMock(return_value=fake_response)):
        result = await executor.execute(agent, context={"goal": "test"})

    assert result.status == "completed"
    assert result.output_preview == "direct path text"
    # New fields stay None on direct path:
    assert result.output is None
    assert result.messages is None
    assert result.telemetry is None


@pytest.mark.asyncio
async def test_harness_path_captures_full_output_not_just_preview():
    """StepResult.output contains the full LLM response, not the 200-char preview."""
    full_text = "B" * 5000
    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(return_value=_harness_response(text=full_text))

    cfg = ExecutorConfig(harness_proxy=proxy, harness_identity=_identity())
    executor = AgentExecutor(cfg)

    result = await executor.execute(_llm_agent(), context={})

    assert result.output == full_text
    assert result.output_preview == full_text[:200]
    assert len(result.output_preview or "") == 200
    assert len(result.output or "") == 5000


@pytest.mark.asyncio
async def test_harness_path_captures_full_messages():
    """StepResult.messages contains system + user + assistant turns."""
    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(return_value=_harness_response(text="ok"))

    cfg = ExecutorConfig(harness_proxy=proxy, harness_identity=_identity())
    executor = AgentExecutor(cfg)

    result = await executor.execute(_llm_agent(), context={"goal": "do thing"})

    assert result.messages is not None
    assert len(result.messages) == 3
    roles = [m["role"] for m in result.messages]
    assert roles == ["system", "user", "assistant"]
    assert result.messages[2]["content"] == "ok"


@pytest.mark.asyncio
async def test_harness_path_populates_telemetry_from_response():
    """StepResult.telemetry reflects the harness response's usage + _harness blocks."""
    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(
        return_value=_harness_response(
            text="x",
            model="claude-sonnet-4-5-20250929",
            input_tokens=42,
            output_tokens=18,
        )
    )

    cfg = ExecutorConfig(harness_proxy=proxy, harness_identity=_identity())
    executor = AgentExecutor(cfg)

    result = await executor.execute(_llm_agent(), context={})

    assert result.telemetry is not None
    assert result.telemetry.input_tokens == 42
    assert result.telemetry.output_tokens == 18
    assert result.telemetry.model_used == "claude-sonnet-4-5-20250929"
    assert result.telemetry.cost_usd == 0.0042  # from _harness_response stub
    assert result.telemetry.latency_ms == 850.0


@pytest.mark.asyncio
async def test_harness_path_handles_minimal_response():
    """When response lacks usage/_harness blocks, telemetry uses safe defaults."""
    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(
        return_value={
            "choices": [{"message": {"role": "assistant", "content": "minimal"}}],
            # no usage, no _harness, no model
        }
    )

    cfg = ExecutorConfig(harness_proxy=proxy, harness_identity=_identity())
    executor = AgentExecutor(cfg)

    result = await executor.execute(_llm_agent(), context={})

    assert result.status == "completed"
    assert result.output == "minimal"
    assert result.telemetry is not None
    assert result.telemetry.input_tokens == 0
    assert result.telemetry.output_tokens == 0
    # model_used falls back to ExecutorConfig.model when response lacks it
    assert result.telemetry.model_used == cfg.model
    assert result.telemetry.cost_usd == 0.0


@pytest.mark.asyncio
async def test_harness_path_failed_status_on_proxy_exception():
    """When proxy.handle_request raises, StepResult.status is 'failed' and output is None."""
    proxy = AsyncMock()
    proxy.handle_request = AsyncMock(side_effect=RuntimeError("backend down"))

    cfg = ExecutorConfig(harness_proxy=proxy, harness_identity=_identity())
    executor = AgentExecutor(cfg)

    result = await executor.execute(_llm_agent(), context={})

    assert result.status == "failed"
    assert "backend down" in (result.output_preview or "")
    assert result.output is None
    assert result.messages is None
    assert result.telemetry is None
