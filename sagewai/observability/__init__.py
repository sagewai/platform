# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Observability module — cost tracking, metrics, prompt logging, tracing, and audit."""

from sagewai.observability.audit import (
    AuditBackend,
    AuditEvent,
    AuditLogger,
    FileAuditBackend,
    InMemoryAuditBackend,
)
from sagewai.observability.costs import (
    CostTracker,
    LLMCallRecord,
    RunSummary,
    calculate_cost,
    get_model_pricing,
)
from sagewai.observability.metrics import (
    agent_run_duration_seconds,
    agent_runs_total,
    is_prometheus_available,
    llm_cost_usd_total,
    llm_tokens_total,
    metrics_event_hook,
    reset_metrics,
)
from sagewai.observability.prompt_store import (
    PromptLogRecord,
    PromptStore,
)
from sagewai.observability.tracing import (
    TracingConfig,
    agent_span,
    is_tracing_enabled,
    llm_span,
    memory_span,
    setup_tracing,
    shutdown_tracing,
    tool_span,
    tracing_event_hook,
)

__all__ = [
    "AuditBackend",
    "AuditEvent",
    "AuditLogger",
    "CostTracker",
    "FileAuditBackend",
    "InMemoryAuditBackend",
    "LLMCallRecord",
    "PromptLogRecord",
    "PromptStore",
    "RunSummary",
    "TracingConfig",
    "agent_run_duration_seconds",
    "agent_runs_total",
    "agent_span",
    "calculate_cost",
    "get_model_pricing",
    "is_prometheus_available",
    "is_tracing_enabled",
    "llm_cost_usd_total",
    "llm_span",
    "llm_tokens_total",
    "memory_span",
    "metrics_event_hook",
    "reset_metrics",
    "setup_tracing",
    "shutdown_tracing",
    "tool_span",
    "tracing_event_hook",
]
