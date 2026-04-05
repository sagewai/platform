# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""PostgreSQL-backed context metadata store using asyncpg."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sagewai.context.models import (
    ContextChunk,
    ContextDocument,
    ContextScope,
    ContextSource,
)
from sagewai.context.stores import ContextMetadataStore

logger = logging.getLogger(__name__)


class PostgresContextStore:
    """asyncpg-backed implementation of ``ContextMetadataStore``.

    Usage::

        store = PostgresContextStore(database_url="postgresql://...")
        await store.initialize()
        await store.save_document(doc)
        await store.close()

    Or pass an existing pool::

        store = PostgresContextStore(pool=existing_pool)
    """

    def __init__(
        self,
        database_url: str | None = None,
        pool: Any = None,
    ) -> None:
        self._database_url = database_url
        self._pool = pool

    async def initialize(self) -> None:
        """Create the connection pool if not already provided."""
        if self._pool is not None:
            return
        if self._database_url is None:
            raise ValueError("Either database_url or pool must be provided")
        try:
            import asyncpg
        except ImportError as exc:
            raise ImportError(
                "asyncpg is required for PostgresContextStore. "
                "Install with: uv add asyncpg"
            ) from exc
        self._pool = await asyncpg.create_pool(
            self._database_url, min_size=2, max_size=10
        )

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    async def save_document(self, doc: ContextDocument) -> None:
        await self._pool.execute(
            """
            INSERT INTO context_documents
                (id, scope, scope_id, project_id, title, source, source_uri,
                 mime_type, file_size_bytes, chunk_count, status, confidence,
                 freshness_at, tags, metadata, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, $15::jsonb, $16, $17)
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                chunk_count = EXCLUDED.chunk_count,
                tags = EXCLUDED.tags,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            """,
            doc.id,
            doc.scope.value,
            doc.scope_id,
            doc.project_id,
            doc.title,
            doc.source.value,
            doc.source_uri,
            doc.mime_type,
            doc.file_size_bytes,
            doc.chunk_count,
            doc.status,
            doc.confidence,
            doc.freshness_at,
            doc.tags,
            json.dumps(doc.metadata, default=str),
            doc.created_at,
            doc.updated_at,
        )

    async def get_document(self, doc_id: str) -> ContextDocument | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM context_documents WHERE id = $1", doc_id
        )
        return self._row_to_document(row) if row else None

    _SORTABLE_COLUMNS = {"created_at", "updated_at", "title", "status", "chunk_count", "confidence", "source"}

    def _build_list_query(
        self,
        project_id: str,
        scope: ContextScope | None,
        scope_id: str | None,
        source: ContextSource | None,
        status: str | None,
        search: str | None,
        tags: list[str] | None,
    ) -> tuple[str, list[Any], int]:
        """Build WHERE clause shared by list and count."""
        query = " WHERE project_id = $1"
        params: list[Any] = [project_id]
        idx = 2

        if scope is not None:
            query += f" AND scope = ${idx}"
            params.append(scope.value)
            idx += 1
        if scope_id is not None:
            query += f" AND scope_id = ${idx}"
            params.append(scope_id)
            idx += 1
        if source is not None:
            query += f" AND source = ${idx}"
            params.append(source.value)
            idx += 1
        if status is not None:
            query += f" AND status = ${idx}"
            params.append(status)
            idx += 1
        if search:
            query += f" AND (title ILIKE ${idx} OR source_uri ILIKE ${idx})"
            params.append(f"%{search}%")
            idx += 1
        if tags:
            query += f" AND tags @> ${idx}::text[]"
            params.append(tags)
            idx += 1
        return query, params, idx

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
        where, params, idx = self._build_list_query(
            project_id, scope, scope_id, source, status, search, tags,
        )
        col = sort_by if sort_by in self._SORTABLE_COLUMNS else "created_at"
        direction = "ASC" if sort_order == "asc" else "DESC"
        query = f"SELECT * FROM context_documents{where} ORDER BY {col} {direction}"
        if limit is not None:
            query += f" LIMIT ${idx}"
            params.append(limit)
            idx += 1
        if offset is not None:
            query += f" OFFSET ${idx}"
            params.append(offset)
            idx += 1
        rows = await self._pool.fetch(query, *params)
        return [self._row_to_document(r) for r in rows]

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
        where, params, _idx = self._build_list_query(
            project_id, scope, scope_id, source, status, search, tags,
        )
        row = await self._pool.fetchrow(
            f"SELECT COUNT(*) AS cnt FROM context_documents{where}", *params
        )
        return row["cnt"] if row else 0

    async def update_document(self, doc: ContextDocument) -> None:
        await self._pool.execute(
            """
            UPDATE context_documents SET
                status = $2, chunk_count = $3, confidence = $4,
                freshness_at = $5, tags = $6, metadata = $7::jsonb,
                updated_at = NOW()
            WHERE id = $1
            """,
            doc.id,
            doc.status,
            doc.chunk_count,
            doc.confidence,
            doc.freshness_at,
            doc.tags,
            json.dumps(doc.metadata, default=str),
        )

    async def delete_document(self, doc_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM context_documents WHERE id = $1", doc_id
        )
        return result != "DELETE 0"

    # ------------------------------------------------------------------
    # Chunks
    # ------------------------------------------------------------------

    async def save_chunks(self, chunks: list[ContextChunk]) -> None:
        if not chunks:
            return
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for chunk in chunks:
                    await conn.execute(
                        """
                        INSERT INTO context_chunks
                            (id, document_id, scope, scope_id, project_id, content,
                             chunk_index, token_count, embedding_model, content_hash,
                             importance, access_count, last_accessed_at, metadata,
                             created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                                $11, $12, $13, $14::jsonb, $15)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        chunk.id,
                        chunk.document_id,
                        chunk.scope.value,
                        chunk.scope_id,
                        chunk.project_id,
                        chunk.content,
                        chunk.chunk_index,
                        chunk.token_count,
                        chunk.embedding_model,
                        chunk.content_hash,
                        chunk.importance,
                        chunk.access_count,
                        chunk.last_accessed_at,
                        json.dumps(chunk.metadata, default=str),
                        chunk.created_at,
                    )

    async def get_chunks(self, document_id: str) -> list[ContextChunk]:
        rows = await self._pool.fetch(
            "SELECT * FROM context_chunks WHERE document_id = $1 ORDER BY chunk_index",
            document_id,
        )
        return [self._row_to_chunk(r) for r in rows]

    async def get_chunk(self, chunk_id: str) -> ContextChunk | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM context_chunks WHERE id = $1", chunk_id
        )
        return self._row_to_chunk(row) if row else None

    async def delete_chunks(self, document_id: str) -> int:
        result = await self._pool.execute(
            "DELETE FROM context_chunks WHERE document_id = $1", document_id
        )
        # result is like "DELETE 5"
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def get_existing_hashes(self, project_id: str) -> set[str]:
        rows = await self._pool.fetch(
            "SELECT DISTINCT content_hash FROM context_chunks WHERE project_id = $1",
            project_id,
        )
        return {r["content_hash"] for r in rows}

    async def update_chunk_access(self, chunk_id: str) -> None:
        await self._pool.execute(
            """
            UPDATE context_chunks SET
                access_count = access_count + 1,
                last_accessed_at = NOW(),
                importance = LEAST(1.0, importance + 0.05)
            WHERE id = $1
            """,
            chunk_id,
        )

    async def update_chunk(self, chunk: ContextChunk) -> None:
        await self._pool.execute(
            """
            UPDATE context_chunks SET
                content = $2, importance = $3, access_count = $4,
                last_accessed_at = $5, metadata = $6::jsonb
            WHERE id = $1
            """,
            chunk.id,
            chunk.content,
            chunk.importance,
            chunk.access_count,
            chunk.last_accessed_at,
            json.dumps(chunk.metadata, default=str),
        )

    async def delete_chunk(self, chunk_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM context_chunks WHERE id = $1", chunk_id
        )
        return result != "DELETE 0"

    # ------------------------------------------------------------------
    # Row converters
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_document(row: Any) -> ContextDocument:
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        return ContextDocument(
            id=row["id"],
            scope=ContextScope(row["scope"]),
            scope_id=row["scope_id"],
            project_id=row["project_id"],
            title=row["title"],
            source=ContextSource(row["source"]),
            source_uri=row.get("source_uri"),
            mime_type=row["mime_type"],
            file_size_bytes=row["file_size_bytes"],
            chunk_count=row["chunk_count"],
            status=row["status"],
            confidence=row["confidence"],
            freshness_at=row["freshness_at"],
            tags=list(row.get("tags") or []),
            metadata=meta or {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_chunk(row: Any) -> ContextChunk:
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        return ContextChunk(
            id=row["id"],
            document_id=row["document_id"],
            scope=ContextScope(row["scope"]),
            scope_id=row["scope_id"],
            project_id=row["project_id"],
            content=row["content"],
            chunk_index=row["chunk_index"],
            token_count=row["token_count"],
            embedding_model=row["embedding_model"],
            content_hash=row["content_hash"],
            importance=row["importance"],
            access_count=row["access_count"],
            last_accessed_at=row.get("last_accessed_at"),
            metadata=meta or {},
            created_at=row["created_at"],
        )
