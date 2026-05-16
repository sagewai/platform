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
"""Example 14 — Register agents to your organization's registry.

Shows how to register agents with capability tags so that
orchestrators and other agents can discover and delegate to them.
The registry is thread-safe and supports singleton access.

Requirements::

    pip install sagewai

Usage::

    python 14_register_agent.py
"""

from __future__ import annotations

import asyncio

from sagewai.core.registry import AgentRegistry
from sagewai.engines.universal import UniversalAgent


async def main() -> None:
    """Register agents with capabilities to the global registry."""
    print("=" * 55)
    print("  Agent Registry — Registration Demo")
    print("=" * 55)
    print()

    registry = AgentRegistry()

    # ── Create agents ───────────────────────────────────────────
    researcher = UniversalAgent(
        name="research-agent",
        model="claude-haiku-4-5-20251001",
        system_prompt="You are a research specialist.",
    )
    writer = UniversalAgent(
        name="content-writer",
        model="claude-sonnet-4-5-20250929",
        system_prompt="You are a content writing expert.",
    )
    reviewer = UniversalAgent(
        name="code-reviewer",
        model="claude-sonnet-4-5-20250929",
        system_prompt="You review code for quality and security.",
    )

    # ── Register with capability tags ───────────────────────────
    registry.register(researcher, capabilities=["research", "search", "analysis"])
    registry.register(writer, capabilities=["writing", "drafting", "editing"])
    registry.register(reviewer, capabilities=["code-review", "security", "analysis"])

    print(f"Registered {len(registry)} agents:")
    print()
    for name, caps in registry.list_agents().items():
        print(f"  {name:<20} capabilities: {caps}")
    print()

    # ── Lookup by name ──────────────────────────────────────────
    agent = registry.get("research-agent")
    if agent:
        print(f"Lookup 'research-agent': found ({agent.config.model})")

    # ── Check membership ────────────────────────────────────────
    print(f"'content-writer' in registry: {'content-writer' in registry}")
    print(f"'unknown-agent' in registry:  {'unknown-agent' in registry}")
    print()

    # ── Unregister ──────────────────────────────────────────────
    registry.unregister("code-reviewer")
    print(f"After unregistering 'code-reviewer': {len(registry)} agents remain")
    print()

    print("Agents are now discoverable by capability tag.")
    print("See example 15 for discovery and delegation.")

    registry.clear()


if __name__ == "__main__":
    asyncio.run(main())
