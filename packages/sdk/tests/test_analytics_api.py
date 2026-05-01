# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for analytics API endpoints."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin.analytics import AnalyticsStore, create_analytics_router


@pytest.fixture
def analytics_store() -> AnalyticsStore:
    store = AnalyticsStore()
    # Seed with sample data
    store.record_cost(agent_name="agent-a", model="gpt-4o", cost_usd=0.005, tokens=500)
    store.record_cost(agent_name="agent-a", model="gpt-4o-mini", cost_usd=0.001, tokens=200)
    store.record_cost(
        agent_name="agent-b", model="claude-sonnet-4-5-20250514", cost_usd=0.003, tokens=300
    )
    store.record_guardrail_event(agent_name="agent-a", event_type="pii", entity="EMAIL")
    store.record_guardrail_event(
        agent_name="agent-a", event_type="hallucination", score=0.2
    )
    return store


@pytest.fixture
def client(analytics_store: AnalyticsStore) -> TestClient:
    app = FastAPI()
    app.include_router(create_analytics_router(analytics_store), prefix="/analytics")
    return TestClient(app)


class TestCostAnalytics:
    def test_get_costs(self, client: TestClient):
        resp = client.get("/analytics/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_cost_usd" in data
        assert data["total_cost_usd"] > 0
        assert "by_model" in data
        assert "by_agent" in data

    def test_get_costs_by_agent(self, client: TestClient):
        resp = client.get("/analytics/costs?agent_name=agent-a")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_cost_usd"] == pytest.approx(0.006)


class TestUsageAnalytics:
    def test_get_usage(self, client: TestClient):
        resp = client.get("/analytics/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_tokens" in data
        assert data["total_tokens"] == 1000

    def test_get_usage_by_model(self, client: TestClient):
        resp = client.get("/analytics/usage")
        data = resp.json()
        assert "by_model" in data


class TestRiskAnalytics:
    def test_get_risks(self, client: TestClient):
        resp = client.get("/analytics/risks")
        assert resp.status_code == 200
        data = resp.json()
        assert "pii_events" in data
        assert data["pii_events"] >= 1
        assert "hallucination_flags" in data

    def test_get_risks_by_agent(self, client: TestClient):
        resp = client.get("/analytics/risks?agent_name=agent-a")
        data = resp.json()
        assert data["pii_events"] >= 1


class TestModelAnalytics:
    def test_get_model_comparison(self, client: TestClient):
        resp = client.get("/analytics/models")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2  # At least gpt-4o and claude


class TestAgentAnalytics:
    def test_get_agent_analytics(self, client: TestClient):
        resp = client.get("/analytics/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2  # agent-a and agent-b
