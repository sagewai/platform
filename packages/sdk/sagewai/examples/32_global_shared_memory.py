#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Example 32 — GlobalMemory: many agents, one shared knowledge surface.

The complement to per-mission :class:`MemoryBranch` (Example 29) and
the multi-model relay (Example 31). ``GlobalMemory`` lets every agent
in your deployment read AND write a single shared memory surface.
Use cases:

- An on-call team where every triage agent learns from prior incidents
- A code-review squad where reviewers' notes accumulate over time
- A research org where every agent contributes findings to a common pool

This example simulates **5 different agents** (a triager, an on-call
escalator, a customer-success bot, a runbook keeper, a postmortem
writer) each writing their own observations to one shared
``GlobalMemory`` scope. Then a 6th agent (the "summary" agent)
retrieves from the pool and produces a coherent team brief.

Plus what every Sagewai example demonstrates:

- ``GlobalMemory.get(scope=...)`` — singleton-per-scope
- Concurrent adds from many agents (asyncio gather)
- Per-scope isolation: agents in scope-A don't see scope-B
- ``stats()`` for the Observatory dashboard

**Multi-worker deployments — important:**

The default storage backend is process-local. For multi-worker setups
where the same ``scope=...`` must be visible from multiple processes
or hosts, configure a shared backend at startup::

    from sagewai.memory.global_memory_backends import PostgresBackend
    GlobalMemory.configure_backend(
        PostgresBackend(connection_pool=postgres_store.pool)
    )
    await GlobalMemory.ensure_backend_ready()

Or :class:`RedisBackend` for a fast cache. The example below uses the
default in-process backend; swap one line to switch.

Requirements::

    pip install sagewai

Usage::

    python 32_global_shared_memory.py
"""

from __future__ import annotations

import asyncio

from sagewai.memory import GlobalMemory


# ── 5 agents, each contributes 2 observations concurrently ────────


async def triager(team: GlobalMemory) -> None:
    """The on-call triage agent."""
    await team.add("Triage: incident #P-2026-001 was caused by a memory leak in api-gateway.")
    await team.add("Triage: customer ACME-CORP has had 3 sev-2 incidents this quarter.")


async def escalator(team: GlobalMemory) -> None:
    """The on-call escalation agent."""
    await team.add("Escalation: incident #P-2026-001 paged the founder after 45 min unresolved.")
    await team.add("Escalation: ACME-CORP has an executive sponsor — escalate within 15 min.")


async def cs_bot(team: GlobalMemory) -> None:
    """The customer-success agent."""
    await team.add("Customer success: ACME-CORP renewed their contract through 2027.")
    await team.add("Customer success: ACME-CORP's primary contact is sarah@acme-corp.com.")


async def runbook_keeper(team: GlobalMemory) -> None:
    """The runbook documentation agent."""
    await team.add("Runbook: api-gateway memory leak fix is in runbooks/api-gateway-restart.md.")
    await team.add("Runbook: standard escalation tree is on-call-1 → on-call-2 → founder after 30 min.")


async def postmortem(team: GlobalMemory) -> None:
    """The postmortem-writing agent."""
    await team.add("Postmortem: incident #P-2026-001 — root cause was a connection pool leak.")
    await team.add("Postmortem: action item #1 — add memory-leak alert at 80% RSS to api-gateway.")


# ── the summarising agent ─────────────────────────────────────────


async def summary_agent(team: GlobalMemory) -> str:
    """Reads the shared memory and produces a coherent brief.

    No LLM call required — for this example we just walk the retrieved
    facts and group them. In a real Sagewai deployment, swap in a
    UniversalAgent + LLM call for the actual generation step.
    """
    incident_facts = await team.retrieve("incident #P-2026-001", top_k=5)
    customer_facts = await team.retrieve("ACME-CORP", top_k=5)

    brief = ["═══ TEAM BRIEF — ACME-CORP / incident P-2026-001 ═══", ""]
    brief.append("--- Incident state ---")
    for f in incident_facts:
        brief.append(f"  • {f}")
    brief.append("")
    brief.append("--- Customer context ---")
    for f in customer_facts:
        brief.append(f"  • {f}")
    return "\n".join(brief)


# ── main ──────────────────────────────────────────────────────────


async def main() -> None:
    print("─" * 72)
    print(" Sagewai GlobalMemory — many agents, one shared brain (example 32)")
    print("─" * 72)
    print()

    # 1. Get the shared memory for this team. Every agent that calls
    #    GlobalMemory.get(scope="oncall-team") gets the SAME instance.
    team = GlobalMemory.get(scope="oncall-team")
    print(f"  Team memory created — scope='{team.scope}'")
    print()

    # 2. Five agents contribute observations CONCURRENTLY.
    #    GlobalMemory's per-scope lock serialises adds without
    #    blocking the agents from running in parallel.
    print("  Five agents writing observations concurrently…")
    await asyncio.gather(
        triager(team),
        escalator(team),
        cs_bot(team),
        runbook_keeper(team),
        postmortem(team),
    )

    stats = team.stats()
    print(f"    {stats['add_count']} facts added (5 agents × 2 each)")
    print(f"    age: {stats['age_seconds']}s")
    print()

    # 3. The 6th agent — summary — reads from the shared pool
    print("  Summary agent retrieving from shared pool…")
    print()
    brief = await summary_agent(team)
    print(brief)
    print()

    # 4. Demonstrate scope isolation — a different scope sees nothing
    print("─" * 72)
    print(" Demonstrating scope isolation")
    print("─" * 72)
    print()
    other_team = GlobalMemory.get(scope="finance-team")
    other_team_results = await other_team.retrieve("ACME-CORP", top_k=5)
    print(f"  finance-team scope retrieving 'ACME-CORP': {len(other_team_results)} results")
    print(f"  oncall-team scope retrieving 'ACME-CORP': "
          f"{len(await team.retrieve('ACME-CORP', top_k=5))} results")
    print()
    print("  → Different scopes never share memory. The on-call team and")
    print("    the finance team can't see each other's notes.")
    print()

    # 5. Final stats
    print("─" * 72)
    print(" GlobalMemory stats (consumable by the Observatory dashboard)")
    print("─" * 72)
    print()
    for scope in GlobalMemory.list_scopes():
        gm = GlobalMemory.get(scope=scope)
        s = gm.stats()
        print(f"  scope={s['scope']:<20} adds={s['add_count']:>3}  retrieves={s['retrieve_count']:>3}  age={s['age_seconds']}s")


if __name__ == "__main__":
    asyncio.run(main())
