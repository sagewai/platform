"""Tests for token cost tracking."""

from __future__ import annotations

import pytest

from sagewai.core.events import AgentEvent
from sagewai.observability.costs import (
    CostTracker,
    LLMCallRecord,
    RunSummary,
    calculate_cost,
    estimate_tokens_from_text,
    get_model_pricing,
)

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


class TestTokenEstimation:
    def test_basic(self):
        assert estimate_tokens_from_text("hello world") >= 1

    def test_empty(self):
        assert estimate_tokens_from_text("") >= 1

    def test_long_text(self):
        short = estimate_tokens_from_text("hi")
        long = estimate_tokens_from_text("hello " * 100)
        assert long > short


# ---------------------------------------------------------------------------
# Model pricing
# ---------------------------------------------------------------------------


class TestModelPricing:
    def test_known_model(self):
        inp, out = get_model_pricing("gpt-4o")
        assert inp == 2.50
        assert out == 10.00

    def test_claude_model(self):
        inp, out = get_model_pricing("claude-opus-4-6")
        assert inp == 15.00
        assert out == 75.00

    def test_prefix_match(self):
        inp, out = get_model_pricing("gpt-4o-2024-08-06")
        assert inp == 2.50

    def test_unknown_model_fallback(self):
        inp, out = get_model_pricing("totally-unknown-model")
        assert inp > 0
        assert out > 0

    def test_gemini_model(self):
        inp, out = get_model_pricing("gemini-2.5-flash")
        assert inp == 0.10


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


class TestCalculateCost:
    def test_basic_cost(self):
        # gpt-4o: $2.50/1M input, $10/1M output
        cost = calculate_cost(1000, 500, "gpt-4o")
        expected = (1000 * 2.50 + 500 * 10.00) / 1_000_000
        assert abs(cost - expected) < 0.0001

    def test_zero_tokens(self):
        assert calculate_cost(0, 0, "gpt-4o") == 0.0

    def test_large_usage(self):
        cost = calculate_cost(1_000_000, 1_000_000, "gpt-4o")
        assert cost == 2.50 + 10.00

    def test_unknown_model_still_works(self):
        cost = calculate_cost(100, 100, "unknown")
        assert cost > 0


# ---------------------------------------------------------------------------
# LLMCallRecord
# ---------------------------------------------------------------------------


class TestLLMCallRecord:
    def test_creation(self):
        record = LLMCallRecord(
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            duration_ms=150.0,
        )
        assert record.model == "gpt-4o"
        assert record.input_tokens == 100
        assert record.timestamp > 0


# ---------------------------------------------------------------------------
# RunSummary
# ---------------------------------------------------------------------------


class TestRunSummary:
    def test_empty_summary(self):
        summary = RunSummary(agent_name="test")
        assert summary.total_tokens == 0
        assert summary.call_count == 0

    def test_add_call(self):
        summary = RunSummary(agent_name="test")
        record = LLMCallRecord(model="gpt-4o", input_tokens=100, output_tokens=50, cost_usd=0.001)
        summary.add_call(record)
        assert summary.total_input_tokens == 100
        assert summary.total_output_tokens == 50
        assert summary.total_tokens == 150
        assert summary.call_count == 1

    def test_multiple_calls(self):
        summary = RunSummary(agent_name="test")
        for i in range(3):
            summary.add_call(
                LLMCallRecord(model="gpt-4o", input_tokens=100, output_tokens=50, cost_usd=0.001)
            )
        assert summary.call_count == 3
        assert summary.total_input_tokens == 300
        assert summary.total_cost_usd == pytest.approx(0.003)

    def test_to_dict(self):
        summary = RunSummary(agent_name="test-agent")
        summary.add_call(
            LLMCallRecord(
                model="gpt-4o", input_tokens=100, output_tokens=50, cost_usd=0.001, duration_ms=100
            )
        )
        d = summary.to_dict()
        assert d["agent_name"] == "test-agent"
        assert d["total_tokens"] == 150
        assert d["call_count"] == 1
        assert len(d["calls"]) == 1
        assert d["calls"][0]["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


class TestCostTracker:
    def test_start_and_end_run(self):
        tracker = CostTracker()
        run = tracker.start_run("agent-1")
        assert tracker.current_run is run
        ended = tracker.end_run()
        assert ended is run
        assert tracker.current_run is None
        assert len(tracker.runs) == 1

    def test_record_call(self):
        tracker = CostTracker()
        tracker.start_run("agent-1")
        record = tracker.record_call(
            model="gpt-4o", input_tokens=1000, output_tokens=500, duration_ms=200
        )
        assert record.cost_usd > 0
        assert tracker.current_run.call_count == 1

    def test_record_without_run(self):
        tracker = CostTracker()
        record = tracker.record_call(model="gpt-4o", input_tokens=100, output_tokens=50)
        assert record.cost_usd > 0

    def test_total_cost(self):
        tracker = CostTracker()
        tracker.start_run("agent-1")
        tracker.record_call(model="gpt-4o", input_tokens=1000, output_tokens=500)
        tracker.end_run()

        tracker.start_run("agent-2")
        tracker.record_call(model="gpt-4o", input_tokens=2000, output_tokens=1000)
        tracker.end_run()

        assert tracker.total_cost > 0
        assert len(tracker.runs) == 2

    def test_total_tokens(self):
        tracker = CostTracker()
        tracker.start_run("agent-1")
        tracker.record_call(model="gpt-4o", input_tokens=100, output_tokens=50)
        tracker.end_run()
        assert tracker.total_tokens == 150

    def test_summary(self):
        tracker = CostTracker()
        tracker.start_run("agent-1")
        tracker.record_call(model="gpt-4o", input_tokens=100, output_tokens=50)
        tracker.end_run()
        s = tracker.summary()
        assert s["total_runs"] == 1
        assert s["total_tokens"] == 150
        assert len(s["runs"]) == 1

    def test_reset(self):
        tracker = CostTracker()
        tracker.start_run("agent-1")
        tracker.record_call(model="gpt-4o", input_tokens=100, output_tokens=50)
        tracker.end_run()
        tracker.reset()
        assert len(tracker.runs) == 0
        assert tracker.total_cost == 0

    def test_end_run_without_start(self):
        tracker = CostTracker()
        assert tracker.end_run() is None

    @pytest.mark.asyncio
    async def test_event_hook_run_started(self):
        tracker = CostTracker()
        await tracker.event_hook(AgentEvent.RUN_STARTED, {"agent": "test-agent"})
        assert tracker.current_run is not None
        assert tracker.current_run.agent_name == "test-agent"

    @pytest.mark.asyncio
    async def test_event_hook_run_finished(self):
        tracker = CostTracker()
        await tracker.event_hook(AgentEvent.RUN_STARTED, {"agent": "test-agent"})
        tracker.record_call(model="gpt-4o", input_tokens=100, output_tokens=50)
        await tracker.event_hook(AgentEvent.RUN_FINISHED, {"agent": "test-agent"})
        assert tracker.current_run is None
        assert len(tracker.runs) == 1

    @pytest.mark.asyncio
    async def test_event_hook_llm_call_finished(self):
        tracker = CostTracker()
        await tracker.event_hook(AgentEvent.RUN_STARTED, {"agent": "test-agent"})
        await tracker.event_hook(
            AgentEvent.LLM_CALL_FINISHED,
            {
                "agent": "test-agent",
                "model": "gpt-4o",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.00075,
                "duration_ms": 250.0,
            },
        )
        assert tracker.current_run is not None
        assert tracker.current_run.call_count == 1
        assert tracker.current_run.total_input_tokens == 100
        assert tracker.current_run.total_output_tokens == 50
        assert tracker.current_run.total_duration_ms == 250.0

    @pytest.mark.asyncio
    async def test_event_hook_multiple_llm_calls(self):
        tracker = CostTracker()
        await tracker.event_hook(AgentEvent.RUN_STARTED, {"agent": "test-agent"})
        for _ in range(3):
            await tracker.event_hook(
                AgentEvent.LLM_CALL_FINISHED,
                {
                    "model": "gpt-4o",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "duration_ms": 100.0,
                },
            )
        await tracker.event_hook(AgentEvent.RUN_FINISHED, {"agent": "test-agent"})
        assert len(tracker.runs) == 1
        assert tracker.runs[0].call_count == 3
        assert tracker.runs[0].total_input_tokens == 300

    @pytest.mark.asyncio
    async def test_event_hook_ignores_other_events(self):
        tracker = CostTracker()
        await tracker.event_hook(AgentEvent.STEP_STARTED, {"step": "test"})
        assert tracker.current_run is None

    def test_runs_are_copies(self):
        tracker = CostTracker()
        tracker.start_run("a")
        tracker.end_run()
        runs = tracker.runs
        runs.clear()
        assert len(tracker.runs) == 1  # Original not affected
