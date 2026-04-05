# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Milvus-backed vector memory for semantic retrieval."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from sagewai.core.context import resolve_project_id

if TYPE_CHECKING:
    from sagewai.intelligence.embeddings.protocol import Embedder

logger = logging.getLogger(__name__)


def _escape_milvus(value: str) -> str:
    """Escape a string for Milvus boolean filter expressions."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


try:
    from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient
except ImportError:
    MilvusClient = None  # type: ignore[assignment,misc]
    CollectionSchema = None  # type: ignore[assignment,misc]
    DataType = None  # type: ignore[assignment,misc]
    FieldSchema = None  # type: ignore[assignment,misc]


async def _embed(text: str, model: str) -> list[float]:
    """Generate an embedding vector using litellm."""
    import litellm

    response = await litellm.aembedding(model=model, input=[text])
    return response.data[0]["embedding"]


class MilvusVectorMemory:
    """Vector memory backed by Milvus for production-grade semantic search.

    Satisfies the ``MemoryProvider`` protocol so it can be passed to any
    BaseAgent's ``memory`` parameter or to ``RAGEngine(vector=...)``.

    Requires ``pymilvus`` (install via ``uv add sagewai[memory]``).

    Args:
        uri: Milvus server URI.
        collection: Base collection name.
        embedding_model: Model name for litellm embedding calls.
        dim: Embedding vector dimension.
        project_id: Explicit project scope. When ``None``, auto-resolves
            from the active ``ProjectContext`` contextvar, falling back
            to ``"default"``.
    """

    def __init__(
        self,
        *,
        uri: str = "http://localhost:19530",
        collection: str = "agent_memory",
        embedding_model: str = "text-embedding-3-small",
        dim: int = 1536,
        project_id: str | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        if MilvusClient is None:
            raise ImportError(
                "pymilvus is required for MilvusVectorMemory. "
                "Install with: uv add sagewai[memory]"
            )
        self._client = MilvusClient(uri=uri)
        self.collection = collection
        self.embedding_model = embedding_model
        self._embedder = embedder
        # Auto-set dimension from embedder when available
        self.dim = embedder.dimension if embedder is not None else dim
        self._project_id = project_id

    def _resolve_pid(self) -> str:
        """Resolve the effective project_id for this operation."""
        return resolve_project_id(self._project_id)

    def _ensure_collection(self) -> None:
        """Create the collection with vector index if it doesn't exist."""
        if not self._client.has_collection(self.collection):
            schema = CollectionSchema(
                fields=[
                    FieldSchema("id", DataType.VARCHAR, is_primary=True, max_length=64),
                    FieldSchema("vector", DataType.FLOAT_VECTOR, dim=self.dim),
                    FieldSchema(
                        "project_id", DataType.VARCHAR, max_length=128, default_value="default"
                    ),
                ],
                enable_dynamic_field=True,
            )
            index_params = self._client.prepare_index_params()
            index_params.add_index(field_name="vector", metric_type="COSINE")
            self._client.create_collection(
                collection_name=self.collection,
                schema=schema,
                index_params=index_params,
                consistency_level="Strong",
            )

    async def _do_embed(self, text: str) -> list[float]:
        """Embed a single text using the configured embedder or legacy path."""
        if self._embedder is not None:
            return await self._embedder.embed_query(text)
        return await _embed(text, self.embedding_model)

    def _sync_insert(
        self,
        doc_id: str,
        vector: list[float],
        content: str,
        metadata: dict[str, Any] | None,
        pid: str,
    ) -> None:
        """Synchronous insert into Milvus."""
        self._ensure_collection()
        self._client.insert(
            collection_name=self.collection,
            data=[{
                "id": doc_id,
                "vector": vector,
                "content": content,
                "metadata": json.dumps(metadata or {}),
                "project_id": pid,
            }],
        )

    async def store(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        """Store content with its embedding vector.

        Returns:
            The document ID.
        """
        doc_id = str(uuid.uuid4())
        vector = await self._do_embed(content)
        pid = self._resolve_pid()
        await asyncio.to_thread(self._sync_insert, doc_id, vector, content, metadata, pid)
        return doc_id

    def _sync_retrieve(self, vector: list[float], pid: str, top_k: int) -> list[str]:
        """Synchronous search in Milvus."""
        if not self._client.has_collection(self.collection):
            return []
        results = self._client.search(
            collection_name=self.collection,
            data=[vector],
            output_fields=["content", "metadata"],
            filter=f'project_id == "{_escape_milvus(pid)}"',
            limit=top_k,
        )
        return [hit["entity"]["content"] for hit in results[0]] if results else []

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        """Retrieve content most similar to the query.

        Results are scoped to the current project.

        Returns:
            List of content strings ordered by relevance.
        """
        vector = await self._do_embed(query)
        pid = self._resolve_pid()
        return await asyncio.to_thread(self._sync_retrieve, vector, pid, top_k)

    def _sync_delete(self, doc_id: str, pid: str) -> bool:
        """Synchronous delete from Milvus."""
        if not self._client.has_collection(self.collection):
            return False
        self._client.delete(
            collection_name=self.collection,
            filter=f'id == "{_escape_milvus(doc_id)}" and project_id == "{_escape_milvus(pid)}"',
        )
        return True

    async def delete(self, doc_id: str) -> bool:
        """Delete a document by ID (scoped to current project)."""
        pid = self._resolve_pid()
        return await asyncio.to_thread(self._sync_delete, doc_id, pid)

    def _sync_clear(self) -> None:
        """Synchronous clear of the collection."""
        if self._client.has_collection(self.collection):
            self._client.drop_collection(self.collection)

    async def clear(self) -> None:
        """Drop and recreate the collection."""
        await asyncio.to_thread(self._sync_clear)
