# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""RAG engine — combines vector and graph memory for context retrieval.

Orchestrates VectorMemory and GraphMemory with configurable retrieval
strategies: vector-only, graph-only, or hybrid (default).

Usage::

    from sagewai.memory.rag import RAGEngine, RetrievalStrategy
    from sagewai.memory.vector import VectorMemory
    from sagewai.memory.graph import GraphMemory

    rag = RAGEngine(
        vector=VectorMemory(),
        graph=GraphMemory(),
        strategy=RetrievalStrategy.HYBRID,
    )

    # Use as memory provider for any agent
    agent = UniversalAgent(name="my-agent", memory=rag)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

from sagewai.memory.graph import GraphMemory
from sagewai.memory.vector import VectorMemory

if TYPE_CHECKING:
    from sagewai.memory.query_router import QueryRouter

logger = logging.getLogger(__name__)


class RetrievalStrategy(str, Enum):
    """Retrieval strategy for the RAG engine."""

    VECTOR_ONLY = "vector_only"
    GRAPH_ONLY = "graph_only"
    HYBRID = "hybrid"
    AUTO = "auto"


class RAGEngine:
    """RAG engine combining vector similarity and graph traversal.

    Implements the MemoryProvider protocol so it can be passed directly
    to any BaseAgent's ``memory`` parameter.

    Args:
        vector: VectorMemory instance for similarity search.
        graph: GraphMemory instance for entity/relation traversal.
        strategy: Retrieval strategy to use. ``AUTO`` classifies each query.
        vector_weight: Weight for vector results in hybrid mode (0-1).
        graph_weight: Weight for graph results in hybrid mode (0-1).
        query_router: Custom QueryRouter for AUTO mode. Auto-created if None.
        project_id: Explicit project scope passed to sub-stores when they are
            auto-created.  ``None`` → sub-stores resolve from contextvar.
    """

    def __init__(
        self,
        *,
        vector: VectorMemory | None = None,
        graph: GraphMemory | None = None,
        strategy: RetrievalStrategy = RetrievalStrategy.HYBRID,
        vector_weight: float = 0.6,
        graph_weight: float = 0.4,
        query_router: QueryRouter | None = None,
        project_id: str | None = None,
    ) -> None:
        self.vector = vector or VectorMemory(project_id=project_id)
        self.graph = graph or GraphMemory(project_id=project_id)
        self.strategy = strategy
        self.vector_weight = vector_weight
        self.graph_weight = graph_weight

        if query_router is not None:
            self._router = query_router
        else:
            from sagewai.memory.query_router import (  # noqa: N814
                QueryRouter as _QueryRouter,
            )

            self._router = _QueryRouter()

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        """Retrieve context using the configured strategy.

        Args:
            query: Search query text.
            top_k: Maximum number of context strings.

        Returns:
            Deduplicated list of context strings.
        """
        strategy = self.strategy

        if strategy == RetrievalStrategy.AUTO:
            strategy = self._router.route(query)
            logger.debug(
                "AUTO routing: query=%r → strategy=%s",
                query[:80],
                strategy.value,
            )

        if strategy == RetrievalStrategy.VECTOR_ONLY:
            return await self.vector.retrieve(query, top_k=top_k)

        if strategy == RetrievalStrategy.GRAPH_ONLY:
            return await self.graph.retrieve(query, top_k=top_k)

        # Hybrid: vector search → graph expansion → deduplicate
        vector_k = max(1, int(top_k * self.vector_weight))
        graph_k = max(1, int(top_k * self.graph_weight))

        vector_results = await self.vector.retrieve(query, top_k=vector_k)
        graph_results = await self.graph.retrieve(query, top_k=graph_k)

        # Merge and deduplicate while preserving order
        seen: set[str] = set()
        merged: list[str] = []
        for item in vector_results + graph_results:
            if item not in seen:
                seen.add(item)
                merged.append(item)

        return merged[:top_k]

    async def store(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        """Store content in both vector and graph stores.

        For vector: stores the content text for similarity search.
        For graph: stores as an entity if metadata includes entity info.

        Args:
            content: Text content to store.
            metadata: Optional metadata. If ``entity`` key is True,
                also stored as a graph entity.
        """
        await self.vector.store(content, metadata=metadata)

        # Auto-create graph entity if metadata indicates it
        if metadata and metadata.get("entity"):
            graph_meta = {k: v for k, v in metadata.items() if k != "entity"}
            await self.graph.store(content, metadata=graph_meta)

    async def store_relation(self, source: str, relation: str, target: str) -> None:
        """Store a relationship in the graph memory.

        Also stores source and target as vector entries for searchability.
        """
        await self.graph.add_relation(source, relation, target)
        # Ensure entities are also searchable via vector
        await self.vector.store(
            f"{source} {relation} {target}",
            metadata={"type": "relation", "source": source, "target": target},
        )

    async def clear(self) -> None:
        """Clear both vector and graph stores."""
        v_result = self.vector.clear()
        g_result = self.graph.clear()
        # Await if the underlying stores return coroutines (e.g. Milvus/Nebula)
        if inspect.isawaitable(v_result):
            await v_result
        if inspect.isawaitable(g_result):
            await g_result
