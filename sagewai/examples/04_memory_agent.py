#!/usr/bin/env python3
# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 04 — Agents That Remember: Persistent Memory.

Agents without memory forget everything between messages. With Sagewai's
memory system, agents retain facts across conversations using vector and
graph stores.

This example uses the in-memory graph store (no external DB needed).
For production, use Milvus (vector) and NebulaGraph (graph).

Requirements::

    pip install sagewai

Usage::

    export OPENAI_API_KEY=sk-...
    python 04_memory_agent.py
"""

from __future__ import annotations

import asyncio

from sagewai.engines.universal import UniversalAgent
from sagewai.memory.graph import GraphMemory


async def main() -> None:
    # Create a persistent memory store
    memory = GraphMemory()

    agent = UniversalAgent(
        name="assistant",
        model="gpt-4o-mini",
        memory=memory,
        system_prompt="You are a personal assistant. Remember facts the user tells you.",
    )

    # Conversation 1: teach the agent some facts
    print("--- Conversation 1: Teaching facts ---")
    r1 = await agent.chat("My name is Alice and I work at Acme Corp as a data scientist.")
    print(f"Agent: {r1}\n")

    r2 = await agent.chat("My favorite programming language is Python and I have a dog named Max.")
    print(f"Agent: {r2}\n")

    # Conversation 2: test recall
    print("--- Conversation 2: Testing recall ---")
    r3 = await agent.chat("What do you remember about me?")
    print(f"Agent: {r3}\n")

    # Show what's stored in the graph
    print("--- Memory graph contents ---")
    entities = await memory.list_entities()
    for entity in entities[:10]:
        print(f"  Entity: {entity}")
        neighbors = await memory.get_neighbors(entity)
        for neighbor in neighbors[:5]:
            print(f"    -> {neighbor}")


if __name__ == "__main__":
    asyncio.run(main())
