# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Memory subsystem: Vector (Milvus) and Graph (NebulaGraph) stores.

Defines the ``MemoryProvider`` protocol that all memory backends must implement.
When assigned to an agent, the provider is queried before each LLM call to
inject relevant context into the conversation.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MemoryProvider(Protocol):
    """Protocol for memory backends.

    Implementations must provide at minimum:
    - ``retrieve(query)`` — return relevant context strings
    - ``store(content, metadata)`` — persist new information
    """

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        """Retrieve relevant context for a query.

        Args:
            query: The search query (typically the user message).
            top_k: Maximum number of results to return.

        Returns:
            List of relevant context strings.
        """
        ...

    async def store(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        """Store content in memory.

        Args:
            content: The text content to store.
            metadata: Optional metadata (source, timestamp, agent, etc.).
        """
        ...


from sagewai.memory.branch import MemoryBranch  # noqa: E402
from sagewai.memory.global_memory import GlobalMemory  # noqa: E402
from sagewai.memory.graph import GraphMemory  # noqa: E402
from sagewai.memory.strategies import (  # noqa: E402
    ExtractedRecord,
    MemoryStrategy,
    TurnEvent,
)
from sagewai.memory.strategies.preference import PreferenceStrategy  # noqa: E402
from sagewai.memory.strategies.semantic import SemanticFactStrategy  # noqa: E402
from sagewai.memory.strategies.summary import SummaryStrategy  # noqa: E402

try:
    from sagewai.memory.milvus import MilvusVectorMemory  # noqa: E402
except ImportError:
    MilvusVectorMemory = None  # type: ignore[assignment,misc]
try:
    from sagewai.memory.nebula import NebulaGraphMemory  # noqa: E402
except ImportError:
    NebulaGraphMemory = None  # type: ignore[assignment,misc]
from sagewai.memory.query_router import QueryIntent, QueryRouter  # noqa: E402
from sagewai.memory.rag import RAGEngine, RetrievalStrategy  # noqa: E402
from sagewai.memory.sqlite_vec import SqliteVecMemory  # noqa: E402
from sagewai.memory.vector import VectorMemory  # noqa: E402

__all__ = [
    "ExtractedRecord",
    "GlobalMemory",
    "GraphMemory",
    "MemoryBranch",
    "MemoryProvider",
    "MemoryStrategy",
    "MilvusVectorMemory",
    "NebulaGraphMemory",
    "PreferenceStrategy",
    "QueryIntent",
    "QueryRouter",
    "RAGEngine",
    "RetrievalStrategy",
    "SemanticFactStrategy",
    "SqliteVecMemory",
    "SummaryStrategy",
    "TurnEvent",
    "VectorMemory",
]
