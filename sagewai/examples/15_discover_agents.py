#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 15 — Discover and invoke agents from the registry.

Registers a set of agents, then discovers them by capability tag
and delegates a task to the best match. This is the pattern used
by orchestrator agents to find specialists at runtime.

Requirements::

    pip install sagewai

Usage::

    python 15_discover_agents.py

Note: Delegation calls ``agent.chat()`` which requires a valid
API key. Set ``ANTHROPIC_API_KEY`` to run the delegation step.
Without it, discovery still works but delegation is skipped.
"""

from __future__ import annotations

import asyncio
import os

from sagewai.core.registry import AgentRegistry
from sagewai.engines.universal import UniversalAgent


async def main() -> None:
    """Discover agents by capability and delegate tasks."""
    print("=" * 55)
    print("  Agent Registry — Discovery & Delegation Demo")
    print("=" * 55)
    print()

    registry = AgentRegistry()

    # ── Populate the registry ───────────────────────────────────
    agents = [
        ("scout", ["research", "search"], "You find information on any topic."),
        ("writer", ["writing", "drafting"], "You write clear, concise content."),
        ("analyst", ["analysis", "research"], "You analyze data and trends."),
        ("translator", ["translation", "i18n"], "You translate between languages."),
    ]
    for name, caps, prompt in agents:
        agent = UniversalAgent(
            name=name,
            model="claude-haiku-4-5-20251001",
            system_prompt=prompt,
        )
        registry.register(agent, capabilities=caps)

    print(f"Registry has {len(registry)} agents")
    print()

    # ── Discover by capability ──────────────────────────────────
    for capability in ("research", "writing", "translation", "unknown"):
        found = registry.discover(capability)
        names = [a.config.name for a in found]
        print(f"  discover('{capability}'): {names or 'none'}")
    print()

    # ── Delegate a task ─────────────────────────────────────────
    has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
    if has_key:
        print("Delegating 'research' task to the first matching agent...")
        result = await registry.delegate(
            "research",
            "Summarize the key benefits of microservices architecture.",
        )
        print(f"  Response: {result[:120]}...")
    else:
        print("Set ANTHROPIC_API_KEY to run the delegation step.")
        print("Without it, discovery works but delegation is skipped.")
    print()

    # ── Delegate to a specific agent ────────────────────────────
    if has_key:
        print("Delegating 'research' to 'analyst' specifically...")
        result = await registry.delegate(
            "research",
            "Compare REST vs GraphQL for mobile backends.",
            agent_name="analyst",
        )
        print(f"  Response: {result[:120]}...")

    print()
    print("The registry pattern lets orchestrators discover")
    print("and delegate to specialist agents at runtime.")

    registry.clear()


if __name__ == "__main__":
    asyncio.run(main())
