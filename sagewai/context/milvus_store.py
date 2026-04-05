# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Milvus-backed context vector store for production semantic search."""

from __future__ import annotations

import logging
from typing import Any

from sagewai.context.stores import ContextVectorStore

logger = logging.getLogger(__name__)


def _escape_milvus(value: str) -> str:
    """Escape a string for use in Milvus filter expressions."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


class MilvusContextVectorStore:
    """Milvus-backed implementation of ``ContextVectorStore``.

    Uses a dedicated ``context_vectors`` collection with scope-based filtering.

    Usage::

        store = MilvusContextVectorStore(uri="http://localhost:19530")
        await store.initialize()
        await store.insert(chunk_id, vector, metadata)
        results = await store.search(query_vector, top_k=5, filters={...})
    """

    def __init__(
        self,
        uri: str = "http://localhost:19530",
        collection: str = "context_vectors",
        dim: int = 1536,
    ) -> None:
        self.uri = uri
        self.collection_name = collection
        self.dim = dim
        self._client: Any = None

    async def initialize(self) -> None:
        """Connect to Milvus and ensure the collection exists."""
        try:
            from pymilvus import (
                CollectionSchema,
                DataType,
                FieldSchema,
                MilvusClient,
            )
        except ImportError as exc:
            raise ImportError(
                "pymilvus is required for MilvusContextVectorStore. "
                "Install with: uv add pymilvus"
            ) from exc

        self._client = MilvusClient(uri=self.uri)

        if not self._client.has_collection(self.collection_name):
            schema = CollectionSchema(
                fields=[
                    FieldSchema(
                        "id", DataType.VARCHAR, is_primary=True, max_length=64
                    ),
                    FieldSchema("vector", DataType.FLOAT_VECTOR, dim=self.dim),
                    FieldSchema(
                        "project_id",
                        DataType.VARCHAR,
                        max_length=128,
                        default_value="default",
                    ),
                    FieldSchema(
                        "scope", DataType.VARCHAR, max_length=32, default_value="project"
                    ),
                    FieldSchema(
                        "scope_id",
                        DataType.VARCHAR,
                        max_length=128,
                        default_value="default",
                    ),
                    FieldSchema(
                        "document_id",
                        DataType.VARCHAR,
                        max_length=64,
                        default_value="",
                    ),
                ],
                enable_dynamic_field=True,
            )
            self._client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
            )
            logger.info("Created Milvus collection: %s", self.collection_name)

        # Ensure index exists (handles both new and pre-existing collections)
        indexes = self._client.list_indexes(self.collection_name)
        if not indexes:
            idx_params = self._client.prepare_index_params()
            idx_params.add_index(
                field_name="vector",
                index_type="IVF_FLAT",
                metric_type="COSINE",
                params={"nlist": 128},
            )
            self._client.create_index(
                collection_name=self.collection_name,
                index_params=idx_params,
            )
            logger.info("Created missing index on '%s'", self.collection_name)

        # Ensure the collection is loaded into memory for search
        try:
            self._client.load_collection(self.collection_name)
            logger.info("Milvus collection '%s' loaded", self.collection_name)
        except Exception as exc:
            logger.warning("Failed to load Milvus collection: %s", exc)

    async def insert(
        self,
        chunk_id: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None:
        """Insert a vector with metadata."""
        if self._client is None:
            await self.initialize()

        data = {
            "id": chunk_id,
            "vector": vector,
            "project_id": metadata.get("project_id", "default"),
            "scope": metadata.get("scope", "project"),
            "scope_id": metadata.get("scope_id", "default"),
            "document_id": metadata.get("document_id", ""),
        }
        self._client.insert(
            collection_name=self.collection_name,
            data=[data],
        )

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filters: dict[str, str] | None = None,
    ) -> list[tuple[str, float]]:
        """Search for similar vectors with optional scope/project filtering."""
        if self._client is None:
            await self.initialize()

        # Build filter expression
        filter_expr = ""
        if filters:
            parts = []
            for key, value in filters.items():
                parts.append(f'{key} == "{_escape_milvus(value)}"')
            filter_expr = " and ".join(parts)

        results = self._client.search(
            collection_name=self.collection_name,
            data=[query_vector],
            limit=top_k,
            output_fields=["id"],
            filter=filter_expr if filter_expr else None,
            search_params={"metric_type": "COSINE", "params": {"nprobe": 10}},
        )

        hits: list[tuple[str, float]] = []
        if results and len(results) > 0:
            for hit in results[0]:
                hits.append((hit["id"], hit["distance"]))
        return hits

    async def delete(self, chunk_ids: list[str]) -> None:
        """Delete vectors by chunk IDs."""
        if self._client is None or not chunk_ids:
            return

        # Milvus delete by filter
        id_list = ", ".join(f'"{_escape_milvus(cid)}"' for cid in chunk_ids)
        self._client.delete(
            collection_name=self.collection_name,
            filter=f"id in [{id_list}]",
        )

    async def close(self) -> None:
        """Close the Milvus client."""
        if self._client is not None:
            self._client.close()
            self._client = None
