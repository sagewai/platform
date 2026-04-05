# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Harness budget management with user/team/project scoping.

Wraps the existing BudgetManager to provide multi-scope budget enforcement
for the LLM Harness. Instead of requiring a separate budget table, it keys
on composite names like ``harness:user:{user_id}``, ``harness:team:{team_id}``,
``harness:project:{project_id}``.

Usage::

    from sagewai.admin.budget import BudgetManager
    from sagewai.harness.budget import HarnessBudgetManager

    mgr = HarnessBudgetManager(BudgetManager())
    mgr.configure_user_budget("alice", max_daily_usd=5.0, max_monthly_usd=50.0)
    result = mgr.check_budget(user_id="alice")
"""

from __future__ import annotations

import logging
from typing import Any

from dataclasses import dataclass

from sagewai.admin.budget import BudgetCheckResult, BudgetLimit, BudgetManager

logger = logging.getLogger(__name__)


@dataclass
class HarnessBudgetResult:
    """Budget check result adapted for the harness router.

    Provides an ``exceeded`` flag (inverse of BudgetCheckResult.allowed)
    for clearer router integration.
    """

    exceeded: bool
    action: str  # "warn", "throttle", "stop", "allow"
    reason: str | None = None
    daily_spend: float = 0.0
    monthly_spend: float = 0.0

    @classmethod
    def from_check(cls, result: BudgetCheckResult) -> "HarnessBudgetResult":
        """Create from a BudgetCheckResult."""
        return cls(
            exceeded=not result.allowed,
            action=result.action,
            reason=result.reason,
            daily_spend=result.daily_spend,
            monthly_spend=result.monthly_spend,
        )

_PREFIX_USER = "harness:user"
_PREFIX_TEAM = "harness:team"
_PREFIX_PROJECT = "harness:project"


def _user_key(user_id: str) -> str:
    """Build composite budget key for a user."""
    return f"{_PREFIX_USER}:{user_id}"


def _team_key(team_id: str) -> str:
    """Build composite budget key for a team."""
    return f"{_PREFIX_TEAM}:{team_id}"


def _project_key(project_id: str) -> str:
    """Build composite budget key for a project."""
    return f"{_PREFIX_PROJECT}:{project_id}"


class HarnessBudgetManager:
    """Multi-scope budget enforcement for the LLM Harness.

    Delegates to the existing ``BudgetManager`` using composite agent names
    to scope budgets by user, team, and project without additional tables.

    Args:
        budget_manager: The underlying ``BudgetManager`` instance.
    """

    def __init__(self, budget_manager: BudgetManager) -> None:
        self._mgr = budget_manager

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure_user_budget(
        self,
        user_id: str,
        *,
        max_daily_usd: float,
        max_monthly_usd: float,
        action: str = "warn",
        fallback_chain: list[str] | None = None,
    ) -> None:
        """Configure a budget limit scoped to a single user.

        Args:
            user_id: Unique identifier of the user.
            max_daily_usd: Maximum allowed daily spend in USD.
            max_monthly_usd: Maximum allowed monthly spend in USD.
            action: Enforcement action (``"warn"``, ``"throttle"``, or ``"stop"``).
            fallback_chain: Optional ordered list of cheaper fallback models.
        """
        self._mgr.add_limit(
            BudgetLimit(
                agent_name=_user_key(user_id),
                max_daily_usd=max_daily_usd,
                max_monthly_usd=max_monthly_usd,
                action=action,  # type: ignore[arg-type]
                fallback_chain=fallback_chain or [],
            )
        )
        logger.debug("Configured user budget for %s", user_id)

    def configure_team_budget(
        self,
        team_id: str,
        *,
        max_daily_usd: float,
        max_monthly_usd: float,
        action: str = "warn",
        fallback_chain: list[str] | None = None,
    ) -> None:
        """Configure a budget limit scoped to a team.

        Args:
            team_id: Unique identifier of the team.
            max_daily_usd: Maximum allowed daily spend in USD.
            max_monthly_usd: Maximum allowed monthly spend in USD.
            action: Enforcement action (``"warn"``, ``"throttle"``, or ``"stop"``).
            fallback_chain: Optional ordered list of cheaper fallback models.
        """
        self._mgr.add_limit(
            BudgetLimit(
                agent_name=_team_key(team_id),
                max_daily_usd=max_daily_usd,
                max_monthly_usd=max_monthly_usd,
                action=action,  # type: ignore[arg-type]
                fallback_chain=fallback_chain or [],
            )
        )
        logger.debug("Configured team budget for %s", team_id)

    def configure_project_budget(
        self,
        project_id: str,
        *,
        max_daily_usd: float,
        max_monthly_usd: float,
        action: str = "warn",
        fallback_chain: list[str] | None = None,
    ) -> None:
        """Configure a budget limit scoped to a project.

        Args:
            project_id: Unique identifier of the project.
            max_daily_usd: Maximum allowed daily spend in USD.
            max_monthly_usd: Maximum allowed monthly spend in USD.
            action: Enforcement action (``"warn"``, ``"throttle"``, or ``"stop"``).
            fallback_chain: Optional ordered list of cheaper fallback models.
        """
        self._mgr.add_limit(
            BudgetLimit(
                agent_name=_project_key(project_id),
                max_daily_usd=max_daily_usd,
                max_monthly_usd=max_monthly_usd,
                action=action,  # type: ignore[arg-type]
                fallback_chain=fallback_chain or [],
            )
        )
        logger.debug("Configured project budget for %s", project_id)

    # ------------------------------------------------------------------
    # Budget checks
    # ------------------------------------------------------------------

    def _applicable_keys(
        self,
        *,
        user_id: str,
        team_id: str | None = None,
        project_id: str | None = None,
    ) -> list[str]:
        """Return all composite budget keys that apply to the given scope."""
        keys = [_user_key(user_id)]
        if team_id is not None:
            keys.append(_team_key(team_id))
        if project_id is not None:
            keys.append(_project_key(project_id))
        return keys

    def check_budget(
        self,
        *,
        user_id: str,
        team_id: str | None = None,
        project_id: str | None = None,
    ) -> BudgetCheckResult:
        """Check budget across all applicable scopes.

        Evaluates user, team, and project budgets (where configured) and
        returns the **most restrictive** result -- i.e. the first that is
        disallowed, or the one with the least remaining budget if all pass.

        Args:
            user_id: The user whose budget to check.
            team_id: Optional team scope.
            project_id: Optional project scope.

        Returns:
            The most restrictive ``BudgetCheckResult`` across all scopes.
        """
        keys = self._applicable_keys(
            user_id=user_id, team_id=team_id, project_id=project_id
        )
        results = [self._mgr.check_budget(k) for k in keys]

        # Any disallowed result is immediately most restrictive.
        denied = [r for r in results if not r.allowed]
        if denied:
            # Pick the one with the harshest action (stop > throttle > warn).
            severity = {"stop": 3, "throttle": 2, "warn": 1}
            denied.sort(key=lambda r: severity.get(r.action, 0), reverse=True)
            return denied[0]

        # All allowed -- return the one with the least remaining headroom.
        # We approximate headroom as the sum of daily + monthly remaining;
        # lower means closer to the limit.
        if not results:
            return BudgetCheckResult(allowed=True, action="allow")

        def _headroom(r: BudgetCheckResult) -> float:
            """Estimate remaining budget headroom from check result."""
            # If there's no configured limit the manager returns
            # daily_spend=0 / monthly_spend=0 with action="allow".
            # Treat unlimited as infinite headroom.
            return float("inf") if r.action == "allow" else -(r.daily_spend + r.monthly_spend)

        # The result with the smallest headroom is the tightest.
        results.sort(key=_headroom)
        return results[0]

    async def check(self, identity: Any) -> HarnessBudgetResult:
        """Async-compatible budget check accepting a HarnessIdentity.

        Convenience method used by ``HarnessRouter`` to integrate budget
        enforcement without unpacking identity fields manually.

        Args:
            identity: A ``HarnessIdentity`` or any object with ``user_id``,
                ``team_id``, and ``project_id`` attributes.

        Returns:
            A ``HarnessBudgetResult`` reflecting the most restrictive scope.
        """
        result = self.check_budget(
            user_id=identity.user_id,
            team_id=getattr(identity, "team_id", None),
            project_id=getattr(identity, "project_id", None),
        )
        return HarnessBudgetResult.from_check(result)

    # ------------------------------------------------------------------
    # Spend recording
    # ------------------------------------------------------------------

    def record_spend(
        self,
        *,
        user_id: str,
        team_id: str | None = None,
        project_id: str | None = None,
        cost_usd: float,
    ) -> None:
        """Record spend against all applicable budget scopes.

        Args:
            user_id: The user incurring the cost.
            team_id: Optional team scope.
            project_id: Optional project scope.
            cost_usd: The cost in USD to record.
        """
        keys = self._applicable_keys(
            user_id=user_id, team_id=team_id, project_id=project_id
        )
        for key in keys:
            self._mgr.record_spend(agent_name=key, cost_usd=cost_usd)
        logger.debug(
            "Recorded $%.6f spend across %d scope(s)", cost_usd, len(keys)
        )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_budget_status(
        self,
        *,
        user_id: str,
        team_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Return combined budget status with per-scope breakdown.

        Args:
            user_id: The user to query.
            team_id: Optional team scope.
            project_id: Optional project scope.

        Returns:
            A dict with ``"scopes"`` (per-key status dicts) and a top-level
            ``"allowed"`` flag reflecting the most restrictive check.
        """
        keys = self._applicable_keys(
            user_id=user_id, team_id=team_id, project_id=project_id
        )

        scopes: dict[str, dict[str, Any]] = {}
        for key in keys:
            scopes[key] = self._mgr.get_budget_status(key)

        overall = self.check_budget(
            user_id=user_id, team_id=team_id, project_id=project_id
        )

        return {
            "allowed": overall.allowed,
            "action": overall.action,
            "reason": overall.reason,
            "scopes": scopes,
        }
