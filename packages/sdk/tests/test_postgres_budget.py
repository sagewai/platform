"""Tests for PostgresBudgetManager.

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
async def mgr():
    from sagewai.admin.postgres_budget import PostgresBudgetManager

    m = PostgresBudgetManager(os.environ["SAGEWAI_DATABASE_URL"])
    await m.init()
    yield m
    await m.clear()
    await m.close()


class TestPostgresBudgetManager:
    async def test_add_and_list_limits(self, mgr):
        await mgr.add_limit(
            agent_name="agent-a",
            max_daily_usd=5.0,
            max_monthly_usd=100.0,
            action="warn",
        )
        limits = await mgr.list_limits()
        assert len(limits) == 1
        assert limits[0]["agent_name"] == "agent-a"
        assert limits[0]["max_daily_usd"] == pytest.approx(5.0)

    async def test_remove_limit(self, mgr):
        await mgr.add_limit(
            agent_name="agent-a",
            max_daily_usd=5.0,
            max_monthly_usd=100.0,
        )
        await mgr.remove_limit("agent-a")
        limits = await mgr.list_limits()
        assert len(limits) == 0

    async def test_record_spend_and_get_status(self, mgr):
        await mgr.add_limit(
            agent_name="agent-a",
            max_daily_usd=5.0,
            max_monthly_usd=100.0,
        )
        await mgr.record_spend(agent_name="agent-a", cost_usd=1.50)
        await mgr.record_spend(agent_name="agent-a", cost_usd=0.50)
        status = await mgr.get_budget_status("agent-a")
        assert status["daily_spend_usd"] == pytest.approx(2.0, abs=0.01)

    async def test_check_budget_within_limits(self, mgr):
        await mgr.add_limit(
            agent_name="agent-a",
            max_daily_usd=5.0,
            max_monthly_usd=100.0,
            action="stop",
        )
        await mgr.record_spend(agent_name="agent-a", cost_usd=1.0)
        result = await mgr.check_budget("agent-a")
        assert result["allowed"] is True

    async def test_check_budget_daily_exceeded(self, mgr):
        await mgr.add_limit(
            agent_name="agent-a",
            max_daily_usd=1.0,
            max_monthly_usd=100.0,
            action="stop",
        )
        await mgr.record_spend(agent_name="agent-a", cost_usd=1.50)
        result = await mgr.check_budget("agent-a")
        assert result["allowed"] is False
        assert result["action"] == "stop"

    async def test_check_budget_no_limit_set(self, mgr):
        result = await mgr.check_budget("no-such-agent")
        assert result["allowed"] is True

    async def test_get_fallback_model(self, mgr):
        await mgr.add_limit(
            agent_name="agent-a",
            max_daily_usd=0.01,
            max_monthly_usd=100.0,
            action="throttle",
            fallback_chain=["gpt-4o-mini", "gemini-2.5-flash"],
        )
        await mgr.record_spend(agent_name="agent-a", cost_usd=1.0)
        fallback = await mgr.get_fallback_model("agent-a", "gpt-4o")
        assert fallback == "gpt-4o-mini"

    async def test_upsert_limit(self, mgr):
        await mgr.add_limit(
            agent_name="agent-a",
            max_daily_usd=5.0,
            max_monthly_usd=100.0,
        )
        await mgr.add_limit(
            agent_name="agent-a",
            max_daily_usd=10.0,
            max_monthly_usd=200.0,
        )
        limits = await mgr.list_limits()
        assert len(limits) == 1
        assert limits[0]["max_daily_usd"] == pytest.approx(10.0)
