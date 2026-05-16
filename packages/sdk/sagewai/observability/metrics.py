# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Prometheus counters for agent runs and LLM costs.

Exposes four metric families:

- ``agent_runs_total`` -- Counter (labels: agent_name, status)
- ``agent_run_duration_seconds`` -- Histogram (labels: agent_name)
- ``llm_cost_usd_total`` -- Counter (labels: model, agent_name)
- ``llm_tokens_total`` -- Counter (labels: model, agent_name, token_type)

All imports are guarded with try/except so prometheus_client remains an
optional dependency. When the library is not installed, the module exports
no-op stubs and :func:`metrics_event_hook` silently ignores events.

Usage::

    from sagewai.observability.metrics import metrics_event_hook

    agent = UniversalAgent(name="my-agent", on_event=metrics_event_hook)

Requires ``prometheus_client`` (optional)::

    uv add --optional prometheus prometheus_client
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional prometheus_client import
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter, Histogram

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False
    Counter = None  # type: ignore[assignment,misc]
    Histogram = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Metric definitions (created only when prometheus_client is installed)
# ---------------------------------------------------------------------------

agent_runs_total: Any = None
agent_run_duration_seconds: Any = None
llm_cost_usd_total: Any = None
llm_tokens_total: Any = None

# Workflow metrics
workflow_runs_total: Any = None
workflow_step_duration_seconds: Any = None
workflow_stale_recoveries_total: Any = None

if _PROMETHEUS_AVAILABLE:
    agent_runs_total = Counter(
        "agent_runs_total",
        "Total number of agent runs",
        ["agent_name", "status"],
    )
    agent_run_duration_seconds = Histogram(
        "agent_run_duration_seconds",
        "Duration of agent runs in seconds",
        ["agent_name"],
        buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
    )
    llm_cost_usd_total = Counter(
        "llm_cost_usd_total",
        "Cumulative LLM cost in USD",
        ["model", "agent_name"],
    )
    llm_tokens_total = Counter(
        "llm_tokens_total",
        "Total LLM tokens consumed",
        ["model", "agent_name", "token_type"],
    )
    workflow_runs_total = Counter(
        "workflow_runs_total",
        "Total workflow runs",
        ["workflow_name", "status"],
    )
    workflow_step_duration_seconds = Histogram(
        "workflow_step_duration_seconds",
        "Duration of workflow steps",
        ["step_name"],
        buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0, 600.0),
    )
    workflow_stale_recoveries_total = Counter(
        "workflow_stale_recoveries_total",
        "Total stale workflow recoveries",
        [],
    )

# ---------------------------------------------------------------------------
# Internal run-start tracking (for duration computation)
# ---------------------------------------------------------------------------

_run_start_times: dict[str, float] = {}

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def is_prometheus_available() -> bool:
    """Return True if prometheus_client is installed."""
    return _PROMETHEUS_AVAILABLE


def reset_metrics() -> None:
    """Clear all internal state (useful for testing).

    Note: this does NOT reset the Prometheus collectors themselves -- those
    are process-global singletons. It only clears the run-start time cache.
    """
    _run_start_times.clear()


# ---------------------------------------------------------------------------
# Event hook
# ---------------------------------------------------------------------------


async def metrics_event_hook(event: Any, data: dict[str, Any] | None = None) -> None:
    """Async event hook that increments Prometheus counters.

    Attach to a BaseAgent via the ``on_event`` callback::

        agent = UniversalAgent(name="scout", on_event=metrics_event_hook)

    Handles the following events:

    - **run_started** -- records the start timestamp for duration tracking.
    - **run_finished** -- increments ``agent_runs_total`` (status=success),
      observes ``agent_run_duration_seconds``.
    - **run_error** -- increments ``agent_runs_total`` (status=error),
      observes ``agent_run_duration_seconds``.
    - **llm_call_finished** -- increments ``llm_cost_usd_total`` and
      ``llm_tokens_total`` (input + output).

    Args:
        event: An AgentEvent enum value (or any object with a ``.value`` attr).
        data: Event payload dict from BaseAgent._emit().
    """
    if not _PROMETHEUS_AVAILABLE:
        return

    if data is None:
        data = {}

    event_type = event.value if hasattr(event, "value") else str(event)
    agent_name = data.get("agent", "unknown")

    if event_type == "run_started":
        _run_start_times[agent_name] = time.monotonic()

    elif event_type == "run_finished":
        agent_runs_total.labels(agent_name=agent_name, status="success").inc()
        _observe_duration(agent_name)

    elif event_type == "run_error":
        agent_runs_total.labels(agent_name=agent_name, status="error").inc()
        _observe_duration(agent_name)

    elif event_type == "llm_call_finished":
        model = data.get("model", "unknown")
        cost_usd = data.get("cost_usd", 0.0)
        input_tokens = data.get("input_tokens", 0)
        output_tokens = data.get("output_tokens", 0)

        if cost_usd:
            llm_cost_usd_total.labels(model=model, agent_name=agent_name).inc(cost_usd)
        if input_tokens:
            llm_tokens_total.labels(
                model=model, agent_name=agent_name, token_type="input"
            ).inc(input_tokens)
        if output_tokens:
            llm_tokens_total.labels(
                model=model, agent_name=agent_name, token_type="output"
            ).inc(output_tokens)


def _observe_duration(agent_name: str) -> None:
    """Observe run duration if a start time was recorded."""
    start = _run_start_times.pop(agent_name, None)
    if start is not None:
        elapsed = time.monotonic() - start
        agent_run_duration_seconds.labels(agent_name=agent_name).observe(elapsed)
