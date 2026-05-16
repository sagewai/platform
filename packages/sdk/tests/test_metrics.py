# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for Prometheus metrics counters."""

from __future__ import annotations

import pytest

from sagewai.core.events import AgentEvent
from sagewai.observability.metrics import (
    _run_start_times,
    agent_run_duration_seconds,
    agent_runs_total,
    is_prometheus_available,
    llm_cost_usd_total,
    llm_tokens_total,
    metrics_event_hook,
    reset_metrics,
)

# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class TestPrometheusAvailability:
    def test_prometheus_is_available(self):
        """prometheus_client is installed in the test environment."""
        assert is_prometheus_available() is True

    def test_metric_objects_are_not_none(self):
        assert agent_runs_total is not None
        assert agent_run_duration_seconds is not None
        assert llm_cost_usd_total is not None
        assert llm_tokens_total is not None


# ---------------------------------------------------------------------------
# reset_metrics
# ---------------------------------------------------------------------------


class TestResetMetrics:
    def test_clears_run_start_times(self):
        _run_start_times["test-agent"] = 12345.0
        reset_metrics()
        assert len(_run_start_times) == 0


# ---------------------------------------------------------------------------
# metrics_event_hook — run lifecycle
# ---------------------------------------------------------------------------


class TestRunLifecycleMetrics:
    @pytest.fixture(autouse=True)
    def _clean(self):
        reset_metrics()
        yield
        reset_metrics()

    @pytest.mark.asyncio
    async def test_run_started_records_start_time(self):
        await metrics_event_hook(AgentEvent.RUN_STARTED, {"agent": "scout"})
        assert "scout" in _run_start_times
        assert _run_start_times["scout"] > 0

    @pytest.mark.asyncio
    async def test_run_finished_increments_counter(self):
        before = agent_runs_total.labels(agent_name="scout", status="success")._value.get()
        await metrics_event_hook(AgentEvent.RUN_STARTED, {"agent": "scout"})
        await metrics_event_hook(AgentEvent.RUN_FINISHED, {"agent": "scout"})
        after = agent_runs_total.labels(agent_name="scout", status="success")._value.get()
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_run_finished_observes_duration(self):
        before_count = agent_run_duration_seconds.labels(agent_name="scout")._sum.get()
        await metrics_event_hook(AgentEvent.RUN_STARTED, {"agent": "scout"})
        await metrics_event_hook(AgentEvent.RUN_FINISHED, {"agent": "scout"})
        after_count = agent_run_duration_seconds.labels(agent_name="scout")._sum.get()
        assert after_count > before_count

    @pytest.mark.asyncio
    async def test_run_error_increments_error_counter(self):
        before = agent_runs_total.labels(agent_name="scout", status="error")._value.get()
        await metrics_event_hook(AgentEvent.RUN_STARTED, {"agent": "scout"})
        await metrics_event_hook(AgentEvent.RUN_ERROR, {"agent": "scout", "error": "boom"})
        after = agent_runs_total.labels(agent_name="scout", status="error")._value.get()
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_run_finished_without_start_skips_duration(self):
        """Duration not recorded if run_started was never fired."""
        before = agent_run_duration_seconds.labels(agent_name="ghost")._sum.get()
        await metrics_event_hook(AgentEvent.RUN_FINISHED, {"agent": "ghost"})
        after = agent_run_duration_seconds.labels(agent_name="ghost")._sum.get()
        assert after == before

    @pytest.mark.asyncio
    async def test_run_start_time_cleaned_after_finish(self):
        await metrics_event_hook(AgentEvent.RUN_STARTED, {"agent": "scout"})
        assert "scout" in _run_start_times
        await metrics_event_hook(AgentEvent.RUN_FINISHED, {"agent": "scout"})
        assert "scout" not in _run_start_times


# ---------------------------------------------------------------------------
# metrics_event_hook — LLM call
# ---------------------------------------------------------------------------


class TestLLMCallMetrics:
    @pytest.fixture(autouse=True)
    def _clean(self):
        reset_metrics()
        yield
        reset_metrics()

    @pytest.mark.asyncio
    async def test_llm_call_increments_cost(self):
        before = llm_cost_usd_total.labels(model="gpt-4o", agent_name="scout")._value.get()
        await metrics_event_hook(
            AgentEvent.LLM_CALL_FINISHED,
            {
                "agent": "scout",
                "model": "gpt-4o",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.00075,
                "duration_ms": 250.0,
            },
        )
        after = llm_cost_usd_total.labels(model="gpt-4o", agent_name="scout")._value.get()
        assert after == pytest.approx(before + 0.00075)

    @pytest.mark.asyncio
    async def test_llm_call_increments_input_tokens(self):
        before = llm_tokens_total.labels(
            model="gpt-4o", agent_name="scout", token_type="input"
        )._value.get()
        await metrics_event_hook(
            AgentEvent.LLM_CALL_FINISHED,
            {
                "agent": "scout",
                "model": "gpt-4o",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.001,
            },
        )
        after = llm_tokens_total.labels(
            model="gpt-4o", agent_name="scout", token_type="input"
        )._value.get()
        assert after == before + 100

    @pytest.mark.asyncio
    async def test_llm_call_increments_output_tokens(self):
        before = llm_tokens_total.labels(
            model="gpt-4o", agent_name="scout", token_type="output"
        )._value.get()
        await metrics_event_hook(
            AgentEvent.LLM_CALL_FINISHED,
            {
                "agent": "scout",
                "model": "gpt-4o",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.001,
            },
        )
        after = llm_tokens_total.labels(
            model="gpt-4o", agent_name="scout", token_type="output"
        )._value.get()
        assert after == before + 50

    @pytest.mark.asyncio
    async def test_llm_call_zero_cost_skips_cost_counter(self):
        before = llm_cost_usd_total.labels(
            model="gemini-2.5-flash", agent_name="test"
        )._value.get()
        await metrics_event_hook(
            AgentEvent.LLM_CALL_FINISHED,
            {
                "agent": "test",
                "model": "gemini-2.5-flash",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
            },
        )
        after = llm_cost_usd_total.labels(
            model="gemini-2.5-flash", agent_name="test"
        )._value.get()
        assert after == before

    @pytest.mark.asyncio
    async def test_llm_call_zero_tokens_skips_token_counters(self):
        before_in = llm_tokens_total.labels(
            model="gemini-2.5-flash", agent_name="test", token_type="input"
        )._value.get()
        before_out = llm_tokens_total.labels(
            model="gemini-2.5-flash", agent_name="test", token_type="output"
        )._value.get()
        await metrics_event_hook(
            AgentEvent.LLM_CALL_FINISHED,
            {
                "agent": "test",
                "model": "gemini-2.5-flash",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.001,
            },
        )
        after_in = llm_tokens_total.labels(
            model="gemini-2.5-flash", agent_name="test", token_type="input"
        )._value.get()
        after_out = llm_tokens_total.labels(
            model="gemini-2.5-flash", agent_name="test", token_type="output"
        )._value.get()
        assert after_in == before_in
        assert after_out == before_out

    @pytest.mark.asyncio
    async def test_multiple_llm_calls_accumulate(self):
        before = llm_tokens_total.labels(
            model="gpt-4o", agent_name="multi", token_type="input"
        )._value.get()
        for _ in range(3):
            await metrics_event_hook(
                AgentEvent.LLM_CALL_FINISHED,
                {
                    "agent": "multi",
                    "model": "gpt-4o",
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "cost_usd": 0.002,
                },
            )
        after = llm_tokens_total.labels(
            model="gpt-4o", agent_name="multi", token_type="input"
        )._value.get()
        assert after == before + 600


# ---------------------------------------------------------------------------
# metrics_event_hook — edge cases
# ---------------------------------------------------------------------------


class TestMetricsEdgeCases:
    @pytest.fixture(autouse=True)
    def _clean(self):
        reset_metrics()
        yield
        reset_metrics()

    @pytest.mark.asyncio
    async def test_ignores_unrelated_events(self):
        """Events like STEP_STARTED should not raise or change counters."""
        await metrics_event_hook(AgentEvent.STEP_STARTED, {"step": "iteration_1"})

    @pytest.mark.asyncio
    async def test_none_data_handled_gracefully(self):
        await metrics_event_hook(AgentEvent.RUN_STARTED, None)

    @pytest.mark.asyncio
    async def test_missing_agent_defaults_to_unknown(self):
        before = agent_runs_total.labels(agent_name="unknown", status="success")._value.get()
        await metrics_event_hook(AgentEvent.RUN_STARTED, {})
        await metrics_event_hook(AgentEvent.RUN_FINISHED, {})
        after = agent_runs_total.labels(agent_name="unknown", status="success")._value.get()
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_missing_model_defaults_to_unknown(self):
        before = llm_cost_usd_total.labels(
            model="unknown", agent_name="unknown"
        )._value.get()
        await metrics_event_hook(
            AgentEvent.LLM_CALL_FINISHED,
            {"cost_usd": 0.01, "input_tokens": 10, "output_tokens": 5},
        )
        after = llm_cost_usd_total.labels(
            model="unknown", agent_name="unknown"
        )._value.get()
        assert after == pytest.approx(before + 0.01)
