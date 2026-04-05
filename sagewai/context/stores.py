# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""In-memory context stores for development and testing."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from sagewai.context.models import (
    ContextChunk,
    ContextDocument,
    ContextScope,
    ContextSearchResult,
    ContextSource,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Store protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class ContextMetadataStore(Protocol):
    """Protocol for document and chunk metadata storage (Postgres or in-memory)."""

    async def save_document(self, doc: ContextDocument) -> None: ...
    async def get_document(self, doc_id: str) -> ContextDocument | None: ...
    async def list_documents(
        self,
        project_id: str,
        scope: ContextScope | None = None,
        scope_id: str | None = None,
        source: ContextSource | None = None,
        status: str | None = None,
        search: str | None = None,
        tags: list[str] | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ContextDocument]: ...

    async def count_documents(
        self,
        project_id: str,
        scope: ContextScope | None = None,
        scope_id: str | None = None,
        source: ContextSource | None = None,
        status: str | None = None,
        search: str | None = None,
        tags: list[str] | None = None,
    ) -> int: ...

    async def update_document(self, doc: ContextDocument) -> None: ...
    async def delete_document(self, doc_id: str) -> bool: ...

    async def save_chunks(self, chunks: list[ContextChunk]) -> None: ...
    async def get_chunks(self, document_id: str) -> list[ContextChunk]: ...
    async def get_chunk(self, chunk_id: str) -> ContextChunk | None: ...
    async def delete_chunks(self, document_id: str) -> int: ...
    async def get_existing_hashes(self, project_id: str) -> set[str]: ...
    async def update_chunk_access(self, chunk_id: str) -> None: ...
    async def update_chunk(self, chunk: ContextChunk) -> None: ...
    async def delete_chunk(self, chunk_id: str) -> bool: ...


@runtime_checkable
class ContextVectorStore(Protocol):
    """Protocol for vector storage and retrieval (Milvus or in-memory)."""

    async def insert(
        self,
        chunk_id: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None: ...

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filters: dict[str, str] | None = None,
    ) -> list[tuple[str, float]]: ...

    async def delete(self, chunk_ids: list[str]) -> None: ...


# ---------------------------------------------------------------------------
# In-memory metadata store
# ---------------------------------------------------------------------------


class InMemoryMetadataStore:
    """In-memory implementation of ContextMetadataStore for dev/test."""

    def __init__(self) -> None:
        self._documents: dict[str, ContextDocument] = {}
        self._chunks: dict[str, ContextChunk] = {}

    async def save_document(self, doc: ContextDocument) -> None:
        self._documents[doc.id] = doc

    async def get_document(self, doc_id: str) -> ContextDocument | None:
        return self._documents.get(doc_id)

    def _filter_documents(
        self,
        project_id: str,
        scope: ContextScope | None = None,
        scope_id: str | None = None,
        source: ContextSource | None = None,
        status: str | None = None,
        search: str | None = None,
        tags: list[str] | None = None,
    ) -> list[ContextDocument]:
        results = [d for d in self._documents.values() if d.project_id == project_id]
        if scope is not None:
            results = [d for d in results if d.scope == scope]
        if scope_id is not None:
            results = [d for d in results if d.scope_id == scope_id]
        if source is not None:
            results = [d for d in results if d.source == source]
        if status is not None:
            results = [d for d in results if d.status == status]
        if search:
            q = search.lower()
            results = [
                d for d in results
                if q in d.title.lower() or (d.source_uri and q in d.source_uri.lower())
            ]
        if tags:
            tag_set = set(tags)
            results = [d for d in results if tag_set.issubset(set(d.tags))]
        return results

    async def list_documents(
        self,
        project_id: str,
        scope: ContextScope | None = None,
        scope_id: str | None = None,
        source: ContextSource | None = None,
        status: str | None = None,
        search: str | None = None,
        tags: list[str] | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ContextDocument]:
        results = self._filter_documents(
            project_id, scope, scope_id, source, status, search, tags,
        )
        reverse = (sort_order or "desc") == "desc"
        key_field = sort_by or "created_at"
        results.sort(key=lambda d: getattr(d, key_field, d.created_at), reverse=reverse)
        if offset:
            results = results[offset:]
        if limit:
            results = results[:limit]
        return results

    async def count_documents(
        self,
        project_id: str,
        scope: ContextScope | None = None,
        scope_id: str | None = None,
        source: ContextSource | None = None,
        status: str | None = None,
        search: str | None = None,
        tags: list[str] | None = None,
    ) -> int:
        return len(self._filter_documents(
            project_id, scope, scope_id, source, status, search, tags,
        ))

    async def update_document(self, doc: ContextDocument) -> None:
        doc.updated_at = datetime.now(timezone.utc)
        self._documents[doc.id] = doc

    async def delete_document(self, doc_id: str) -> bool:
        if doc_id in self._documents:
            del self._documents[doc_id]
            # Also remove chunks
            chunk_ids = [c.id for c in self._chunks.values() if c.document_id == doc_id]
            for cid in chunk_ids:
                del self._chunks[cid]
            return True
        return False

    async def save_chunks(self, chunks: list[ContextChunk]) -> None:
        for chunk in chunks:
            self._chunks[chunk.id] = chunk

    async def get_chunks(self, document_id: str) -> list[ContextChunk]:
        return sorted(
            [c for c in self._chunks.values() if c.document_id == document_id],
            key=lambda c: c.chunk_index,
        )

    async def get_chunk(self, chunk_id: str) -> ContextChunk | None:
        return self._chunks.get(chunk_id)

    async def delete_chunks(self, document_id: str) -> int:
        to_delete = [c.id for c in self._chunks.values() if c.document_id == document_id]
        for cid in to_delete:
            del self._chunks[cid]
        return len(to_delete)

    async def get_existing_hashes(self, project_id: str) -> set[str]:
        return {
            c.content_hash for c in self._chunks.values() if c.project_id == project_id
        }

    async def update_chunk_access(self, chunk_id: str) -> None:
        chunk = self._chunks.get(chunk_id)
        if chunk:
            chunk.access_count += 1
            chunk.last_accessed_at = datetime.now(timezone.utc)
            chunk.importance = min(1.0, chunk.importance + 0.05)

    async def update_chunk(self, chunk: ContextChunk) -> None:
        self._chunks[chunk.id] = chunk

    async def delete_chunk(self, chunk_id: str) -> bool:
        if chunk_id in self._chunks:
            del self._chunks[chunk_id]
            return True
        return False


# ---------------------------------------------------------------------------
# In-memory vector store
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryVectorStore:
    """In-memory vector store using brute-force cosine similarity."""

    def __init__(self) -> None:
        self._vectors: dict[str, tuple[list[float], dict[str, Any]]] = {}

    async def insert(
        self,
        chunk_id: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None:
        self._vectors[chunk_id] = (vector, metadata)

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filters: dict[str, str] | None = None,
    ) -> list[tuple[str, float]]:
        candidates: list[tuple[str, float]] = []
        for chunk_id, (vec, meta) in self._vectors.items():
            # Apply filters
            if filters:
                if not all(meta.get(k) == v for k, v in filters.items()):
                    continue
            score = _cosine_similarity(query_vector, vec)
            candidates.append((chunk_id, score))
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:top_k]

    async def delete(self, chunk_ids: list[str]) -> None:
        for cid in chunk_ids:
            self._vectors.pop(cid, None)
