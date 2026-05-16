# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for OpenTelemetry tracing integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sagewai.observability.tracing import (
    TracingConfig,
    _NoOpSpan,
    _NoOpTracer,
    agent_span,
    is_tracing_enabled,
    llm_span,
    memory_span,
    setup_tracing,
    shutdown_tracing,
    tool_span,
    tracing_event_hook,
)

# ---------------------------------------------------------------------------
# TracingConfig
# ---------------------------------------------------------------------------


class TestTracingConfig:
    def test_defaults(self):
        config = TracingConfig()
        assert config.service_name == "sagewai"
        assert config.exporter == "console"
        assert config.endpoint is None
        assert config.sample_rate == 1.0
        assert config.attributes == {}

    def test_custom_config(self):
        config = TracingConfig(
            service_name="nexus",
            exporter="otlp",
            endpoint="localhost:4317",
            sample_rate=0.5,
            attributes={"env": "prod"},
        )
        assert config.service_name == "nexus"
        assert config.exporter == "otlp"
        assert config.attributes["env"] == "prod"


# ---------------------------------------------------------------------------
# No-op fallbacks
# ---------------------------------------------------------------------------


class TestNoOpSpan:
    def test_set_attribute(self):
        span = _NoOpSpan()
        span.set_attribute("key", "value")  # Should not raise

    def test_set_status(self):
        span = _NoOpSpan()
        span.set_status("OK")  # Should not raise

    def test_record_exception(self):
        span = _NoOpSpan()
        span.record_exception(RuntimeError("test"))

    def test_end(self):
        span = _NoOpSpan()
        span.end()

    def test_context_manager(self):
        span = _NoOpSpan()
        with span:
            pass  # Should not raise


class TestNoOpTracer:
    def test_start_as_current_span(self):
        tracer = _NoOpTracer()
        span = tracer.start_as_current_span("test")
        assert isinstance(span, _NoOpSpan)

    def test_start_span(self):
        tracer = _NoOpTracer()
        span = tracer.start_span("test")
        assert isinstance(span, _NoOpSpan)


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


class TestSetupTracing:
    def test_none_exporter_disables(self):
        result = setup_tracing(TracingConfig(exporter="none"))
        assert result is False
        assert not is_tracing_enabled()

    def test_otel_not_installed(self):
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": None,
                "opentelemetry.trace": None,
                "opentelemetry.sdk": None,
                "opentelemetry.sdk.trace": None,
                "opentelemetry.sdk.resources": None,
            },
        ):
            result = setup_tracing(TracingConfig())
            assert result is False

    def test_setup_with_console_exporter(self):
        try:
            import opentelemetry  # noqa: F401
        except ImportError:
            pytest.skip("opentelemetry-sdk not installed")

        result = setup_tracing(TracingConfig(exporter="console"))
        assert result is True
        assert is_tracing_enabled()
        shutdown_tracing()

    def test_shutdown(self):
        try:
            import opentelemetry  # noqa: F401
        except ImportError:
            pytest.skip("opentelemetry not installed")

        setup_tracing(TracingConfig(exporter="console"))
        assert is_tracing_enabled()
        shutdown_tracing()
        assert not is_tracing_enabled()


# ---------------------------------------------------------------------------
# Span helpers (with OTel)
# ---------------------------------------------------------------------------


class TestSpanHelpers:
    @pytest.fixture(autouse=True)
    def _setup_tracing(self):
        try:
            import opentelemetry  # noqa: F401
        except ImportError:
            pytest.skip("opentelemetry not installed")
        setup_tracing(TracingConfig(exporter="console"))
        yield
        shutdown_tracing()

    def test_agent_span(self):
        with agent_span("test-agent", "chat", model="gpt-4o") as span:
            assert span is not None

    def test_llm_span(self):
        with llm_span("gpt-4o", input_tokens="100") as span:
            assert span is not None

    def test_tool_span(self):
        with tool_span("search", query="test") as span:
            assert span is not None

    def test_memory_span(self):
        with memory_span("retrieve", provider="milvus", top_k="5") as span:
            assert span is not None

    def test_nested_spans(self):
        with agent_span("pipeline", "run") as outer:
            with llm_span("gpt-4o") as inner:
                assert outer is not None
                assert inner is not None


# ---------------------------------------------------------------------------
# Span helpers (without OTel — no-op mode)
# ---------------------------------------------------------------------------


class TestSpanHelpersNoOp:
    @pytest.fixture(autouse=True)
    def _disable_tracing(self):
        shutdown_tracing()
        # Force no-op by patching get_tracer
        with patch("sagewai.observability.tracing.get_tracer", return_value=_NoOpTracer()):
            yield

    def test_agent_span_noop(self):
        with agent_span("agent", "chat") as span:
            assert isinstance(span, _NoOpSpan)

    def test_llm_span_noop(self):
        with llm_span("model") as span:
            assert isinstance(span, _NoOpSpan)

    def test_tool_span_noop(self):
        with tool_span("tool") as span:
            assert isinstance(span, _NoOpSpan)

    def test_memory_span_noop(self):
        with memory_span("retrieve") as span:
            assert isinstance(span, _NoOpSpan)


# ---------------------------------------------------------------------------
# Event hook
# ---------------------------------------------------------------------------


class TestTracingEventHook:
    def test_hook_when_not_initialized(self):
        shutdown_tracing()
        from sagewai.core.events import AgentEvent

        # Should not raise — exits early when not initialized
        tracing_event_hook(AgentEvent.LLM_CALL_FINISHED, {"agent": "test"})

    def test_hook_with_tracing(self):
        try:
            import opentelemetry  # noqa: F401
        except ImportError:
            pytest.skip("opentelemetry not installed")

        from sagewai.core.events import AgentEvent

        setup_tracing(TracingConfig(exporter="console"))
        tracing_event_hook(
            AgentEvent.LLM_CALL_FINISHED,
            {"agent": "test-agent", "model": "gpt-4o", "input_tokens": "150"},
        )
        shutdown_tracing()

    def test_hook_with_noop_tracer(self):
        shutdown_tracing()
        from sagewai.core.events import AgentEvent

        with patch("sagewai.observability.tracing.get_tracer", return_value=_NoOpTracer()):
            with patch("sagewai.observability.tracing._initialized", True):
                tracing_event_hook(AgentEvent.STEP_STARTED, {"agent": "agent"})

    def test_hook_with_no_data(self):
        """Hook accepts data=None (the default)."""
        shutdown_tracing()
        from sagewai.core.events import AgentEvent

        tracing_event_hook(AgentEvent.STEP_STARTED)

    def test_hook_with_string_event(self):
        """Hook works with plain string event values too."""
        shutdown_tracing()
        tracing_event_hook("llm_call_finished", {"agent": "test"})
