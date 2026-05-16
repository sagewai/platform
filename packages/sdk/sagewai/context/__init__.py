# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Context Engine — universal ingestion, scoped memory, adaptive lifecycle.

The ``context`` module is the high-level orchestration layer for agent memory.
It builds on the low-level ``memory`` primitives (VectorMemory, GraphMemory,
RAGEngine) and adds document ingestion, scoped access, deduplication, and
lifecycle management.

``ContextEngine`` implements the ``MemoryProvider`` protocol, so it can be
passed directly to ``BaseAgent(memory=engine)`` with zero agent-side changes.
"""

from sagewai.context.access import AccessDeniedError, check_read_access, check_write_access
from sagewai.context.chunking import ChunkManager
from sagewai.context.conflict_resolver import ConflictDetector
from sagewai.context.dedup import Deduplicator
from sagewai.context.engine import ContextEngine
from sagewai.context.episodes import Episode, EpisodeStore, PersistentEpisodeStore
from sagewai.context.ingestion import IngestionPipeline
from sagewai.context.memory_bridge import MemoryBridge
from sagewai.context.tools import create_memory_tools
from sagewai.context.models import (
    ChunkingConfig,
    ChunkText,
    CodeEntity,
    ContextChunk,
    ContextDocument,
    ContextScope,
    ContextSearchResult,
    ContextSource,
    ParsedCode,
    ParsedDocument,
)
from sagewai.context.lifecycle import ConflictPair, LifecycleConfig, LifecycleManager, LifecycleReport
from sagewai.context.stores import (
    ContextMetadataStore,
    ContextVectorStore,
    InMemoryMetadataStore,
    InMemoryVectorStore,
)

# Optional production stores — require asyncpg / pymilvus
try:
    from sagewai.context.milvus_store import MilvusContextVectorStore
except ImportError:
    MilvusContextVectorStore = None  # type: ignore[assignment,misc]
try:
    from sagewai.context.pg_store import PostgresContextStore
except ImportError:
    PostgresContextStore = None  # type: ignore[assignment,misc]

__all__ = [
    "AccessDeniedError",
    "check_read_access",
    "check_write_access",
    "ChunkingConfig",
    "ChunkManager",
    "ChunkText",
    "CodeEntity",
    "ContextChunk",
    "ContextDocument",
    "ContextEngine",
    "ContextMetadataStore",
    "ContextScope",
    "ContextSearchResult",
    "ContextSource",
    "ContextVectorStore",
    "Deduplicator",
    "InMemoryMetadataStore",
    "InMemoryVectorStore",
    "IngestionPipeline",
    "LifecycleConfig",
    "LifecycleManager",
    "LifecycleReport",
    "ConflictDetector",
    "ConflictPair",
    "MemoryBridge",
    "MilvusContextVectorStore",
    "ParsedCode",
    "ParsedDocument",
    "PostgresContextStore",
    "create_memory_tools",
    "Episode",
    "EpisodeStore",
    "PersistentEpisodeStore",
]
