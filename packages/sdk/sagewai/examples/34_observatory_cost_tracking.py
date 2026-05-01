#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 34 — Observatory cost tracking: see the wallet for every run.

The Observatory dashboard's most-asked-for view is **where is the money
going**. This example wires the SDK's :class:`CostTracker` and
:class:`BudgetManager` together to show what your operators see in
production:

- Per-run token + cost breakdown (one row per LLM call)
- Per-project, per-agent, per-model roll-ups
- Local-vs-cloud token mix — the cost-down advantage in numbers
- Pre-flight budget check (warn / throttle / stop)
- "What would this have cost if we'd routed everything to cloud?"
  counterfactual that quantifies the savings

We simulate a multi-tenant org running 3 projects (acme, globex,
initech) with 8 runs across 4 models. No live LLM calls — pricing
comes from :data:`MODEL_PRICING`, so the demo runs offline and
deterministically.

What you see:

1. A run-by-run trace (model, tokens, cost)
2. A roll-up grid (project × model)
3. The savings if local routing had been used everywhere
4. A budget check showing one project would have been throttled

In production these same numbers are emitted as OTel metrics
(``llm_call_cost_usd``, ``llm_call_tokens``) tagged with
``sagewai.project_id`` and rendered in the Observatory's "Cost" page.

Requirements::

    pip install sagewai

Usage::

    python 34_observatory_cost_tracking.py
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, field

from sagewai.admin.budget import BudgetManager
from sagewai.harness.budget import HarnessBudgetManager
from sagewai.observability.costs import (
    CostTracker,
    calculate_cost,
    is_local_model,
)


# ── simulated workload ─────────────────────────────────────────────


@dataclass
class SimulatedRun:
    """One agent run that issues a sequence of LLM calls."""

    project_id: str
    agent_name: str
    calls: list[tuple[str, int, int]] = field(default_factory=list)
    """List of (model, input_tokens, output_tokens) per call."""


# 8 runs across 3 projects + 4 models
WORKLOAD: list[SimulatedRun] = [
    # ── ACME (enterprise tier, mostly cloud) ──
    SimulatedRun(
        project_id="acme-prod",
        agent_name="customer-support",
        calls=[
            ("claude-sonnet-4-5-20250929", 3200, 800),
            ("claude-sonnet-4-5-20250929", 1800, 400),
        ],
    ),
    SimulatedRun(
        project_id="acme-prod",
        agent_name="legal-reviewer",
        calls=[
            ("claude-opus-4-6", 5000, 1200),
            ("claude-opus-4-6", 4200, 800),
        ],
    ),
    SimulatedRun(
        project_id="acme-prod",
        agent_name="email-triage",
        calls=[
            ("claude-haiku-4-5-20251001", 800, 200),
            ("claude-haiku-4-5-20251001", 600, 150),
            ("claude-haiku-4-5-20251001", 700, 180),
        ],
    ),
    # ── GLOBEX (premium tier, mixed local + cloud) ──
    SimulatedRun(
        project_id="globex-prod",
        agent_name="docs-search",
        calls=[
            ("ollama/llama3:70b", 2000, 500),  # local — free
            ("gpt-4o-mini", 1200, 300),
        ],
    ),
    SimulatedRun(
        project_id="globex-prod",
        agent_name="code-reviewer",
        calls=[
            ("gpt-4o-mini", 3500, 900),
            ("gpt-4o-mini", 2800, 700),
        ],
    ),
    # ── INITECH (starter tier, mostly local) ──
    SimulatedRun(
        project_id="initech-prod",
        agent_name="local-summariser",
        calls=[
            ("ollama/llama3:70b", 4000, 1000),  # local — free
            ("ollama/llama3:70b", 3200, 800),  # local — free
        ],
    ),
    SimulatedRun(
        project_id="initech-prod",
        agent_name="prototype-bot",
        calls=[
            ("ollama/llama3:70b", 1500, 400),  # local — free
            ("gpt-4o-mini", 600, 150),  # one cloud fallback
        ],
    ),
    SimulatedRun(
        project_id="initech-prod",
        agent_name="batch-classifier",
        calls=[
            ("ollama/llama3:70b", 8000, 2000),  # local — free
        ],
    ),
]


# ── helpers ────────────────────────────────────────────────────────


def _fmt_usd(amount: float) -> str:
    if amount == 0:
        return "$0.000000"
    return f"${amount:.6f}"


def _bar(amount: float, max_amount: float, width: int = 20) -> str:
    if max_amount == 0:
        return ""
    filled = int(round((amount / max_amount) * width))
    return "█" * filled + "·" * (width - filled)


# ── main ───────────────────────────────────────────────────────────


async def main() -> None:
    print("─" * 72)
    print(" Sagewai Observatory — cost tracking dashboard (example 34)")
    print("─" * 72)
    print()

    # 1. Track per-project costs by attaching one CostTracker per project.
    #    In production the harness emits llm_call_finished events that the
    #    tracker consumes via event_hook; here we record calls directly.
    trackers: dict[str, CostTracker] = defaultdict(CostTracker)

    print("  Simulating 8 runs across 3 projects, 4 models…")
    print()

    # 2. Run the workload — each call is recorded, costs computed from
    #    the pricing table.
    for run in WORKLOAD:
        tracker = trackers[run.project_id]
        tracker.start_run(run.agent_name)
        for model, in_toks, out_toks in run.calls:
            tracker.record_call(
                model=model,
                input_tokens=in_toks,
                output_tokens=out_toks,
            )
        tracker.end_run()

    # 3. Per-run trace
    print("─" * 72)
    print(" Run trace — chronological")
    print("─" * 72)
    print()
    print(f"  {'project':<14} {'agent':<22} {'calls':>5} {'tokens':>8} {'cost':>13}")
    print(f"  {'-'*14} {'-'*22} {'-'*5} {'-'*8} {'-'*13}")
    for run in WORKLOAD:
        # Find the run summary that matches this agent (last one added)
        proj_runs = trackers[run.project_id].runs
        summary = next(r for r in proj_runs if r.agent_name == run.agent_name)
        print(
            f"  {run.project_id:<14} {run.agent_name:<22} "
            f"{summary.call_count:>5} "
            f"{summary.total_tokens:>8} "
            f"{_fmt_usd(summary.total_cost_usd):>13}"
        )
    print()

    # 4. Per-project roll-up
    print("─" * 72)
    print(" Project roll-up — total spend per tenant")
    print("─" * 72)
    print()
    proj_totals = {p: t.total_cost for p, t in trackers.items()}
    proj_tokens = {p: t.total_tokens for p, t in trackers.items()}
    grand_total = sum(proj_totals.values())
    grand_tokens = sum(proj_tokens.values())
    max_cost = max(proj_totals.values()) if proj_totals else 1.0
    print(f"  {'project':<14} {'tokens':>8} {'cost':>13}  share")
    print(f"  {'-'*14} {'-'*8} {'-'*13}  {'-'*22}")
    for p in sorted(proj_totals, key=lambda x: -proj_totals[x]):
        share = (proj_totals[p] / grand_total) if grand_total > 0 else 0.0
        bar = _bar(proj_totals[p], max_cost)
        print(
            f"  {p:<14} {proj_tokens[p]:>8} "
            f"{_fmt_usd(proj_totals[p]):>13}  {bar} {share:>5.1%}"
        )
    print(f"  {'-'*14} {'-'*8} {'-'*13}")
    print(f"  {'TOTAL':<14} {grand_tokens:>8} {_fmt_usd(grand_total):>13}")
    print()

    # 5. Per-model roll-up — which models are eating the budget?
    print("─" * 72)
    print(" Model roll-up — spend by model")
    print("─" * 72)
    print()
    model_totals: dict[str, float] = defaultdict(float)
    model_tokens: dict[str, int] = defaultdict(int)
    model_calls: dict[str, int] = defaultdict(int)
    for tracker in trackers.values():
        for run in tracker.runs:
            for c in run.calls:
                model_totals[c.model] += c.cost_usd
                model_tokens[c.model] += c.input_tokens + c.output_tokens
                model_calls[c.model] += 1
    max_model = max(model_totals.values()) if model_totals else 1.0
    print(f"  {'model':<32} {'calls':>5} {'tokens':>8} {'cost':>13}")
    print(f"  {'-'*32} {'-'*5} {'-'*8} {'-'*13}")
    for m in sorted(model_totals, key=lambda x: -model_totals[x]):
        bar = _bar(model_totals[m], max_model, width=15)
        local = "(local)" if is_local_model(m) else ""
        print(
            f"  {m:<32} {model_calls[m]:>5} {model_tokens[m]:>8} "
            f"{_fmt_usd(model_totals[m]):>13}  {bar} {local}"
        )
    print()

    # 6. Local-vs-cloud token mix — the cost-down advantage in numbers
    print("─" * 72)
    print(" Local-vs-cloud mix — the cost-down advantage")
    print("─" * 72)
    print()
    local_tokens = sum(t for m, t in model_tokens.items() if is_local_model(m))
    cloud_tokens = grand_tokens - local_tokens
    local_pct = (local_tokens / grand_tokens * 100) if grand_tokens else 0.0
    print(f"  Cloud tokens:  {cloud_tokens:>8} ({100 - local_pct:>5.1f}%)  "
          f"cost {_fmt_usd(grand_total)}")
    print(f"  Local tokens:  {local_tokens:>8} ({local_pct:>5.1f}%)  cost   $0.000000")
    print(f"  Total tokens:  {grand_tokens:>8}")
    print()

    # 7. Counterfactual — what if every cloud call had been local?
    counterfactual_savings = grand_total  # local is $0, so all cloud spend would vanish
    # And — what if everything had stayed on cloud (no local routing)?
    cloud_only_extra = 0.0
    for tracker in trackers.values():
        for run in tracker.runs:
            for c in run.calls:
                if is_local_model(c.model):
                    # Use claude-sonnet pricing as the default cloud fallback
                    cloud_only_extra += calculate_cost(
                        c.input_tokens, c.output_tokens, "claude-sonnet-4-5-20250929",
                    )
    print(
        f"  If local-everywhere:  cost would drop from "
        f"{_fmt_usd(grand_total)} → $0.000000  "
        f"(saves {_fmt_usd(counterfactual_savings)})"
    )
    print(
        f"  If cloud-everywhere:  cost would rise to     "
        f"{_fmt_usd(grand_total + cloud_only_extra)}  "
        f"(extra {_fmt_usd(cloud_only_extra)})"
    )
    print()

    # 8. Budget pre-flight — would any project trip a limit?
    print("─" * 72)
    print(" Budget enforcement — pre-flight check")
    print("─" * 72)
    print()
    budget = HarnessBudgetManager(BudgetManager())
    # Per-tenant daily limits (USD)
    budget.configure_team_budget("acme-prod", max_daily_usd=0.20, max_monthly_usd=5.00, action="warn")
    budget.configure_team_budget("globex-prod", max_daily_usd=0.05, max_monthly_usd=1.00, action="throttle")
    budget.configure_team_budget("initech-prod", max_daily_usd=0.01, max_monthly_usd=0.30, action="stop")

    # We use a synthetic agent-runtime user_id since these are bot runs;
    # the team scope (project_id) is what carries the budget.
    SYSTEM_USER = "system-bot"
    for p, tracker in trackers.items():
        budget.record_spend(
            user_id=SYSTEM_USER, team_id=p, cost_usd=tracker.total_cost,
        )
        result = budget.check_budget(user_id=SYSTEM_USER, team_id=p)
        verdict = "ALLOWED" if result.allowed else "DENIED"
        print(
            f"  {p:<14} spent {_fmt_usd(tracker.total_cost):>13}  "
            f"verdict={verdict:<8} action={result.action}"
        )
    print()

    # 9. Final JSON dump — same shape the Observatory dashboard
    #    consumes from the /api/v1/observatory/costs endpoint.
    print("─" * 72)
    print(" JSON snapshot (Observatory dashboard payload)")
    print("─" * 72)
    print()
    snapshot = {
        "summary": {
            "grand_total_usd": round(grand_total, 6),
            "grand_total_tokens": grand_tokens,
            "local_token_pct": round(local_pct, 1),
            "savings_if_cloud_only": round(cloud_only_extra, 6),
        },
        "by_project": [
            {
                "project_id": p,
                "tokens": proj_tokens[p],
                "cost_usd": round(proj_totals[p], 6),
                "runs": len(trackers[p].runs),
            }
            for p in sorted(proj_totals, key=lambda x: -proj_totals[x])
        ],
        "by_model": [
            {
                "model": m,
                "calls": model_calls[m],
                "tokens": model_tokens[m],
                "cost_usd": round(model_totals[m], 6),
                "is_local": is_local_model(m),
            }
            for m in sorted(model_totals, key=lambda x: -model_totals[x])
        ],
    }
    print(json.dumps(snapshot, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
