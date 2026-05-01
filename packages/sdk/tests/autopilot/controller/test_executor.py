# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for AgentExecutor and ExecutorConfig."""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.autopilot._types import AgentKind, MissionState
from sagewai.autopilot.agent_graph import Agent
from sagewai.autopilot.controller.executor import (
    AgentExecutor,
    ExecutorConfig,
    _NO_PROVIDER_SENTINEL,
)

# ── helpers ─────────────────────────────────────────────────────────


def _llm_agent(node_id: str = "scout", prompt_ref: str = "p/test.md") -> Agent:
    return Agent(id=node_id, kind=AgentKind.LLM, prompt_ref=prompt_ref)


def _det_agent(node_id: str = "router") -> Agent:
    return Agent(id=node_id, kind=AgentKind.DETERMINISTIC)


def _mock_litellm_response(text: str) -> MagicMock:
    """Build a minimal litellm response mock."""
    choice = MagicMock()
    choice.message.content = text
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ── ExecutorConfig defaults ──────────────────────────────────────────


def test_executor_config_defaults():
    cfg = ExecutorConfig()
    assert cfg.model == "gpt-4o-mini"
    assert cfg.max_tokens == 2048
    assert cfg.temperature == 0.3


def test_executor_config_frozen():
    cfg = ExecutorConfig()
    with pytest.raises(Exception):
        cfg.model = "gpt-4"  # type: ignore[misc]


def test_executor_config_custom_values():
    cfg = ExecutorConfig(model="claude-3-haiku-20240307", max_tokens=512, temperature=0.0)
    assert cfg.model == "claude-3-haiku-20240307"
    assert cfg.max_tokens == 512
    assert cfg.temperature == 0.0


# ── deterministic agent ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deterministic_agent_returns_completed():
    executor = AgentExecutor()
    result = await executor.execute(_det_agent(), context={})
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_deterministic_agent_output_preview():
    executor = AgentExecutor()
    result = await executor.execute(_det_agent("my-node"), context={})
    assert result.output_preview == "deterministic pass-through"
    assert result.node_id == "my-node"


@pytest.mark.asyncio
async def test_deterministic_agent_ignores_context():
    executor = AgentExecutor()
    ctx = {"slots": "value", "other": 42}
    result = await executor.execute(_det_agent(), context=ctx)
    assert result.status == "completed"


# ── LLM agent — no API key ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_agent_no_api_key_returns_skipped(monkeypatch):
    """When litellm raises AuthenticationError the result is skipped."""
    import litellm.exceptions

    async def _raise(*args, **kwargs):
        raise litellm.exceptions.AuthenticationError(
            "No API key", llm_provider="openai", model="gpt-4o-mini"
        )

    monkeypatch.setattr("litellm.acompletion", _raise)

    executor = AgentExecutor()
    result = await executor.execute(_llm_agent(), context={})
    assert result.status == "skipped"
    assert result.output_preview == _NO_PROVIDER_SENTINEL


@pytest.mark.asyncio
async def test_llm_agent_skipped_result_has_correct_node_id(monkeypatch):
    import litellm.exceptions

    async def _raise(*args, **kwargs):
        raise litellm.exceptions.AuthenticationError(
            "No API key", llm_provider="openai", model="gpt-4o-mini"
        )

    monkeypatch.setattr("litellm.acompletion", _raise)

    executor = AgentExecutor()
    result = await executor.execute(_llm_agent("sentinel-node"), context={})
    assert result.node_id == "sentinel-node"


# ── LLM agent — mocked success ──────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_agent_mocked_returns_completed(monkeypatch):
    response_text = "A" * 300  # longer than 200 chars to test truncation
    monkeypatch.setattr(
        "litellm.acompletion",
        AsyncMock(return_value=_mock_litellm_response(response_text)),
    )

    executor = AgentExecutor()
    result = await executor.execute(_llm_agent(), context={})
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_llm_agent_output_preview_truncated_to_200(monkeypatch):
    response_text = "X" * 500
    monkeypatch.setattr(
        "litellm.acompletion",
        AsyncMock(return_value=_mock_litellm_response(response_text)),
    )

    executor = AgentExecutor()
    result = await executor.execute(_llm_agent(), context={})
    assert result.output_preview is not None
    assert len(result.output_preview) == 200


@pytest.mark.asyncio
async def test_llm_agent_mocked_node_id_preserved(monkeypatch):
    monkeypatch.setattr(
        "litellm.acompletion",
        AsyncMock(return_value=_mock_litellm_response("hello")),
    )

    executor = AgentExecutor()
    result = await executor.execute(_llm_agent("my-llm-node"), context={})
    assert result.node_id == "my-llm-node"


# ── LLM agent — failed call ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_agent_unexpected_error_returns_failed(monkeypatch):
    async def _crash(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("litellm.acompletion", _crash)

    executor = AgentExecutor()
    result = await executor.execute(_llm_agent(), context={})
    assert result.status == "failed"
    assert "connection refused" in (result.output_preview or "")


@pytest.mark.asyncio
async def test_llm_agent_failed_result_does_not_raise(monkeypatch):
    """AgentExecutor.execute must never propagate exceptions."""

    async def _crash(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr("litellm.acompletion", _crash)

    executor = AgentExecutor()
    # Should return normally, not raise
    result = await executor.execute(_llm_agent(), context={})
    assert result.status == "failed"


# ── prompt_ref loading ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_agent_missing_prompt_ref_uses_generic(monkeypatch, tmp_path):
    """A prompt_ref that doesn't exist falls back to the generic prompt."""
    calls: list[dict] = []

    async def _capture(*args, **kwargs):
        calls.append(kwargs)
        return _mock_litellm_response("ok")

    monkeypatch.setattr("litellm.acompletion", _capture)

    agent = _llm_agent(prompt_ref="/nonexistent/path/prompt.md")
    executor = AgentExecutor()
    await executor.execute(agent, context={})

    assert calls
    messages = calls[0]["messages"]
    system_content = next(m["content"] for m in messages if m["role"] == "system")
    assert "Sagewai autopilot" in system_content


@pytest.mark.asyncio
async def test_llm_agent_existing_prompt_ref_is_loaded(monkeypatch, tmp_path):
    """A prompt_ref pointing to an existing file is loaded as the system prompt."""
    prompt_file = tmp_path / "my_prompt.md"
    prompt_file.write_text("You are a test agent. Be concise.")

    calls: list[dict] = []

    async def _capture(*args, **kwargs):
        calls.append(kwargs)
        return _mock_litellm_response("response")

    monkeypatch.setattr("litellm.acompletion", _capture)

    agent = _llm_agent(prompt_ref=str(prompt_file))
    executor = AgentExecutor()
    await executor.execute(agent, context={})

    messages = calls[0]["messages"]
    system_content = next(m["content"] for m in messages if m["role"] == "system")
    assert "You are a test agent" in system_content


# ── integration: context flows between MissionDriver steps ───────────


@pytest.mark.asyncio
async def test_context_accumulates_between_steps(monkeypatch):
    """Output of step N is available to step N+1 via context injection."""
    call_contexts: list[str] = []

    async def _capture(*args, **kwargs):
        # Capture the user message to inspect accumulated context
        user_msg = next(m["content"] for m in kwargs["messages"] if m["role"] == "user")
        call_contexts.append(user_msg)
        return _mock_litellm_response(f"output-{len(call_contexts)}")

    monkeypatch.setattr("litellm.acompletion", _capture)

    # Build a 2-node linear mission
    from sagewai.autopilot._types import MissionState
    from sagewai.autopilot.controller.driver import MissionDriver
    from tests.autopilot.fixtures import make_synthetic_scheduled_blueprint

    bp = make_synthetic_scheduled_blueprint()
    slots = {
        "vendors": ["https://example.com"],
        "schedule": "0 9 * * 1-5",
        "__blueprint_json__": bp.model_dump_json(),
    }
    from sagewai.autopilot.mission import Mission

    mission = Mission(
        mission_id="ms-ctx-test",
        project_id="test-project",
        blueprint_id=bp.id,
        blueprint_version=bp.version,
        slots=slots,
    )
    mission.transition_to(MissionState.APPROVED)
    mission.transition_to(MissionState.SCHEDULED)

    driver = MissionDriver()
    result = await driver.execute(mission)

    assert result.status == "completed"
    assert len(result.steps) == 2
    # The second call should have context from the first step's output
    assert len(call_contexts) == 2
    # Second user message should contain the first step's output key
    assert "step_scout_output" in call_contexts[1]


# ── ExecutorConfig — harness fields ──────────────────────────────────


def test_executor_config_has_optional_harness_fields():
    from sagewai.autopilot.controller.executor import ExecutorConfig

    cfg = ExecutorConfig()
    assert cfg.harness_proxy is None
    assert cfg.harness_identity is None


def test_executor_config_accepts_harness_proxy_and_identity():
    from sagewai.autopilot.controller.executor import ExecutorConfig
    from sagewai.harness.models import HarnessIdentity

    sentinel_proxy = object()  # we don't test the proxy itself here
    identity = HarnessIdentity(key_id="autopilot-default", user_id="autopilot")
    cfg = ExecutorConfig(harness_proxy=sentinel_proxy, harness_identity=identity)
    assert cfg.harness_proxy is sentinel_proxy
    assert cfg.harness_identity is identity
