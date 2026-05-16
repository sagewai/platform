#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Example 12 — Team budgets with automatic enforcement.

Shows how to configure per-user, per-team, and per-project budget
limits with different enforcement actions:

- **warn**: log a warning but allow the request
- **throttle**: downgrade to a cheaper model
- **stop**: block the request entirely

When a user exceeds their budget, the harness applies the configured
action automatically — no human intervention needed.

Requirements::

    pip install sagewai[harness]

Usage::

    python 12_budget_enforcement.py
"""

from __future__ import annotations

import asyncio

from sagewai.admin.budget import BudgetManager
from sagewai.harness.budget import HarnessBudgetManager


async def main() -> None:
    """Demonstrate multi-scope budget enforcement."""
    print("=" * 55)
    print("  LLM Harness — Budget Enforcement Demo")
    print("=" * 55)
    print()

    budget = HarnessBudgetManager(BudgetManager())

    # ── Configure budgets for three developers ──────────────────
    budget.configure_user_budget(
        "alice", max_daily_usd=10.00, max_monthly_usd=200.00, action="warn",
    )
    budget.configure_user_budget(
        "bob", max_daily_usd=3.00, max_monthly_usd=30.00, action="throttle",
    )
    budget.configure_user_budget(
        "intern", max_daily_usd=1.00, max_monthly_usd=10.00, action="stop",
    )
    print("Configured budgets:")
    print("  Alice  — $10/day, $200/month (warn on exceed)")
    print("  Bob    — $3/day,  $30/month  (downgrade on exceed)")
    print("  Intern — $1/day,  $10/month  (block on exceed)")
    print()

    # ── Configure a shared team budget ──────────────────────────
    budget.configure_team_budget(
        "engineering", max_daily_usd=50.00, max_monthly_usd=500.00, action="warn",
    )
    print("Team budget: engineering — $50/day, $500/month")
    print()

    # ── Simulate spend ──────────────────────────────────────────
    print("Simulating spend...")
    budget.record_spend(user_id="alice", team_id="engineering", cost_usd=8.50)
    budget.record_spend(user_id="bob", team_id="engineering", cost_usd=2.80)
    budget.record_spend(user_id="intern", team_id="engineering", cost_usd=0.95)
    print("  Alice:  $8.50 spent")
    print("  Bob:    $2.80 spent")
    print("  Intern: $0.95 spent")
    print()

    # ── Check each user's budget status ─────────────────────────
    for user_id in ("alice", "bob", "intern"):
        result = budget.check_budget(user_id=user_id, team_id="engineering")
        allowed_str = "ALLOWED" if result.allowed else "DENIED"
        print(f"  {user_id:8s} -> {allowed_str} (action={result.action})")

    print()

    # ── Push intern over the limit ──────────────────────────────
    print("Intern makes one more request ($0.10)...")
    budget.record_spend(user_id="intern", team_id="engineering", cost_usd=0.10)
    result = budget.check_budget(user_id="intern", team_id="engineering")
    print(f"  Result: allowed={result.allowed}, action={result.action}")
    if not result.allowed:
        print("  The intern's request is BLOCKED. Budget exceeded.")
    print()

    print("Budget enforcement keeps your LLM costs predictable.")
    print("Configure per-user, per-team, and per-project limits")
    print("with warn / throttle / stop actions.")


if __name__ == "__main__":
    asyncio.run(main())
