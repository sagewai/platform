# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OpenTelemetry tracing for agent runs.

Auto-instruments agent runs with spans for LLM calls, tool executions,
and memory queries. Zero overhead when OTel is not configured.

Usage::

    from sagewai.observability.tracing import TracingConfig, setup_tracing, trace_agent

    setup_tracing(TracingConfig(service_name="nexus", exporter="console"))

    @trace_agent
    async def run_pipeline(agent, query):
        return await agent.chat(query)

    # Or use the event hook for automatic instrumentation:
    from sagewai.observability.tracing import tracing_event_hook
    agent = UniversalAgent(name="my-agent", on_event=tracing_event_hook)

Requires ``opentelemetry-api`` and ``opentelemetry-sdk`` (optional)::

    uv add opentelemetry-api opentelemetry-sdk
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Lazy-loaded OTel references
_tracer: Any = None
_initialized = False


@dataclass
class TracingConfig:
    """Configuration for OpenTelemetry tracing.

    Parameters
    ----------
    service_name:
        Name of the service (appears in traces).
    exporter:
        Exporter type: "console", "jaeger", "otlp", or "none".
    endpoint:
        Exporter endpoint URL (for jaeger/otlp).
    sample_rate:
        Sampling rate between 0.0 and 1.0 (default: 1.0 = all).
    attributes:
        Additional resource attributes.
    """

    service_name: str = "sagewai"
    exporter: str = "console"
    endpoint: str | None = None
    sample_rate: float = 1.0
    attributes: dict[str, str] = field(default_factory=dict)


def setup_tracing(config: TracingConfig | None = None) -> bool:
    """Initialize OpenTelemetry tracing.

    Args:
        config: Tracing configuration. Uses defaults if None.

    Returns:
        True if tracing was successfully initialized.
    """
    global _tracer, _initialized

    if config is None:
        config = TracingConfig()

    if config.exporter == "none":
        _initialized = False
        _tracer = None
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        logger.debug("opentelemetry not installed, tracing disabled")
        _initialized = False
        _tracer = None
        return False

    resource_attrs = {"service.name": config.service_name}
    resource_attrs.update(config.attributes)
    resource = Resource.create(resource_attrs)

    provider = TracerProvider(resource=resource)

    # Configure exporter
    exporter = _create_exporter(config)
    if exporter is not None:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("sagewai", "0.1.0")
    _initialized = True

    logger.info(
        "OpenTelemetry tracing initialized: service=%s, exporter=%s",
        config.service_name,
        config.exporter,
    )
    return True


def _create_exporter(config: TracingConfig) -> Any:
    """Create a span exporter based on config."""
    if config.exporter == "console":
        try:
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter

            return ConsoleSpanExporter()
        except ImportError:
            return None

    if config.exporter == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            return OTLPSpanExporter(endpoint=config.endpoint)
        except ImportError:
            logger.warning("OTLP exporter not available, install opentelemetry-exporter-otlp")
            return None

    if config.exporter == "jaeger":
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            endpoint = config.endpoint or "localhost:4317"
            return OTLPSpanExporter(endpoint=endpoint)
        except ImportError:
            logger.warning("Jaeger exporter not available, install opentelemetry-exporter-otlp")
            return None

    return None


def get_tracer() -> Any:
    """Get the configured tracer, or a no-op tracer if not initialized."""
    if _tracer is not None:
        return _tracer

    try:
        from opentelemetry import trace

        return trace.get_tracer("sagewai")
    except ImportError:
        return _NoOpTracer()


def is_tracing_enabled() -> bool:
    """Check whether tracing is initialized."""
    return _initialized


def shutdown_tracing() -> None:
    """Shut down the tracer provider and flush pending spans."""
    global _tracer, _initialized
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
    except (ImportError, Exception):
        pass
    _tracer = None
    _initialized = False


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------


@contextmanager
def agent_span(agent_name: str, operation: str = "chat", **attributes: Any):
    """Context manager that creates a span for an agent operation.

    Args:
        agent_name: Name of the agent.
        operation: Operation type (chat, tool_call, memory_query).
        **attributes: Additional span attributes.

    Yields:
        The span object (or a no-op span).
    """
    tracer = get_tracer()
    span_name = f"{agent_name}.{operation}"

    if isinstance(tracer, _NoOpTracer):
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("agent.operation", operation)
        for key, value in attributes.items():
            span.set_attribute(f"agent.{key}", str(value))
        yield span


@contextmanager
def llm_span(model: str, **attributes: Any):
    """Context manager for LLM call spans.

    Args:
        model: Model identifier (e.g., "gpt-4o", "claude-3.5-sonnet").
        **attributes: Additional attributes (input_tokens, output_tokens, etc.).
    """
    tracer = get_tracer()

    if isinstance(tracer, _NoOpTracer):
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(f"llm.call.{model}") as span:
        span.set_attribute("llm.model", model)
        for key, value in attributes.items():
            span.set_attribute(f"llm.{key}", str(value))
        start = time.time()
        yield span
        span.set_attribute("llm.duration_ms", int((time.time() - start) * 1000))


@contextmanager
def tool_span(tool_name: str, **attributes: Any):
    """Context manager for tool execution spans."""
    tracer = get_tracer()

    if isinstance(tracer, _NoOpTracer):
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(f"tool.{tool_name}") as span:
        span.set_attribute("tool.name", tool_name)
        for key, value in attributes.items():
            span.set_attribute(f"tool.{key}", str(value))
        yield span


@contextmanager
def memory_span(operation: str, provider: str = "unknown", **attributes: Any):
    """Context manager for memory query/store spans."""
    tracer = get_tracer()

    if isinstance(tracer, _NoOpTracer):
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(f"memory.{operation}") as span:
        span.set_attribute("memory.operation", operation)
        span.set_attribute("memory.provider", provider)
        for key, value in attributes.items():
            span.set_attribute(f"memory.{key}", str(value))
        yield span


# ---------------------------------------------------------------------------
# Event hook for BaseAgent integration
# ---------------------------------------------------------------------------


def tracing_event_hook(event: Any, data: dict[str, Any] | None = None) -> None:
    """Event hook that creates spans from agent events.

    Attach to a BaseAgent via the ``on_event`` callback::

        agent = UniversalAgent(name="my-agent", on_event=tracing_event_hook)

    Args:
        event: An AgentEvent enum value.
        data: Event payload dict from BaseAgent._emit().
    """
    if not _initialized:
        return

    tracer = get_tracer()
    if isinstance(tracer, _NoOpTracer):
        return

    event_type = event.value if hasattr(event, "value") else str(event)
    if data is None:
        data = {}
    agent_name = data.get("agent", "unknown")

    span_name = f"event.{agent_name}.{event_type}"

    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute("event.type", str(event_type))
        span.set_attribute("event.agent_name", str(agent_name))
        for key, value in data.items():
            span.set_attribute(f"event.{key}", str(value))


# ---------------------------------------------------------------------------
# No-op fallbacks (zero overhead when OTel not installed)
# ---------------------------------------------------------------------------


class _NoOpSpan:
    """No-op span for when tracing is disabled."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record_exception(self, exc: Exception) -> None:
        pass

    def end(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _NoOpTracer:
    """No-op tracer for when OTel is not installed."""

    def start_as_current_span(self, name: str, **kwargs: Any):
        return _NoOpSpan()

    def start_span(self, name: str, **kwargs: Any):
        return _NoOpSpan()
