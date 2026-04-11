# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Self-editing memory tools for agents.

Gives agents the ability to manage their own memory during conversations:
``memory_store``, ``memory_search``, ``memory_forget``, ``memory_update``.

Inspired by Letta/MemGPT's self-editing memory paradigm — agents actively
curate their knowledge rather than passively reading from a store.

Usage::

    engine = ContextEngine(metadata_store=..., vector_store=..., agent_name="analyst")
    agent = UniversalAgent(
        name="analyst",
        model="gpt-4o",
        memory=engine,
        tools=engine.get_tools(),
    )
"""

from __future__ import annotations

import logging
from typing import Any

from sagewai.context.models import ContextScope, ContextSource
from sagewai.models.tool import tool

logger = logging.getLogger(__name__)

_IMPORTANCE_MAP = {"low": 0.3, "medium": 0.5, "high": 0.8, "critical": 1.0}


def create_memory_tools(engine: Any) -> list:
    """Create agent-callable memory tools bound to a ContextEngine instance.

    Parameters
    ----------
    engine:
        A ``ContextEngine`` instance that these tools will read from and write to.

    Returns
    -------
    list
        Four ``@tool``-decorated async functions: memory_store, memory_search,
        memory_forget, memory_update.
    """

    # Define raw async functions first, then wrap with @tool.
    # memory_update calls the raw functions directly to avoid coupling
    # with @tool decorator internals.

    async def _store_impl(
        content: str,
        title: str = "",
        importance: str = "medium",
    ) -> str:
        imp = _IMPORTANCE_MAP.get(importance, 0.5)
        scope = ContextScope.PROJECT
        scope_id = engine.project_id

        doc = await engine.ingest_text(
            text=content,
            title=title or content[:60],
            scope=scope,
            scope_id=scope_id,
            source=ContextSource.CONVERSATION,
            metadata={"importance_hint": imp, "agent_stored": True},
        )
        logger.info("Agent memory_store: '%s' → doc %s", content[:60], doc.id)
        return f"Stored in memory: {content[:100]}"

    async def _forget_impl(query: str) -> str:
        results = await engine.search(query, top_k=1)
        if not results:
            return "No matching memory found to forget."

        chunk = await engine.metadata_store.get_chunk(results[0].chunk_id)
        if chunk:
            chunk.importance = 0.0
            await engine.metadata_store.update_chunk(chunk)
            logger.info("Agent memory_forget: chunk %s marked forgotten", chunk.id)
            return f"Forgotten: {results[0].content[:100]}"
        return "Memory not found."

    @tool
    async def memory_store(
        content: str,
        title: str = "",
        importance: str = "medium",
    ) -> str:
        """Store a fact, decision, or preference in long-term memory.

        Use this when you learn something worth remembering for future
        conversations — user preferences, key decisions, important facts.

        Args:
            content: The fact or information to remember.
            title: Short label for this memory (optional).
            importance: How important is this? One of: low, medium, high, critical.
        """
        return await _store_impl(content, title, importance)

    @tool
    async def memory_search(query: str, limit: int = 5) -> str:
        """Search your memory for relevant information.

        Use this to recall facts, decisions, or context from past conversations.

        Args:
            query: What to search for in memory.
            limit: Maximum number of results (default 5).
        """
        results = await engine.search(query, top_k=min(limit, 10))
        if not results:
            return "No relevant memories found."

        lines = []
        for r in results:
            scope_label = f"{r.scope.value}/{r.scope_id}" if r.scope_id else r.scope.value
            lines.append(f"[{scope_label}] {r.content}")
        return "\n\n".join(lines)

    @tool
    async def memory_forget(query: str) -> str:
        """Mark a memory as forgotten — it will no longer appear in searches.

        Use this when stored information is no longer accurate or relevant.

        Args:
            query: Description of the memory to forget.
        """
        return await _forget_impl(query)

    @tool
    async def memory_update(old_fact: str, new_fact: str) -> str:
        """Update a previously stored fact with new information.

        Use this when a fact has changed — e.g., a user's preference changed
        or a decision was revised.

        Args:
            old_fact: The outdated information to replace.
            new_fact: The updated information.
        """
        forget_result = await _forget_impl(old_fact)
        store_result = await _store_impl(new_fact, importance="high")
        return f"Updated memory.\nOld: {forget_result}\nNew: {store_result}"

    return [memory_store, memory_search, memory_forget, memory_update]
