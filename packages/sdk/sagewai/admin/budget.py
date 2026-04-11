# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Budget management with model fallback chains.

Tracks per-agent spending, enforces daily/monthly budget limits,
and triggers model fallback when thresholds are exceeded.

Usage::

    from sagewai.admin.budget import BudgetManager, BudgetLimit

    mgr = BudgetManager()
    mgr.add_limit(BudgetLimit(
        agent_name="my-agent",
        max_daily_usd=5.0,
        max_monthly_usd=100.0,
        action="throttle",
        fallback_chain=["gpt-4o-mini", "gemini-2.5-flash"],
    ))

    # Record spend after each LLM call
    mgr.record_spend(agent_name="my-agent", cost_usd=0.01)

    # Check budget before allowing next call
    result = mgr.check_budget("my-agent")
    if not result.allowed:
        fallback = mgr.get_fallback_model("my-agent", current_model="gpt-4o")
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from sagewai.core.context import get_current_project
from sagewai.core.model_router import RoutingRule


def _resolve_project(project_id: str | None = None) -> str:
    """Resolve project_id from explicit param, contextvar, or default."""
    if project_id:
        return project_id
    ctx = get_current_project()
    return ctx.project_id if ctx else "default"


@dataclass
class BudgetLimit:
    """Budget configuration for a single agent.

    Args:
        agent_name: Agent this limit applies to.
        max_daily_usd: Maximum daily spend in USD.
        max_monthly_usd: Maximum monthly spend in USD.
        action: What to do when budget is exceeded.
        fallback_chain: Ordered list of cheaper models to fall back to.
    """

    agent_name: str
    max_daily_usd: float
    max_monthly_usd: float
    action: Literal["warn", "throttle", "stop"] = "warn"
    fallback_chain: list[str] = field(default_factory=list)


@dataclass
class BudgetCheckResult:
    """Result of a budget check."""

    allowed: bool
    action: str  # "allow", "warn", "throttle", "stop"
    reason: str | None = None
    daily_spend: float = 0.0
    monthly_spend: float = 0.0


@dataclass
class _SpendRecord:
    """Internal record of a spend event."""

    cost_usd: float
    timestamp: float = field(default_factory=time.time)


class BudgetManager:
    """Manage per-agent budgets with spend tracking and model fallback.

    In-memory implementation for MVP. For production, swap with a
    PostgreSQL-backed store.
    """

    def __init__(self) -> None:
        self._limits: dict[tuple[str, str], BudgetLimit] = {}
        self._spend: dict[tuple[str, str], list[_SpendRecord]] = defaultdict(list)

    def add_limit(
        self, limit: BudgetLimit, *, project_id: str | None = None
    ) -> None:
        """Add or update a budget limit for an agent."""
        key = (_resolve_project(project_id), limit.agent_name)
        self._limits[key] = limit

    def remove_limit(
        self, agent_name: str, *, project_id: str | None = None
    ) -> None:
        """Remove a budget limit for an agent."""
        key = (_resolve_project(project_id), agent_name)
        self._limits.pop(key, None)

    def list_limits(self, *, project_id: str | None = None) -> list[BudgetLimit]:
        """List all configured budget limits for a project."""
        resolved = _resolve_project(project_id)
        return [v for (pid, _), v in self._limits.items() if pid == resolved]

    def record_spend(
        self, *, agent_name: str, cost_usd: float, project_id: str | None = None
    ) -> None:
        """Record a cost event for an agent."""
        key = (_resolve_project(project_id), agent_name)
        self._spend[key].append(_SpendRecord(cost_usd=cost_usd))

    def _daily_spend(
        self, agent_name: str, *, project_id: str | None = None
    ) -> float:
        """Calculate today's total spend for an agent."""
        key = (_resolve_project(project_id), agent_name)
        now = time.time()
        day_start = now - (now % 86400)  # Start of current UTC day
        return sum(
            r.cost_usd
            for r in self._spend.get(key, [])
            if r.timestamp >= day_start
        )

    def _monthly_spend(
        self, agent_name: str, *, project_id: str | None = None
    ) -> float:
        """Calculate this month's total spend for an agent."""
        key = (_resolve_project(project_id), agent_name)
        now = time.time()
        month_start = now - (30 * 86400)  # Approximate: last 30 days
        return sum(
            r.cost_usd
            for r in self._spend.get(key, [])
            if r.timestamp >= month_start
        )

    def get_budget_status(
        self, agent_name: str, *, project_id: str | None = None
    ) -> dict[str, Any]:
        """Get current budget status for an agent."""
        resolved = _resolve_project(project_id)
        key = (resolved, agent_name)
        limit = self._limits.get(key)
        daily = self._daily_spend(agent_name, project_id=resolved)
        monthly = self._monthly_spend(agent_name, project_id=resolved)

        return {
            "agent_name": agent_name,
            "project_id": resolved,
            "daily_spend_usd": daily,
            "monthly_spend_usd": monthly,
            "max_daily_usd": limit.max_daily_usd if limit else None,
            "max_monthly_usd": limit.max_monthly_usd if limit else None,
            "daily_remaining_usd": (limit.max_daily_usd - daily) if limit else None,
            "monthly_remaining_usd": (limit.max_monthly_usd - monthly) if limit else None,
        }

    def check_budget(
        self, agent_name: str, *, project_id: str | None = None
    ) -> BudgetCheckResult:
        """Check if an agent is within budget.

        Returns a BudgetCheckResult with allowed=True if within limits,
        or allowed=False with the configured action if exceeded.
        """
        resolved = _resolve_project(project_id)
        key = (resolved, agent_name)
        limit = self._limits.get(key)
        if limit is None:
            return BudgetCheckResult(allowed=True, action="allow")

        daily = self._daily_spend(agent_name, project_id=resolved)
        monthly = self._monthly_spend(agent_name, project_id=resolved)

        if daily > limit.max_daily_usd:
            return BudgetCheckResult(
                allowed=False,
                action=limit.action,
                reason=f"Daily budget exceeded: ${daily:.4f} > ${limit.max_daily_usd:.4f}",
                daily_spend=daily,
                monthly_spend=monthly,
            )

        if monthly > limit.max_monthly_usd:
            return BudgetCheckResult(
                allowed=False,
                action=limit.action,
                reason=f"Monthly budget exceeded: ${monthly:.4f} > ${limit.max_monthly_usd:.4f}",
                daily_spend=daily,
                monthly_spend=monthly,
            )

        return BudgetCheckResult(
            allowed=True,
            action="allow",
            daily_spend=daily,
            monthly_spend=monthly,
        )

    def get_fallback_model(
        self,
        agent_name: str,
        current_model: str,
        *,
        project_id: str | None = None,
    ) -> str | None:
        """Get the fallback model for an agent when over budget.

        Returns None if within budget or no fallback chain configured.
        """
        resolved = _resolve_project(project_id)
        key = (resolved, agent_name)
        limit = self._limits.get(key)
        if limit is None or not limit.fallback_chain:
            return None

        result = self.check_budget(agent_name, project_id=resolved)
        if result.allowed:
            return None

        # Return first fallback model that isn't the current one
        for model in limit.fallback_chain:
            if model != current_model:
                return model

        return None


def cost_aware_rule(
    budget_manager: BudgetManager,
    *,
    agent_name: str,
    project_id: str | None = None,
) -> RoutingRule:
    """Create a ModelRouter RoutingRule that triggers when over budget.

    When the agent is over budget and has a fallback chain, this rule
    matches and returns the first available fallback model.

    Usage::

        from sagewai.core.model_router import ModelRouter
        from sagewai.admin.budget import BudgetManager, cost_aware_rule

        router = ModelRouter(
            rules=[cost_aware_rule(budget_mgr, agent_name="my-agent")],
            default_model="gpt-4o",
        )
    """
    resolved = _resolve_project(project_id)
    key = (resolved, agent_name)
    limit = budget_manager._limits.get(key)
    fallback_chain = limit.fallback_chain if limit else []
    # Determine fallback model (first in chain, or empty string)
    fallback_model = fallback_chain[0] if fallback_chain else ""

    def _condition(query: str, context: dict[str, Any]) -> bool:
        result = budget_manager.check_budget(agent_name, project_id=resolved)
        if result.allowed:
            return False
        # Check there's a valid fallback
        current = context.get("current_model", "")
        fb = budget_manager.get_fallback_model(
            agent_name, current_model=current, project_id=resolved
        )
        return fb is not None

    return RoutingRule(condition=_condition, model=fallback_model)
