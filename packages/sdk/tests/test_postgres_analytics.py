# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for PostgresAnalyticsStore.

Requires PostgreSQL: SAGEWAI_DATABASE_URL env var.
Skip if not available.
"""

import os

import pytest
import pytest_asyncio

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("SAGEWAI_DATABASE_URL"),
        reason="SAGEWAI_DATABASE_URL not set",
    ),
]


@pytest_asyncio.fixture
async def store():
    from sagewai.admin.postgres_analytics import PostgresAnalyticsStore

    s = PostgresAnalyticsStore(os.environ["SAGEWAI_DATABASE_URL"])
    await s.init()
    yield s
    await s.clear()
    await s.close()


class TestPostgresAnalyticsStore:
    async def test_record_and_get_costs(self, store):
        await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
        await store.record_cost("agent-a", "gpt-4o", 0.03, 500)
        result = await store.get_costs()
        assert result["total_cost_usd"] == pytest.approx(0.08, abs=0.001)
        assert result["record_count"] == 2

    async def test_get_costs_filtered_by_agent(self, store):
        await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
        await store.record_cost("agent-b", "claude", 0.10, 2000)
        result = await store.get_costs(agent_name="agent-a")
        assert result["total_cost_usd"] == pytest.approx(0.05, abs=0.001)
        assert result["record_count"] == 1

    async def test_record_and_get_guardrail_events(self, store):
        await store.record_guardrail_event("agent-a", "pii_detected", "EMAIL found")
        await store.record_guardrail_event("agent-a", "hallucination", "Low score")
        result = await store.get_risks()
        assert result["pii_events"] == 1
        assert result["hallucination_flags"] == 1
        assert result["total"] == 2

    async def test_get_usage(self, store):
        await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
        await store.record_cost("agent-a", "claude", 0.03, 2000)
        result = await store.get_usage()
        assert result["total_tokens"] == 3000

    async def test_get_model_analytics(self, store):
        await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
        await store.record_cost("agent-b", "gpt-4o", 0.03, 500)
        models = await store.get_model_analytics()
        assert len(models) == 1
        assert models[0]["model"] == "gpt-4o"
        assert models[0]["requests"] == 2

    async def test_get_agent_analytics(self, store):
        await store.record_cost("agent-a", "gpt-4o", 0.05, 1000)
        await store.record_cost("agent-a", "claude", 0.10, 2000)
        agents = await store.get_agent_analytics()
        assert len(agents) == 1
        assert agents[0]["agent_name"] == "agent-a"
        assert agents[0]["requests"] == 2
        assert set(agents[0]["models_used"]) == {"gpt-4o", "claude"}
