# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Analytics API — cost, usage, risk, and model analytics.

In-memory analytics store (MVP) with FastAPI router for querying.
Designed for easy swap to PostgreSQL-backed store in production.

Usage::

    from sagewai.admin.analytics import AnalyticsStore, create_analytics_router

    store = AnalyticsStore()
    app.include_router(create_analytics_router(store), prefix="/analytics")
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sagewai.core.context import get_current_project, resolve_project_id
from sagewai.observability.costs import is_local_model


def _resolve_project(project_id: str | None = None) -> str:
    """Resolve project_id from explicit param, contextvar, or default.

    Used by internal SDK store methods where the caller is trusted.
    HTTP-facing endpoints should use ``resolve_project_id()`` directly
    to honour the middleware-set contextvar.
    """
    if project_id:
        return project_id
    ctx = get_current_project()
    return ctx.project_id if ctx else "default"


@dataclass
class CostRecord:
    """A single cost event."""

    agent_name: str
    model: str
    cost_usd: float
    tokens: int
    project_id: str = "default"
    timestamp: float = field(default_factory=time.time)


@dataclass
class GuardrailEvent:
    """A single guardrail event (PII detection, hallucination flag, etc.)."""

    agent_name: str
    event_type: str  # "pii", "hallucination", "content_filter"
    project_id: str = "default"
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class AnalyticsStore:
    """In-memory analytics store for cost, usage, and risk tracking.

    Thread-safe for single-process use. For production, swap with
    a PostgreSQL-backed implementation.
    """

    def __init__(self) -> None:
        self._costs: list[CostRecord] = []
        self._guardrail_events: list[GuardrailEvent] = []

    def record_cost(
        self,
        *,
        agent_name: str,
        model: str,
        cost_usd: float,
        tokens: int,
        project_id: str | None = None,
    ) -> None:
        """Record a cost event."""
        self._costs.append(
            CostRecord(
                agent_name=agent_name,
                model=model,
                cost_usd=cost_usd,
                tokens=tokens,
                project_id=_resolve_project(project_id),
            )
        )

    def record_guardrail_event(
        self,
        *,
        agent_name: str,
        event_type: str,
        project_id: str | None = None,
        **details: Any,
    ) -> None:
        """Record a guardrail event (PII, hallucination, etc.)."""
        self._guardrail_events.append(
            GuardrailEvent(
                agent_name=agent_name,
                event_type=event_type,
                project_id=_resolve_project(project_id),
                details=details,
            )
        )

    def _filter_costs(
        self,
        agent_name: str | None = None,
        project_id: str | None = None,
    ) -> list[CostRecord]:
        """Filter cost records by project and optionally agent."""
        resolved = _resolve_project(project_id)
        records = [r for r in self._costs if r.project_id == resolved]
        if agent_name:
            records = [r for r in records if r.agent_name == agent_name]
        return records

    def _filter_events(
        self,
        agent_name: str | None = None,
        project_id: str | None = None,
    ) -> list[GuardrailEvent]:
        """Filter guardrail events by project and optionally agent."""
        resolved = _resolve_project(project_id)
        events = [e for e in self._guardrail_events if e.project_id == resolved]
        if agent_name:
            events = [e for e in events if e.agent_name == agent_name]
        return events

    def get_costs(
        self, agent_name: str | None = None, *, project_id: str | None = None
    ) -> dict[str, Any]:
        """Get cost analytics, optionally filtered by agent, scoped to project."""
        records = self._filter_costs(agent_name, project_id)

        total = sum(r.cost_usd for r in records)
        by_model: dict[str, float] = defaultdict(float)
        by_agent: dict[str, float] = defaultdict(float)
        for r in records:
            by_model[r.model] += r.cost_usd
            by_agent[r.agent_name] += r.cost_usd

        return {
            "total_cost_usd": total,
            "by_model": dict(by_model),
            "by_agent": dict(by_agent),
            "record_count": len(records),
        }

    def get_usage(
        self, agent_name: str | None = None, *, project_id: str | None = None
    ) -> dict[str, Any]:
        """Get token usage analytics, scoped to project."""
        records = self._filter_costs(agent_name, project_id)

        total_tokens = sum(r.tokens for r in records)
        by_model: dict[str, int] = defaultdict(int)
        by_agent: dict[str, int] = defaultdict(int)
        for r in records:
            by_model[r.model] += r.tokens
            by_agent[r.agent_name] += r.tokens

        return {
            "total_tokens": total_tokens,
            "by_model": dict(by_model),
            "by_agent": dict(by_agent),
            "record_count": len(records),
        }

    def get_risks(
        self, agent_name: str | None = None, *, project_id: str | None = None
    ) -> dict[str, Any]:
        """Get risk analytics (PII events, hallucination flags), scoped to project."""
        events = self._filter_events(agent_name, project_id)

        pii_events = sum(1 for e in events if e.event_type == "pii")
        hallucination_flags = sum(1 for e in events if e.event_type == "hallucination")
        content_filter_events = sum(
            1 for e in events if e.event_type == "content_filter"
        )

        return {
            "pii_events": pii_events,
            "hallucination_flags": hallucination_flags,
            "content_filter_events": content_filter_events,
            "total_events": len(events),
        }

    def get_model_analytics(
        self, *, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get per-model analytics for comparison, scoped to project."""
        records = self._filter_costs(project_id=project_id)
        model_data: dict[str, dict[str, Any]] = {}
        for r in records:
            if r.model not in model_data:
                model_data[r.model] = {
                    "model": r.model,
                    "total_cost_usd": 0.0,
                    "total_tokens": 0,
                    "request_count": 0,
                }
            model_data[r.model]["total_cost_usd"] += r.cost_usd
            model_data[r.model]["total_tokens"] += r.tokens
            model_data[r.model]["request_count"] += 1

        # Calculate cost per 1K tokens + tag local models
        for data in model_data.values():
            tokens = data["total_tokens"]
            data["cost_per_1k_tokens"] = (
                (data["total_cost_usd"] / tokens * 1000) if tokens > 0 else 0.0
            )
            data["is_local"] = is_local_model(data["model"])

        return list(model_data.values())

    def get_agent_analytics(
        self, *, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get per-agent analytics, scoped to project."""
        records = self._filter_costs(project_id=project_id)
        agent_data: dict[str, dict[str, Any]] = {}
        for r in records:
            if r.agent_name not in agent_data:
                agent_data[r.agent_name] = {
                    "agent_name": r.agent_name,
                    "total_cost_usd": 0.0,
                    "total_tokens": 0,
                    "request_count": 0,
                    "models_used": set(),
                }
            agent_data[r.agent_name]["total_cost_usd"] += r.cost_usd
            agent_data[r.agent_name]["total_tokens"] += r.tokens
            agent_data[r.agent_name]["request_count"] += 1
            agent_data[r.agent_name]["models_used"].add(r.model)

        # Convert sets to lists for JSON serialization
        result = []
        for data in agent_data.values():
            data["models_used"] = sorted(data["models_used"])
            result.append(data)
        return result


async def _maybe_await(result: Any) -> Any:
    """Await *result* if it is a coroutine/awaitable; otherwise return as-is.

    This lets ``create_analytics_router`` work transparently with both the
    synchronous in-memory ``AnalyticsStore`` and the async
    ``PostgresAnalyticsStore`` without requiring callers to distinguish them.
    """
    import inspect
    if inspect.isawaitable(result):
        return await result
    return result


def create_analytics_router(store: Any):
    """Create a FastAPI router with analytics endpoints.

    Args:
        store: AnalyticsStore (sync) or PostgresAnalyticsStore (async) instance.

    Returns:
        A FastAPI APIRouter.
    """
    from fastapi import APIRouter, Query

    router = APIRouter(tags=["analytics"])

    @router.get("/costs")
    async def get_costs(
        agent_name: str | None = Query(None),
    ) -> dict[str, Any]:
        """Get cost analytics, optionally filtered by agent."""
        pid = resolve_project_id()
        return await _maybe_await(store.get_costs(agent_name=agent_name, project_id=pid))

    @router.get("/usage")
    async def get_usage(
        agent_name: str | None = Query(None),
    ) -> dict[str, Any]:
        """Get token usage analytics."""
        pid = resolve_project_id()
        return await _maybe_await(store.get_usage(agent_name=agent_name, project_id=pid))

    @router.get("/risks")
    async def get_risks(
        agent_name: str | None = Query(None),
    ) -> dict[str, Any]:
        """Get risk analytics (PII, hallucination, content filter events)."""
        pid = resolve_project_id()
        return await _maybe_await(store.get_risks(agent_name=agent_name, project_id=pid))

    @router.get("/models")
    async def get_model_comparison() -> list[dict[str, Any]]:
        """Get per-model analytics for comparison."""
        pid = resolve_project_id()
        return await _maybe_await(store.get_model_analytics(project_id=pid))

    @router.get("/agents")
    async def get_agent_analytics() -> list[dict[str, Any]]:
        """Get per-agent analytics."""
        pid = resolve_project_id()
        return await _maybe_await(store.get_agent_analytics(project_id=pid))

    return router
