# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for PostgresAnalyticsStore — SQLite and Postgres."""

import pytest

from sagewai.admin.postgres_analytics import PostgresAnalyticsStore


@pytest.mark.asyncio
async def test_record_and_get_costs(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
    await store.record_cost("agent-a", "gpt-4o", 0.03, 500)
    result = await store.get_costs()
    assert result["total_cost_usd"] == pytest.approx(0.08, abs=0.001)
    assert result["record_count"] == 2


@pytest.mark.asyncio
async def test_get_costs_filtered_by_agent(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
    await store.record_cost("agent-b", "claude", 0.10, 2000)
    result = await store.get_costs(agent_name="agent-a")
    assert result["total_cost_usd"] == pytest.approx(0.05, abs=0.001)
    assert result["record_count"] == 1


@pytest.mark.asyncio
async def test_get_costs_no_filter_sums_all(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_cost("agent-a", "gpt-4o", 0.10, 1000)
    await store.record_cost("agent-b", "claude", 0.20, 2000)
    result = await store.get_costs()
    assert result["total_cost_usd"] == pytest.approx(0.30, abs=0.001)
    assert result["record_count"] == 2


@pytest.mark.asyncio
async def test_get_costs_filters_by_project(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_cost("agent-a", "gpt-4o", 0.10, 1000, project_id="project-a")
    await store.record_cost("agent-b", "claude", 0.20, 2000, project_id="project-b")
    result = await store.get_costs(project_id="project-a")
    assert result["total_cost_usd"] == pytest.approx(0.10, abs=0.001)
    assert result["record_count"] == 1


@pytest.mark.asyncio
async def test_get_usage(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
    await store.record_cost("agent-a", "claude", 0.03, 2000)
    result = await store.get_usage()
    assert result["total_tokens"] == 3000
    assert result["record_count"] == 2


@pytest.mark.asyncio
async def test_get_usage_filtered(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
    await store.record_cost("agent-b", "claude", 0.03, 9000)
    result = await store.get_usage(agent_name="agent-a")
    assert result["total_tokens"] == 1000


@pytest.mark.asyncio
async def test_get_usage_filters_by_project(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_cost("agent-a", "gpt-4o", 0.05, 1000, project_id="project-a")
    await store.record_cost("agent-b", "claude", 0.03, 9000, project_id="project-b")
    result = await store.get_usage(project_id="project-a")
    assert result["total_tokens"] == 1000
    assert result["record_count"] == 1


@pytest.mark.asyncio
async def test_record_and_get_risks(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_guardrail_event("agent-a", "pii_detected", "EMAIL found")
    await store.record_guardrail_event("agent-a", "hallucination", "Low score")
    await store.record_guardrail_event("agent-a", "content_filter", "Blocked")
    result = await store.get_risks()
    assert result["pii_events"] == 1
    assert result["hallucination_flags"] == 1
    assert result["content_filter_events"] == 1
    assert result["total_events"] == 3


@pytest.mark.asyncio
async def test_get_risks_filtered(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_guardrail_event("agent-a", "pii_detected")
    await store.record_guardrail_event("agent-b", "hallucination")
    result = await store.get_risks(agent_name="agent-a")
    assert result["pii_events"] == 1
    assert result["total_events"] == 1


@pytest.mark.asyncio
async def test_get_risks_filters_by_project(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_guardrail_event("agent-a", "pii_detected", project_id="project-a")
    await store.record_guardrail_event("agent-b", "hallucination", project_id="project-b")
    result = await store.get_risks(project_id="project-a")
    assert result["pii_events"] == 1
    assert result["total_events"] == 1


@pytest.mark.asyncio
async def test_get_risks_empty(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    result = await store.get_risks()
    assert result["pii_events"] == 0
    assert result["total_events"] == 0


@pytest.mark.asyncio
async def test_get_model_analytics(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
    await store.record_cost("agent-b", "gpt-4o", 0.03, 500)
    await store.record_cost("agent-a", "claude", 0.10, 2000)
    models = await store.get_model_analytics()
    by_name = {m["model"]: m for m in models}
    assert "gpt-4o" in by_name
    assert "claude" in by_name
    assert by_name["gpt-4o"]["request_count"] == 2
    assert by_name["gpt-4o"]["total_tokens"] == 1500
    assert by_name["claude"]["request_count"] == 1
    # cost_per_1k_tokens should be computed
    assert "cost_per_1k_tokens" in by_name["gpt-4o"]
    assert "is_local" in by_name["gpt-4o"]


@pytest.mark.asyncio
async def test_get_model_analytics_filters_by_project(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_cost("agent-a", "gpt-4o", 0.05, 1000, project_id="project-a")
    await store.record_cost("agent-b", "claude", 0.10, 2000, project_id="project-b")
    models = await store.get_model_analytics(project_id="project-a")
    assert [m["model"] for m in models] == ["gpt-4o"]


@pytest.mark.asyncio
async def test_get_agent_analytics(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
    await store.record_cost("agent-a", "claude", 0.10, 2000)
    await store.record_cost("agent-b", "gpt-4o", 0.03, 500)
    agents = await store.get_agent_analytics()
    by_name = {a["agent_name"]: a for a in agents}
    assert "agent-a" in by_name
    assert "agent-b" in by_name
    assert by_name["agent-a"]["request_count"] == 2
    # ARRAY_AGG(DISTINCT model) replacement — models_used must be a list with both models
    assert set(by_name["agent-a"]["models_used"]) == {"gpt-4o", "claude"}
    assert by_name["agent-a"]["total_tokens"] == 3000
    assert by_name["agent-b"]["request_count"] == 1
    assert "gpt-4o" in by_name["agent-b"]["models_used"]


@pytest.mark.asyncio
async def test_get_agent_analytics_filters_by_project(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_cost("agent-a", "gpt-4o", 0.05, 1000, project_id="project-a")
    await store.record_cost("agent-b", "claude", 0.10, 2000, project_id="project-b")
    agents = await store.get_agent_analytics(project_id="project-a")
    assert [a["agent_name"] for a in agents] == ["agent-a"]


@pytest.mark.asyncio
async def test_get_costs_empty_returns_zero(dialect_engine):
    store = PostgresAnalyticsStore(engine=dialect_engine)
    result = await store.get_costs()
    assert result["total_cost_usd"] == 0.0
    assert result["record_count"] == 0


@pytest.mark.asyncio
async def test_engine_kwarg_constructor(dialect_engine):
    """Constructor engine= kwarg works (main test pattern)."""
    store = PostgresAnalyticsStore(engine=dialect_engine)
    await store.record_cost("x", "m", 0.01, 100)
    result = await store.get_costs(agent_name="x")
    assert result["record_count"] == 1
