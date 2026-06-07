# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for PostgresBudgetManager — SQLite and Postgres."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import insert

from sagewai.admin.postgres_budget import PostgresBudgetManager
from sagewai.db.models import BudgetSpend

_spend_tbl = BudgetSpend.__table__


@pytest.mark.asyncio
async def test_add_and_list_limits(dialect_engine):
    mgr = PostgresBudgetManager(engine=dialect_engine)
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
    assert limits[0]["max_monthly_usd"] == pytest.approx(100.0)
    assert limits[0]["action"] == "warn"


@pytest.mark.asyncio
async def test_upsert_limit_conflict_path(dialect_engine):
    """Second add_limit on same agent+project updates in place."""
    mgr = PostgresBudgetManager(engine=dialect_engine)
    await mgr.add_limit(agent_name="agent-a", max_daily_usd=5.0, max_monthly_usd=100.0)
    await mgr.add_limit(agent_name="agent-a", max_daily_usd=10.0, max_monthly_usd=200.0)
    limits = await mgr.list_limits()
    assert len(limits) == 1
    assert limits[0]["max_daily_usd"] == pytest.approx(10.0)
    assert limits[0]["max_monthly_usd"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_remove_limit(dialect_engine):
    mgr = PostgresBudgetManager(engine=dialect_engine)
    await mgr.add_limit(agent_name="agent-a", max_daily_usd=5.0, max_monthly_usd=100.0)
    await mgr.remove_limit("agent-a")
    limits = await mgr.list_limits()
    assert len(limits) == 0


@pytest.mark.asyncio
async def test_record_spend_and_get_status(dialect_engine):
    mgr = PostgresBudgetManager(engine=dialect_engine)
    await mgr.add_limit(agent_name="agent-a", max_daily_usd=5.0, max_monthly_usd=100.0)
    await mgr.record_spend(agent_name="agent-a", cost_usd=1.50)
    await mgr.record_spend(agent_name="agent-a", cost_usd=0.50)
    status = await mgr.get_budget_status("agent-a")
    assert status["daily_spend_usd"] == pytest.approx(2.0, abs=0.01)
    assert status["monthly_spend_usd"] == pytest.approx(2.0, abs=0.01)
    assert status["max_daily_usd"] == pytest.approx(5.0)
    assert status["max_monthly_usd"] == pytest.approx(100.0)
    assert status["agent_name"] == "agent-a"


@pytest.mark.asyncio
async def test_check_budget_within_limits(dialect_engine):
    mgr = PostgresBudgetManager(engine=dialect_engine)
    await mgr.add_limit(
        agent_name="agent-a",
        max_daily_usd=5.0,
        max_monthly_usd=100.0,
        action="stop",
    )
    await mgr.record_spend(agent_name="agent-a", cost_usd=1.0)
    result = await mgr.check_budget("agent-a")
    assert result["allowed"] is True


@pytest.mark.asyncio
async def test_check_budget_daily_exceeded(dialect_engine):
    mgr = PostgresBudgetManager(engine=dialect_engine)
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
    assert "Daily" in result["reason"]


@pytest.mark.asyncio
async def test_check_budget_monthly_exceeded(dialect_engine):
    mgr = PostgresBudgetManager(engine=dialect_engine)
    await mgr.add_limit(
        agent_name="agent-a",
        max_daily_usd=1000.0,
        max_monthly_usd=0.50,
        action="throttle",
    )
    await mgr.record_spend(agent_name="agent-a", cost_usd=1.0)
    result = await mgr.check_budget("agent-a")
    assert result["allowed"] is False
    assert result["action"] == "throttle"
    assert "Monthly" in result["reason"]


@pytest.mark.asyncio
async def test_check_budget_no_limit_set(dialect_engine):
    mgr = PostgresBudgetManager(engine=dialect_engine)
    result = await mgr.check_budget("no-such-agent")
    assert result["allowed"] is True
    assert result["action"] == "allow"


@pytest.mark.asyncio
async def test_fallback_model(dialect_engine):
    mgr = PostgresBudgetManager(engine=dialect_engine)
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


@pytest.mark.asyncio
async def test_fallback_model_within_budget_returns_none(dialect_engine):
    mgr = PostgresBudgetManager(engine=dialect_engine)
    await mgr.add_limit(
        agent_name="agent-a",
        max_daily_usd=100.0,
        max_monthly_usd=1000.0,
        fallback_chain=["gpt-4o-mini"],
    )
    await mgr.record_spend(agent_name="agent-a", cost_usd=0.01)
    fallback = await mgr.get_fallback_model("agent-a", "gpt-4o")
    assert fallback is None


@pytest.mark.asyncio
async def test_fallback_chain_json_roundtrip(dialect_engine):
    """Fallback chain is stored as JSON and round-trips correctly."""
    mgr = PostgresBudgetManager(engine=dialect_engine)
    chain = ["gpt-4o-mini", "gemini-2.5-flash", "llama-3"]
    await mgr.add_limit(
        agent_name="agent-a",
        max_daily_usd=5.0,
        max_monthly_usd=100.0,
        fallback_chain=chain,
    )
    limits = await mgr.list_limits()
    assert limits[0]["fallback_chain"] == chain


@pytest.mark.asyncio
async def test_project_isolation(dialect_engine):
    """Limits are scoped to project_id."""
    mgr = PostgresBudgetManager(engine=dialect_engine)
    await mgr.add_limit(
        agent_name="agent-a", max_daily_usd=5.0, max_monthly_usd=100.0, project_id="proj1"
    )
    await mgr.add_limit(
        agent_name="agent-a", max_daily_usd=10.0, max_monthly_usd=200.0, project_id="proj2"
    )
    limits_p1 = await mgr.list_limits(project_id="proj1")
    limits_p2 = await mgr.list_limits(project_id="proj2")
    assert len(limits_p1) == 1 and limits_p1[0]["max_daily_usd"] == pytest.approx(5.0)
    assert len(limits_p2) == 1 and limits_p2[0]["max_daily_usd"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_engine_kwarg_constructor(dialect_engine):
    """Constructor engine= kwarg works (main test pattern)."""
    mgr = PostgresBudgetManager(engine=dialect_engine)
    await mgr.add_limit(agent_name="x", max_daily_usd=1.0, max_monthly_usd=10.0)
    limits = await mgr.list_limits()
    assert len(limits) == 1


@pytest.mark.asyncio
async def test_time_window_boundary(dialect_engine):
    """2-days-ago spend: excluded from daily window, included in monthly window."""
    mgr = PostgresBudgetManager(engine=dialect_engine)
    await mgr.add_limit(agent_name="agent-a", max_daily_usd=1000.0, max_monthly_usd=1000.0)

    old_ts = datetime.now(timezone.utc) - timedelta(days=2)
    # Insert the old row directly so we can set an explicit created_at
    async with dialect_engine.begin() as conn:
        await conn.execute(
            insert(_spend_tbl).values(
                agent_name="agent-a",
                project_id="default",
                cost_usd=5.0,
                created_at=old_ts,
            )
        )

    # Record a "now" spend through the normal path
    await mgr.record_spend(agent_name="agent-a", cost_usd=1.0)

    status = await mgr.get_budget_status("agent-a")

    # 2-days-ago spend must NOT appear in today's window
    assert status["daily_spend_usd"] == pytest.approx(1.0, abs=0.01)
    # Both spends (5.0 + 1.0) must appear in the 30-day monthly window
    assert status["monthly_spend_usd"] == pytest.approx(6.0, abs=0.01)
