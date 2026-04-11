"""Tests for budget enforcement in BaseAgent._call_llm."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.events import AgentEvent
from sagewai.errors import SagewaiBudgetExceededError
from sagewai.models.message import ChatMessage, UsageInfo


@dataclass
class FakeBudgetCheckResult:
    allowed: bool
    action: str
    reason: str | None = None
    daily_spend: float = 0.0
    monthly_spend: float = 0.0


class MockAgent(BaseAgent):
    """Test agent that returns predetermined responses with usage info."""

    def __init__(self, responses: list[ChatMessage] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._responses = list(responses or [])
        self._call_count = 0

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        if self._responses:
            response = self._responses[self._call_count % len(self._responses)]
        else:
            response = ChatMessage.assistant("Hello!")
        self._call_count += 1
        return response


def _make_response_with_usage(
    content: str = "Hello!",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> ChatMessage:
    msg = ChatMessage.assistant(content)
    msg.usage = UsageInfo(input_tokens=input_tokens, output_tokens=output_tokens)
    return msg


@pytest.mark.asyncio
async def test_no_budget_manager_noop():
    """Agent works normally without budget_manager."""
    agent = MockAgent(
        responses=[_make_response_with_usage()],
        name="test-agent",
        model="mock",
    )
    result = await agent.chat("Hi")
    assert result == "Hello!"
    assert agent._call_count == 1
    assert agent._budget_manager is None


@pytest.mark.asyncio
async def test_budget_allowed_proceeds():
    """Budget check returns allowed=True, LLM call proceeds normally."""
    bm = MagicMock()
    bm.check_budget.return_value = FakeBudgetCheckResult(
        allowed=True, action="allow"
    )
    bm.record_spend = MagicMock()

    agent = MockAgent(
        responses=[_make_response_with_usage()],
        name="test-agent",
        model="mock",
    )
    agent._budget_manager = bm

    result = await agent.chat("Hi")
    assert result == "Hello!"
    assert agent._call_count == 1
    bm.check_budget.assert_called_with("test-agent")


@pytest.mark.asyncio
async def test_budget_warn_emits_event():
    """Budget warn action emits BUDGET_WARNING but doesn't block."""
    bm = MagicMock()
    bm.check_budget.return_value = FakeBudgetCheckResult(
        allowed=False, action="warn", reason="Daily limit at 90%"
    )
    bm.record_spend = MagicMock()

    agent = MockAgent(
        responses=[_make_response_with_usage()],
        name="test-agent",
        model="mock",
    )
    agent._budget_manager = bm

    events: list[tuple[AgentEvent, dict]] = []

    async def capture_event(event, data):
        events.append((event, data))

    agent.on_event(capture_event)

    result = await agent.chat("Hi")
    assert result == "Hello!"
    assert agent._call_count == 1

    budget_events = [e for e in events if e[0] == AgentEvent.BUDGET_WARNING]
    assert len(budget_events) == 1
    assert budget_events[0][1]["agent"] == "test-agent"
    assert budget_events[0][1]["reason"] == "Daily limit at 90%"


@pytest.mark.asyncio
async def test_budget_throttle_swaps_model():
    """Budget throttle swaps to fallback model for the LLM call."""
    bm = MagicMock()
    bm.check_budget.return_value = FakeBudgetCheckResult(
        allowed=False, action="throttle", reason="Over daily budget"
    )
    bm.get_fallback_model.return_value = "gpt-4o-mini"
    bm.record_spend = MagicMock()

    captured_models: list[str] = []

    class ModelCapturingAgent(MockAgent):
        async def _invoke_llm(self, messages, tools, *, model_override=None):
            captured_models.append(model_override or self.config.model)
            return _make_response_with_usage()

    agent = ModelCapturingAgent(name="test-agent", model="gpt-4o")
    agent._budget_manager = bm

    events: list[tuple[AgentEvent, dict]] = []

    async def capture_event(event, data):
        events.append((event, data))

    agent.on_event(capture_event)

    result = await agent.chat("Hi")
    assert result == "Hello!"

    # The LLM call should have used the fallback model via model_override
    assert captured_models == ["gpt-4o-mini"]
    # self.config.model is NEVER mutated (concurrency-safe)
    assert agent.config.model == "gpt-4o"

    throttle_events = [e for e in events if e[0] == AgentEvent.BUDGET_THROTTLED]
    assert len(throttle_events) == 1
    assert throttle_events[0][1]["original_model"] == "gpt-4o"
    assert throttle_events[0][1]["fallback_model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_budget_stop_raises_error():
    """Budget stop raises SagewaiBudgetExceededError."""
    bm = MagicMock()
    bm.check_budget.return_value = FakeBudgetCheckResult(
        allowed=False, action="stop", reason="Monthly budget exceeded"
    )

    agent = MockAgent(
        responses=[_make_response_with_usage()],
        name="test-agent",
        model="mock",
    )
    agent._budget_manager = bm

    events: list[tuple[AgentEvent, dict]] = []

    async def capture_event(event, data):
        events.append((event, data))

    agent.on_event(capture_event)

    with pytest.raises(SagewaiBudgetExceededError) as exc_info:
        await agent.chat("Hi")

    assert exc_info.value.agent_name == "test-agent"
    assert "Monthly budget exceeded" in exc_info.value.reason
    assert agent._call_count == 0  # LLM was never called

    exceeded_events = [e for e in events if e[0] == AgentEvent.BUDGET_EXCEEDED]
    assert len(exceeded_events) == 1


@pytest.mark.asyncio
async def test_budget_spend_recorded():
    """Cost is recorded via record_spend after LLM call."""
    bm = MagicMock()
    bm.check_budget.return_value = FakeBudgetCheckResult(
        allowed=True, action="allow"
    )
    bm.record_spend = MagicMock()

    agent = MockAgent(
        responses=[_make_response_with_usage(input_tokens=100, output_tokens=50)],
        name="test-agent",
        model="mock",
    )
    agent._budget_manager = bm

    await agent.chat("Hi")

    bm.record_spend.assert_called_once()
    call_kwargs = bm.record_spend.call_args
    assert call_kwargs.kwargs["agent_name"] == "test-agent"
    assert call_kwargs.kwargs["cost_usd"] > 0


@pytest.mark.asyncio
async def test_budget_async_manager():
    """Budget manager with async methods works correctly."""
    bm = MagicMock()
    bm.check_budget = AsyncMock(
        return_value=FakeBudgetCheckResult(allowed=True, action="allow")
    )
    bm.record_spend = AsyncMock()

    agent = MockAgent(
        responses=[_make_response_with_usage()],
        name="test-agent",
        model="mock",
    )
    agent._budget_manager = bm

    result = await agent.chat("Hi")
    assert result == "Hello!"
    bm.check_budget.assert_awaited_once_with("test-agent")
    bm.record_spend.assert_awaited_once()


@pytest.mark.asyncio
async def test_budget_throttle_no_fallback_warns():
    """Throttle with no fallback model emits warning and continues."""
    bm = MagicMock()
    bm.check_budget.return_value = FakeBudgetCheckResult(
        allowed=False, action="throttle", reason="Over budget"
    )
    bm.get_fallback_model.return_value = None
    bm.record_spend = MagicMock()

    agent = MockAgent(
        responses=[_make_response_with_usage()],
        name="test-agent",
        model="gpt-4o",
    )
    agent._budget_manager = bm

    events: list[tuple[AgentEvent, dict]] = []

    async def capture_event(event, data):
        events.append((event, data))

    agent.on_event(capture_event)

    result = await agent.chat("Hi")
    assert result == "Hello!"
    assert agent.config.model == "gpt-4o"  # Model unchanged

    warn_events = [e for e in events if e[0] == AgentEvent.BUDGET_WARNING]
    assert len(warn_events) == 1


@pytest.mark.asyncio
async def test_budget_dict_result():
    """Budget manager returning dict (PostgresBudgetManager style) works."""
    bm = MagicMock()
    bm.check_budget = AsyncMock(return_value={
        "allowed": False,
        "action": "stop",
        "reason": "Daily budget exceeded: $5.00 > $4.00",
        "daily_spend": 5.0,
        "monthly_spend": 15.0,
    })

    agent = MockAgent(
        responses=[_make_response_with_usage()],
        name="test-agent",
        model="mock",
    )
    agent._budget_manager = bm

    with pytest.raises(SagewaiBudgetExceededError) as exc_info:
        await agent.chat("Hi")

    assert exc_info.value.agent_name == "test-agent"
    assert "$5.00" in exc_info.value.reason


@pytest.mark.asyncio
async def test_budget_throttle_does_not_mutate_config():
    """Throttle passes model_override instead of mutating self.config.model."""
    bm = MagicMock()
    bm.check_budget.return_value = FakeBudgetCheckResult(
        allowed=False, action="throttle", reason="Over daily budget"
    )
    bm.get_fallback_model.return_value = "gpt-4o-mini"
    bm.record_spend = MagicMock()

    config_model_during_call: list[str] = []

    class ConfigCheckAgent(MockAgent):
        async def _invoke_llm(self, messages, tools, *, model_override=None):
            # self.config.model should NOT have been mutated
            config_model_during_call.append(self.config.model)
            return _make_response_with_usage()

    agent = ConfigCheckAgent(name="test-agent", model="gpt-4o")
    agent._budget_manager = bm

    await agent.chat("Hi")

    # config.model must remain "gpt-4o" even during the call
    assert config_model_during_call == ["gpt-4o"]
    assert agent.config.model == "gpt-4o"


@pytest.mark.asyncio
async def test_budget_stop_blocks_streaming():
    """Budget stop raises SagewaiBudgetExceededError for streaming calls."""
    bm = MagicMock()
    bm.check_budget.return_value = FakeBudgetCheckResult(
        allowed=False, action="stop", reason="Monthly budget exceeded"
    )

    agent = MockAgent(
        responses=[_make_response_with_usage()],
        name="test-agent",
        model="mock",
    )
    agent._budget_manager = bm

    with pytest.raises(SagewaiBudgetExceededError) as exc_info:
        chunks = []
        async for chunk in agent.chat_stream("Hi"):
            chunks.append(chunk)

    assert exc_info.value.agent_name == "test-agent"
    assert agent._call_count == 0  # LLM was never called


@pytest.mark.asyncio
async def test_budget_throttle_streaming_uses_override():
    """Streaming path passes model_override when budget throttles."""
    bm = MagicMock()
    bm.check_budget.return_value = FakeBudgetCheckResult(
        allowed=False, action="throttle", reason="Over daily budget"
    )
    bm.get_fallback_model.return_value = "gpt-4o-mini"
    bm.record_spend = MagicMock()

    captured_overrides: list[str | None] = []

    class StreamOverrideAgent(MockAgent):
        async def _invoke_llm(self, messages, tools, *, model_override=None):
            captured_overrides.append(model_override)
            return _make_response_with_usage()

    agent = StreamOverrideAgent(name="test-agent", model="gpt-4o")
    agent._budget_manager = bm

    chunks = []
    async for chunk in agent.chat_stream("Hi"):
        chunks.append(chunk)

    # The default _stream_llm calls _call_llm which passes model_override
    assert captured_overrides == ["gpt-4o-mini"]
    assert agent.config.model == "gpt-4o"
