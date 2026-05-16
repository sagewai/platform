# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Example 29 — memory strategies + branching.

Demonstrates per-mission memory branches and the three built-in extractors
(semantic, preference, summary). Run twice; on the second run the agent should
recall preferences from the first.

Usage::

    uv run python -m sagewai.examples.29_memory_strategies
"""
from __future__ import annotations

import asyncio

from sagewai.memory import (
    MemoryBranch,
    PreferenceStrategy,
    SemanticFactStrategy,
    SummaryStrategy,
    TurnEvent,
)
from sagewai.memory.rag import RAGEngine, RetrievalStrategy
from sagewai.memory.vector import VectorMemory


async def main() -> None:
    vec = VectorMemory()
    rag = RAGEngine(
        vector=vec,
        strategy=RetrievalStrategy.VECTOR_ONLY,
        strategies=[
            SemanticFactStrategy(llm=...),  # plug in your LLM client
            PreferenceStrategy(llm=...),
            SummaryStrategy(llm=...),
        ],
        branch=MemoryBranch(mission_id="demo-mission"),
    )

    turns = [
        TurnEvent(role="user", content="I'm Arda, I work in Berlin. Keep replies short.", session_id="s1"),
        TurnEvent(role="assistant", content="Got it.", session_id="s1"),
    ]
    await rag.ingest_turns(turns)
    hits = await rag.retrieve("demo-mission/preferences")
    print("Preferences seen:", hits)


if __name__ == "__main__":
    asyncio.run(main())
