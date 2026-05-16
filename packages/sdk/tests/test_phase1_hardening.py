# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for Phase 1 hardening changes across the sagewai SDK.

Covers:
  1a-1b  DurableRunner heartbeat + step timeout
  1c     ReActStrategy configurable limits
  1d     TokenBudgetGuard context (cost_usd_so_far)
  1f     Webhook verification (Slack, Stripe, Email)
  1g     Workflow agent streaming (Sequential, Parallel, Loop)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.durability import DurableRunner, StepTimeoutError
from sagewai.core.state import InMemoryStore, StepStatus
from sagewai.core.strategies import ReActStrategy
from sagewai.core.workflows import LoopAgent, ParallelAgent, SequentialAgent
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec
from sagewai.safety.guardrails import Guardrail, GuardrailResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockAgent(BaseAgent):
    """Agent with predetermined response for testing."""

    def __init__(
        self,
        name: str = "mock",
        response: str = "mock response",
        delay: float = 0.0,
        **kwargs: Any,
    ):
        super().__init__(name=name, **kwargs)
        self._response = response
        self._delay = delay
        self.call_count = 0

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        raise NotImplementedError

    async def chat(self, message: str) -> str:
        self.call_count += 1
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return self._response

    async def chat_stream(self, message: str) -> AsyncGenerator[str, None]:
        self.call_count += 1
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        # Yield response in chunks
        words = self._response.split()
        for word in words:
            yield word + " "


class SlowAgent(BaseAgent):
    """Agent that sleeps for a configurable duration."""

    def __init__(self, name: str = "slow", sleep_seconds: float = 10.0, **kwargs):
        super().__init__(name=name, **kwargs)
        self._sleep_seconds = sleep_seconds

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        raise NotImplementedError

    async def chat(self, message: str) -> str:
        await asyncio.sleep(self._sleep_seconds)
        return "done"


class CostCapturingGuard(Guardrail):
    """Guardrail that captures the context dict for inspection."""

    def __init__(self):
        self.captured_input_contexts: list[dict] = []
        self.captured_output_contexts: list[dict] = []

    async def check_input(self, message: str, context: dict[str, Any]) -> GuardrailResult:
        self.captured_input_contexts.append(dict(context))
        return GuardrailResult(passed=True)

    async def check_output(self, response: str, context: dict[str, Any]) -> GuardrailResult:
        self.captured_output_contexts.append(dict(context))
        return GuardrailResult(passed=True)


# ===================================================================
# 1a-1b: DurableRunner heartbeat + timeout
# ===================================================================


class TestDurableRunnerHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_emitted_during_step_execution(self):
        """Heartbeat is emitted during step execution."""
        store = InMemoryStore()
        heartbeat_calls: list[tuple[str, str]] = []

        original_heartbeat = store.heartbeat

        async def tracking_heartbeat(workflow_name: str, run_id: str) -> None:
            heartbeat_calls.append((workflow_name, run_id))
            await original_heartbeat(workflow_name, run_id)

        store.heartbeat = tracking_heartbeat  # type: ignore[assignment]

        agent = MockAgent(name="slow-ish", response="done", delay=0.2)
        runner = DurableRunner(
            store=store,
            heartbeat_interval=0.05,
        )

        result = await runner.run_sequential(
            agents=[agent],
            input_text="test",
            run_id="hb-run-1",
        )

        assert result == "done"
        # With 0.2s step and 0.05s interval, expect at least 1 heartbeat
        assert len(heartbeat_calls) >= 1
        assert all(wf.startswith("sequential:") for wf, _ in heartbeat_calls)
        assert all(rid == "hb-run-1" for _, rid in heartbeat_calls)


class TestDurableRunnerStepTimeout:
    @pytest.mark.asyncio
    async def test_step_timeout_raises_error(self):
        """Step timeout raises StepTimeoutError."""
        store = InMemoryStore()
        agent = SlowAgent(name="very-slow", sleep_seconds=10.0)
        runner = DurableRunner(
            store=store,
            step_timeout=0.1,
        )

        with pytest.raises(StepTimeoutError) as exc_info:
            await runner.run_sequential(
                agents=[agent],
                input_text="test",
                run_id="timeout-run-1",
            )

        assert exc_info.value.timeout == 0.1
        assert "timed out" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_step_timeout_error_attributes(self):
        """StepTimeoutError carries step_name and timeout."""
        err = StepTimeoutError("my_step", 5.0)
        assert err.step_name == "my_step"
        assert err.timeout == 5.0
        assert "my_step" in str(err)
        assert "5.0" in str(err)


class TestDurableRunnerHeartbeatCancellation:
    @pytest.mark.asyncio
    async def test_heartbeat_cancelled_on_completion(self):
        """Heartbeat task is cancelled when step completes."""
        store = InMemoryStore()
        heartbeat_after_done: list[str] = []
        completion_event = asyncio.Event()

        original_heartbeat = store.heartbeat

        async def tracking_heartbeat(wf: str, rid: str) -> None:
            if completion_event.is_set():
                heartbeat_after_done.append(rid)
            await original_heartbeat(wf, rid)

        store.heartbeat = tracking_heartbeat  # type: ignore[assignment]

        agent = MockAgent(name="fast", response="quick", delay=0.01)
        runner = DurableRunner(
            store=store,
            heartbeat_interval=0.01,
        )

        await runner.run_sequential(
            agents=[agent],
            input_text="test",
            run_id="cancel-run-1",
        )
        completion_event.set()

        # Wait and check no heartbeats fire after completion
        await asyncio.sleep(0.1)
        assert len(heartbeat_after_done) == 0

    @pytest.mark.asyncio
    async def test_heartbeat_cancelled_on_timeout(self):
        """Heartbeat task is cancelled even when step times out."""
        store = InMemoryStore()
        heartbeat_after_timeout: list[str] = []
        timeout_event = asyncio.Event()

        original_heartbeat = store.heartbeat

        async def tracking_heartbeat(wf: str, rid: str) -> None:
            if timeout_event.is_set():
                heartbeat_after_timeout.append(rid)
            await original_heartbeat(wf, rid)

        store.heartbeat = tracking_heartbeat  # type: ignore[assignment]

        agent = SlowAgent(name="timeout-agent", sleep_seconds=10.0)
        runner = DurableRunner(
            store=store,
            heartbeat_interval=0.01,
            step_timeout=0.05,
        )

        with pytest.raises(StepTimeoutError):
            await runner.run_sequential(
                agents=[agent],
                input_text="test",
                run_id="cancel-timeout-1",
            )
        timeout_event.set()

        await asyncio.sleep(0.1)
        assert len(heartbeat_after_timeout) == 0


# ===================================================================
# 1c: ReActStrategy configurable limits
# ===================================================================


class TestReActStrategyLimits:
    def test_default_limits(self):
        """ReActStrategy has sensible defaults."""
        s = ReActStrategy()
        assert s.max_tool_calls_per_name == 3
        assert s.max_error_streak == 2

    def test_custom_limits(self):
        """ReActStrategy accepts custom limits."""
        s = ReActStrategy(max_tool_calls_per_name=10, max_error_streak=5)
        assert s.max_tool_calls_per_name == 10
        assert s.max_error_streak == 5

    def test_limits_set_to_one(self):
        """Minimum limits of 1 are accepted."""
        s = ReActStrategy(max_tool_calls_per_name=1, max_error_streak=1)
        assert s.max_tool_calls_per_name == 1
        assert s.max_error_streak == 1

    def test_strategy_is_keyword_only(self):
        """Parameters are keyword-only."""
        with pytest.raises(TypeError):
            ReActStrategy(10, 5)  # type: ignore[misc]


# ===================================================================
# 1d: TokenBudgetGuard context
# ===================================================================


class LLMTestAgent(BaseAgent):
    """Agent that uses the real BaseAgent.chat() flow with a mock LLM."""

    def __init__(self, response_text: str = "ok", **kwargs):
        super().__init__(**kwargs)
        self._response_text = response_text

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        return ChatMessage.assistant(self._response_text)


class TestBudgetGuardContext:
    @pytest.mark.asyncio
    async def test_guardrail_receives_cost_in_context(self):
        """Guardrail receives accumulated cost in context dict."""
        guard = CostCapturingGuard()
        agent = LLMTestAgent(
            name="cost-test",
            response_text="hello",
            guardrails=[guard],
        )

        await agent.chat("test message")

        # Input guardrail should have been called with cost context
        assert len(guard.captured_input_contexts) == 1
        ctx = guard.captured_input_contexts[0]
        assert "cost_usd_so_far" in ctx
        # Before any LLM call, cost should be 0
        assert ctx["cost_usd_so_far"] == 0.0

    @pytest.mark.asyncio
    async def test_output_guardrail_receives_cost_context(self):
        """Output guardrail also receives the cost context."""
        guard = CostCapturingGuard()
        agent = LLMTestAgent(
            name="cost-output-test",
            response_text="result",
            guardrails=[guard],
        )

        await agent.chat("test")

        assert len(guard.captured_output_contexts) == 1
        ctx = guard.captured_output_contexts[0]
        assert "cost_usd_so_far" in ctx
        assert isinstance(ctx["cost_usd_so_far"], float)

    @pytest.mark.asyncio
    async def test_accumulated_cost_starts_at_zero(self):
        """New agent starts with zero accumulated cost."""
        agent = LLMTestAgent(name="fresh-agent")
        assert agent._accumulated_cost == 0.0


# ===================================================================
# 1f: Webhook verification
# ===================================================================


class TestSlackWebhookVerification:
    @pytest.mark.asyncio
    async def test_valid_signature(self):
        """Slack webhook HMAC-SHA256 verification accepts valid signature."""
        from sagewai.connectors.builtins.slack.connector import SlackConnector

        connector = SlackConnector()

        secret = "test_signing_secret"
        timestamp = "1234567890"
        body = b'{"event":"message"}'

        base_string = f"v0:{timestamp}:{body.decode()}"
        expected_sig = (
            "v0="
            + hmac.new(
                secret.encode(),
                base_string.encode(),
                hashlib.sha256,
            ).hexdigest()
        )

        result = await connector.verify_webhook(
            request_body=body,
            headers={
                "x-slack-request-timestamp": timestamp,
                "x-slack-signature": expected_sig,
            },
            credentials={"signing_secret": secret},
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid_signature(self):
        """Slack webhook verification rejects invalid signature."""
        from sagewai.connectors.builtins.slack.connector import SlackConnector

        connector = SlackConnector()

        result = await connector.verify_webhook(
            request_body=b'{"event":"message"}',
            headers={
                "x-slack-request-timestamp": "1234567890",
                "x-slack-signature": "v0=invalid_signature_here",
            },
            credentials={"signing_secret": "test_secret"},
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_headers_returns_false(self):
        """Missing headers cause verification to return False, not crash."""
        from sagewai.connectors.builtins.slack.connector import SlackConnector

        connector = SlackConnector()

        result = await connector.verify_webhook(
            request_body=b"{}",
            headers={},
            credentials={"signing_secret": "test"},
        )
        assert result is False


class TestStripeWebhookVerification:
    @pytest.mark.asyncio
    async def test_valid_signature(self):
        """Stripe webhook verification accepts valid signature."""
        from sagewai.connectors.builtins.payments.connector import PaymentsConnector

        connector = PaymentsConnector()

        secret = "whsec_test_secret"
        timestamp = "1234567890"
        body = b'{"type":"payment_intent.succeeded"}'

        base_string = f"{timestamp}.{body.decode()}"
        sig = hmac.new(
            secret.encode(),
            base_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        result = await connector.verify_webhook(
            request_body=body,
            headers={
                "stripe-signature": f"t={timestamp},v1={sig}",
            },
            credentials={"webhook_secret": secret},
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid_signature(self):
        """Stripe webhook verification rejects invalid signature."""
        from sagewai.connectors.builtins.payments.connector import PaymentsConnector

        connector = PaymentsConnector()

        result = await connector.verify_webhook(
            request_body=b'{"type":"charge.failed"}',
            headers={
                "stripe-signature": "t=123,v1=bad_sig",
            },
            credentials={"webhook_secret": "whsec_real_secret"},
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_header_returns_false(self):
        """Missing stripe-signature header returns False."""
        from sagewai.connectors.builtins.payments.connector import PaymentsConnector

        connector = PaymentsConnector()

        result = await connector.verify_webhook(
            request_body=b"{}",
            headers={},
            credentials={"webhook_secret": "whsec_test"},
        )
        assert result is False


class TestEmailWebhookVerification:
    @pytest.mark.asyncio
    async def test_valid_signature(self):
        """Email webhook verification accepts valid signature."""
        from sagewai.connectors.builtins.email.connector import EmailConnector

        connector = EmailConnector()

        secret = "email_webhook_secret"
        body = b'[{"email":"test@example.com","event":"delivered"}]'

        sig = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()

        result = await connector.verify_webhook(
            request_body=body,
            headers={"x-webhook-signature": sig},
            credentials={"webhook_secret": secret},
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid_signature(self):
        """Email webhook verification rejects invalid signature."""
        from sagewai.connectors.builtins.email.connector import EmailConnector

        connector = EmailConnector()

        result = await connector.verify_webhook(
            request_body=b'[{"event":"bounce"}]',
            headers={"x-webhook-signature": "invalid_hex"},
            credentials={"webhook_secret": "real_secret"},
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_secret_returns_false(self):
        """Missing webhook_secret credential returns False."""
        from sagewai.connectors.builtins.email.connector import EmailConnector

        connector = EmailConnector()

        result = await connector.verify_webhook(
            request_body=b"{}",
            headers={"x-webhook-signature": "something"},
            credentials={},
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_signature_header_returns_false(self):
        """Missing x-webhook-signature header returns False."""
        from sagewai.connectors.builtins.email.connector import EmailConnector

        connector = EmailConnector()

        result = await connector.verify_webhook(
            request_body=b"{}",
            headers={},
            credentials={"webhook_secret": "secret"},
        )
        assert result is False


# ===================================================================
# 1g: Workflow agent streaming
# ===================================================================


class TestSequentialAgentStreaming:
    @pytest.mark.asyncio
    async def test_chat_stream_yields_chunks(self):
        """SequentialAgent.chat_stream yields chunks from sub-agents."""
        a = MockAgent(name="a", response="hello world")
        b = MockAgent(name="b", response="final result")
        seq = SequentialAgent(name="seq", agents=[a, b])

        chunks: list[str] = []
        async for chunk in seq.chat_stream("start"):
            chunks.append(chunk)

        assert len(chunks) > 0
        full = "".join(chunks)
        # The last agent's streaming output should be present
        assert "final" in full or "result" in full

    @pytest.mark.asyncio
    async def test_chat_stream_pipes_through_agents(self):
        """Sequential streaming pipes output through all agents."""
        a = MockAgent(name="a", response="from-a")
        b = MockAgent(name="b", response="from-b")
        seq = SequentialAgent(name="seq", agents=[a, b])

        chunks: list[str] = []
        async for chunk in seq.chat_stream("input"):
            chunks.append(chunk)

        # Both agents should have been called
        assert a.call_count == 1
        assert b.call_count == 1


class TestParallelAgentStreaming:
    @pytest.mark.asyncio
    async def test_chat_stream_yields_chunks(self):
        """ParallelAgent.chat_stream yields merged chunks."""
        a = MockAgent(name="a", response="result-a")
        b = MockAgent(name="b", response="result-b")
        par = ParallelAgent(name="par", agents=[a, b])

        chunks: list[str] = []
        async for chunk in par.chat_stream("input"):
            chunks.append(chunk)

        assert len(chunks) > 0
        full = "".join(chunks)
        assert "result-a" in full
        assert "result-b" in full

    @pytest.mark.asyncio
    async def test_parallel_streaming_calls_all_agents(self):
        """All parallel agents are invoked during streaming."""
        agents = [MockAgent(name=f"p{i}", response=f"r{i}") for i in range(3)]
        par = ParallelAgent(name="par", agents=agents)

        async for _ in par.chat_stream("go"):
            pass

        for agent in agents:
            assert agent.call_count == 1


class TestLoopAgentStreaming:
    @pytest.mark.asyncio
    async def test_chat_stream_yields_chunks(self):
        """LoopAgent.chat_stream yields chunks from each iteration."""
        agent = MockAgent(name="looper", response="iteration output")
        loop = LoopAgent(
            name="loop",
            agent=agent,
            max_iterations=2,
        )

        chunks: list[str] = []
        async for chunk in loop.chat_stream("start"):
            chunks.append(chunk)

        assert len(chunks) > 0
        assert agent.call_count == 2

    @pytest.mark.asyncio
    async def test_loop_stream_stops_on_condition(self):
        """LoopAgent streaming respects should_stop condition."""
        agent = MockAgent(name="stopper", response="DONE")

        def stop_after_one(result: str, iteration: int) -> bool:
            return iteration >= 0  # Stop after first iteration

        loop = LoopAgent(
            name="loop",
            agent=agent,
            should_stop=stop_after_one,
            max_iterations=10,
        )

        chunks: list[str] = []
        async for chunk in loop.chat_stream("go"):
            chunks.append(chunk)

        # Should have run only once due to stop condition
        assert agent.call_count == 1

    @pytest.mark.asyncio
    async def test_loop_stream_feeds_output_back(self):
        """LoopAgent streaming feeds each iteration's output as next input."""
        received_inputs: list[str] = []

        class TrackingAgent(BaseAgent):
            async def _invoke_llm(self, messages, tools, *, model_override=None):
                raise NotImplementedError

            async def chat(self, message: str) -> str:
                received_inputs.append(message)
                return message + "+"

            async def chat_stream(self, message: str) -> AsyncGenerator[str, None]:
                received_inputs.append(message)
                result = message + "+"
                yield result

        agent = TrackingAgent(name="tracker", model="mock")
        loop = LoopAgent(name="loop", agent=agent, max_iterations=3)

        chunks: list[str] = []
        async for chunk in loop.chat_stream("x"):
            chunks.append(chunk)

        assert received_inputs[0] == "x"
        assert received_inputs[1] == "x+"
        assert received_inputs[2] == "x++"


# ===================================================================
# DurableRunner — parallel and loop heartbeats
# ===================================================================


class TestDurableRunnerParallelHeartbeat:
    @pytest.mark.asyncio
    async def test_parallel_heartbeat(self):
        """Heartbeat fires during parallel execution."""
        store = InMemoryStore()
        heartbeat_calls: list[str] = []

        original_heartbeat = store.heartbeat

        async def tracking_heartbeat(wf: str, rid: str) -> None:
            heartbeat_calls.append(rid)
            await original_heartbeat(wf, rid)

        store.heartbeat = tracking_heartbeat  # type: ignore[assignment]

        agents = [
            MockAgent(name=f"p{i}", response=f"r{i}", delay=0.15)
            for i in range(2)
        ]
        runner = DurableRunner(store=store, heartbeat_interval=0.05)

        result = await runner.run_parallel(
            agents=agents,
            input_text="test",
            run_id="par-hb-1",
        )

        assert "r0" in result
        assert "r1" in result
        assert len(heartbeat_calls) >= 1


class TestDurableRunnerLoopHeartbeat:
    @pytest.mark.asyncio
    async def test_loop_heartbeat(self):
        """Heartbeat fires during loop execution."""
        store = InMemoryStore()
        heartbeat_calls: list[str] = []

        original_heartbeat = store.heartbeat

        async def tracking_heartbeat(wf: str, rid: str) -> None:
            heartbeat_calls.append(rid)
            await original_heartbeat(wf, rid)

        store.heartbeat = tracking_heartbeat  # type: ignore[assignment]

        agent = MockAgent(name="looper", response="done", delay=0.15)
        runner = DurableRunner(store=store, heartbeat_interval=0.05)

        await runner.run_loop(
            agent=agent,
            input_text="test",
            max_iterations=2,
            run_id="loop-hb-1",
        )

        assert len(heartbeat_calls) >= 1


class TestDurableRunnerStepTimeoutLoop:
    @pytest.mark.asyncio
    async def test_loop_step_timeout(self):
        """Step timeout applies to loop iterations too."""
        store = InMemoryStore()
        agent = SlowAgent(name="slow-loop", sleep_seconds=10.0)
        runner = DurableRunner(store=store, step_timeout=0.1)

        with pytest.raises(StepTimeoutError):
            await runner.run_loop(
                agent=agent,
                input_text="test",
                max_iterations=3,
                run_id="loop-timeout-1",
            )

        # Verify the run was marked as failed
        run = await store.load_run("loop:slow-loop", "loop-timeout-1")
        assert run is not None
        assert run.status == StepStatus.FAILED
