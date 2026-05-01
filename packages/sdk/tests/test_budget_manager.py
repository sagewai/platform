# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for budget management with model fallback chains."""

from __future__ import annotations

import pytest

from sagewai.admin.budget import BudgetLimit, BudgetManager, cost_aware_rule


class TestBudgetLimit:
    """Test budget limit configuration."""

    def test_create_limit(self):
        limit = BudgetLimit(
            agent_name="test-agent",
            max_daily_usd=1.0,
            max_monthly_usd=20.0,
        )
        assert limit.agent_name == "test-agent"
        assert limit.max_daily_usd == 1.0
        assert limit.max_monthly_usd == 20.0
        assert limit.action == "warn"  # default
        assert limit.fallback_chain == []  # default

    def test_create_limit_with_fallback(self):
        limit = BudgetLimit(
            agent_name="test-agent",
            max_daily_usd=5.0,
            max_monthly_usd=100.0,
            action="throttle",
            fallback_chain=["gpt-4o-mini", "gemini-2.5-flash", "llama-3.1-8b-instant"],
        )
        assert limit.action == "throttle"
        assert len(limit.fallback_chain) == 3


class TestBudgetManager:
    """Test budget tracking and enforcement."""

    def test_record_spend(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(
            agent_name="agent-a",
            max_daily_usd=1.0,
            max_monthly_usd=20.0,
        ))
        mgr.record_spend(agent_name="agent-a", cost_usd=0.05)
        status = mgr.get_budget_status("agent-a")
        assert status["daily_spend_usd"] == pytest.approx(0.05)

    def test_within_budget(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(
            agent_name="agent-a",
            max_daily_usd=1.0,
            max_monthly_usd=20.0,
        ))
        mgr.record_spend(agent_name="agent-a", cost_usd=0.5)
        result = mgr.check_budget("agent-a")
        assert result.allowed
        assert result.action == "allow"

    def test_daily_budget_exceeded_warns(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(
            agent_name="agent-a",
            max_daily_usd=1.0,
            max_monthly_usd=20.0,
            action="warn",
        ))
        mgr.record_spend(agent_name="agent-a", cost_usd=1.5)
        result = mgr.check_budget("agent-a")
        assert not result.allowed
        assert result.action == "warn"

    def test_daily_budget_exceeded_stops(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(
            agent_name="agent-a",
            max_daily_usd=1.0,
            max_monthly_usd=20.0,
            action="stop",
        ))
        mgr.record_spend(agent_name="agent-a", cost_usd=1.5)
        result = mgr.check_budget("agent-a")
        assert not result.allowed
        assert result.action == "stop"

    def test_no_limit_always_allowed(self):
        mgr = BudgetManager()
        result = mgr.check_budget("unknown-agent")
        assert result.allowed

    def test_monthly_budget_exceeded(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(
            agent_name="agent-a",
            max_daily_usd=100.0,
            max_monthly_usd=5.0,
            action="stop",
        ))
        mgr.record_spend(agent_name="agent-a", cost_usd=6.0)
        result = mgr.check_budget("agent-a")
        assert not result.allowed

    def test_list_limits(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(agent_name="a", max_daily_usd=1.0, max_monthly_usd=20.0))
        mgr.add_limit(BudgetLimit(agent_name="b", max_daily_usd=2.0, max_monthly_usd=40.0))
        limits = mgr.list_limits()
        assert len(limits) == 2

    def test_remove_limit(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(agent_name="a", max_daily_usd=1.0, max_monthly_usd=20.0))
        mgr.remove_limit("a")
        assert len(mgr.list_limits()) == 0

    def test_update_limit(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(agent_name="a", max_daily_usd=1.0, max_monthly_usd=20.0))
        mgr.add_limit(BudgetLimit(agent_name="a", max_daily_usd=5.0, max_monthly_usd=100.0))
        limits = mgr.list_limits()
        assert len(limits) == 1
        assert limits[0].max_daily_usd == 5.0


class TestModelFallback:
    """Test model fallback chain selection when budget threshold hit."""

    def test_fallback_model_when_over_budget(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(
            agent_name="agent-a",
            max_daily_usd=1.0,
            max_monthly_usd=20.0,
            action="throttle",
            fallback_chain=["gpt-4o-mini", "gemini-2.5-flash"],
        ))
        mgr.record_spend(agent_name="agent-a", cost_usd=1.5)
        model = mgr.get_fallback_model("agent-a", current_model="gpt-4o")
        assert model == "gpt-4o-mini"  # First in fallback chain

    def test_no_fallback_when_within_budget(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(
            agent_name="agent-a",
            max_daily_usd=10.0,
            max_monthly_usd=200.0,
            fallback_chain=["gpt-4o-mini"],
        ))
        mgr.record_spend(agent_name="agent-a", cost_usd=0.5)
        model = mgr.get_fallback_model("agent-a", current_model="gpt-4o")
        assert model is None  # No fallback needed

    def test_fallback_skips_current_model(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(
            agent_name="agent-a",
            max_daily_usd=1.0,
            max_monthly_usd=20.0,
            action="throttle",
            fallback_chain=["gpt-4o", "gpt-4o-mini", "gemini-2.5-flash"],
        ))
        mgr.record_spend(agent_name="agent-a", cost_usd=1.5)
        model = mgr.get_fallback_model("agent-a", current_model="gpt-4o")
        assert model == "gpt-4o-mini"  # Skips gpt-4o since it's current

    def test_no_fallback_chain_configured(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(
            agent_name="agent-a",
            max_daily_usd=1.0,
            max_monthly_usd=20.0,
        ))
        mgr.record_spend(agent_name="agent-a", cost_usd=1.5)
        model = mgr.get_fallback_model("agent-a", current_model="gpt-4o")
        assert model is None  # No fallback chain


class TestCostAwareRule:
    """Test ModelRouter integration via cost_aware_rule."""

    def test_cost_aware_rule_returns_fallback(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(
            agent_name="agent-a",
            max_daily_usd=1.0,
            max_monthly_usd=20.0,
            action="throttle",
            fallback_chain=["gpt-4o-mini"],
        ))
        mgr.record_spend(agent_name="agent-a", cost_usd=1.5)
        rule = cost_aware_rule(mgr, agent_name="agent-a")
        # Rule condition should match when over budget
        assert rule.condition("any query", {"current_model": "gpt-4o"})
        assert rule.model == "gpt-4o-mini"

    def test_cost_aware_rule_no_match_within_budget(self):
        mgr = BudgetManager()
        mgr.add_limit(BudgetLimit(
            agent_name="agent-a",
            max_daily_usd=10.0,
            max_monthly_usd=200.0,
            fallback_chain=["gpt-4o-mini"],
        ))
        mgr.record_spend(agent_name="agent-a", cost_usd=0.5)
        rule = cost_aware_rule(mgr, agent_name="agent-a")
        assert not rule.condition("any query", {"current_model": "gpt-4o"})
