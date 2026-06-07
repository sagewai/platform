# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Context metadata store backed by SQLAlchemy Core — works on both SQLite and PostgreSQL.

One implementation replaces the former asyncpg-only PostgresContextStore.
The class name and all public method signatures are unchanged so callers
require no modification.

Tags containment (``tags @> $::text[]`` in the old asyncpg store)
------------------------------------------------------------------
* PostgreSQL: ``ARRAY.contains()`` renders as the ``@>`` containment
  operator natively supported by TEXT[] columns.
* SQLite: tags are stored as a JSON list (``ArrayText``).  There is no
  native array-containment operator in SQLite, so we fetch all candidate
  rows filtered by the other predicates and apply the subset check in
  Python — matching the semantics of ``InMemoryMetadataStore._filter_documents``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import ARRAY, Text, cast, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import func as sa_func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.context.models import (
    ContextChunk,
    ContextDocument,
    ContextScope,
    ContextSource,
)
from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.engine import create_engine
from sagewai.db.models import Base, ContextChunkModel, ContextDocumentModel

logger = logging.getLogger(__name__)

_DOC_TBL = ContextDocumentModel.__table__
_CHUNK_TBL = ContextChunkModel.__table__

_SORTABLE_COLUMNS = {
    "created_at", "updated_at", "title", "status", "chunk_count", "confidence", "source",
}


class PostgresContextStore:
    """Context metadata store using SQLAlchemy Core — SQLite (default) or PostgreSQL.

    Parameters
    ----------
    engine:
        Pre-built :class:`AsyncEngine`.  When supplied, *database_url* and
        *pool* are ignored.
    database_url:
        Connection string passed to :func:`sagewai.db.engine.create_engine`.
        Ignored when *engine* is supplied.
    pool:
        Accepted for backwards-compatibility with callers that previously
        passed an asyncpg pool.  It is **not used** by this implementation;
        the SQLAlchemy engine manages its own connection pool.
    """

    def __init__(
        self,
        database_url: str | None = None,
        pool: Any = None,  # kept for API back-compat; not used
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        if engine is not None:
            self._engine: AsyncEngine = engine
        elif database_url is not None:
            self._engine = create_engine(database_url)
        else:
            self._engine = factory.get_engine()
        # pool is intentionally ignored; SQLAlchemy engine owns connection pooling

    async def initialize(self) -> None:
        """Bootstrap the schema when using SQLite; no-op on PostgreSQL (Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Dispose the engine's connection pool."""
        await self._engine.dispose()

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    async def save_document(self, doc: ContextDocument) -> None:
        """Upsert a document — ON CONFLICT (id) DO UPDATE."""
        now = datetime.now(timezone.utc)
        values = {
            "id": doc.id,
            "scope": doc.scope.value,
            "scope_id": doc.scope_id,
            "project_id": doc.project_id,
            "title": doc.title,
            "source": doc.source.value,
            "source_uri": doc.source_uri,
            "mime_type": doc.mime_type,
            "file_size_bytes": doc.file_size_bytes,
            "chunk_count": doc.chunk_count,
            "status": doc.status,
            "confidence": doc.confidence,
            "freshness_at": doc.freshness_at,
            "metadata": doc.metadata,
            "tags": doc.tags,
            "created_at": doc.created_at,
            "updated_at": now,
        }
        stmt = upsert(
            _DOC_TBL,
            values,
            index_elements=["id"],
            set_={
                "status": doc.status,
                "chunk_count": doc.chunk_count,
                "tags": doc.tags,
                "metadata": doc.metadata,
                "updated_at": now,
            },
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get_document(self, doc_id: str) -> ContextDocument | None:
        stmt = select(_DOC_TBL).where(_DOC_TBL.c.id == doc_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        return _row_to_document(row) if row else None

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
        stmt, needs_python_tag_filter = _build_list_stmt(
            project_id, scope, scope_id, source, status, search, tags,
            sort_by, sort_order, limit, offset,
            dialect=self._engine.dialect.name,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        docs = [_row_to_document(r) for r in rows]
        if needs_python_tag_filter and tags:
            tag_set = set(tags)
            docs = [d for d in docs if tag_set.issubset(set(d.tags))]
        return docs

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
        dialect = self._engine.dialect.name
        # On SQLite, tags containment cannot be expressed in SQL.  Fall back to
        # the full-fetch path only when a tags filter is actually requested.
        if tags and dialect == "sqlite":
            docs = await self.list_documents(
                project_id, scope, scope_id, source, status, search, tags,
            )
            return len(docs)

        # All other cases: use a SQL COUNT(*) — includes Postgres with tags.
        clauses = _build_filter_clauses(
            project_id, scope, scope_id, source, status, search, tags, dialect=dialect,
        )
        stmt = (
            select(sa_func.count())
            .select_from(_DOC_TBL)
            .where(*clauses)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            return result.scalar_one()

    async def update_document(self, doc: ContextDocument) -> None:
        now = datetime.now(timezone.utc)
        stmt = (
            _DOC_TBL.update()
            .where(_DOC_TBL.c.id == doc.id)
            .values(
                status=doc.status,
                chunk_count=doc.chunk_count,
                confidence=doc.confidence,
                freshness_at=doc.freshness_at,
                tags=doc.tags,
                metadata=doc.metadata,
                updated_at=now,
            )
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def delete_document(self, doc_id: str) -> bool:
        stmt = sa_delete(_DOC_TBL).where(_DOC_TBL.c.id == doc_id)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount > 0

    # ------------------------------------------------------------------
    # Chunks
    # ------------------------------------------------------------------

    async def save_chunks(self, chunks: list[ContextChunk]) -> None:
        if not chunks:
            return
        dialect = self._engine.dialect.name
        make_insert = pg_insert if dialect == "postgresql" else sqlite_insert
        async with self._engine.begin() as conn:
            for chunk in chunks:
                values = {
                    "id": chunk.id,
                    "document_id": chunk.document_id,
                    "scope": chunk.scope.value,
                    "scope_id": chunk.scope_id,
                    "project_id": chunk.project_id,
                    "content": chunk.content,
                    "chunk_index": chunk.chunk_index,
                    "token_count": chunk.token_count,
                    "embedding_model": chunk.embedding_model,
                    "content_hash": chunk.content_hash,
                    "importance": chunk.importance,
                    "access_count": chunk.access_count,
                    "last_accessed_at": chunk.last_accessed_at,
                    "metadata": chunk.metadata,
                    "created_at": chunk.created_at,
                }
                # ON CONFLICT (id) DO NOTHING — matches old store's behaviour
                stmt = make_insert(_CHUNK_TBL).values(**values).on_conflict_do_nothing(
                    index_elements=["id"]
                )
                await conn.execute(stmt)

    async def get_chunks(self, document_id: str) -> list[ContextChunk]:
        stmt = (
            select(_CHUNK_TBL)
            .where(_CHUNK_TBL.c.document_id == document_id)
            .order_by(_CHUNK_TBL.c.chunk_index)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [_row_to_chunk(r) for r in rows]

    async def get_chunk(self, chunk_id: str) -> ContextChunk | None:
        stmt = select(_CHUNK_TBL).where(_CHUNK_TBL.c.id == chunk_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        return _row_to_chunk(row) if row else None

    async def delete_chunks(self, document_id: str) -> int:
        stmt = sa_delete(_CHUNK_TBL).where(_CHUNK_TBL.c.document_id == document_id)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount

    async def get_existing_hashes(self, project_id: str) -> set[str]:
        stmt = (
            select(_CHUNK_TBL.c.content_hash)
            .where(_CHUNK_TBL.c.project_id == project_id)
            .distinct()
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.all()
        return {r[0] for r in rows}

    async def update_chunk_access(self, chunk_id: str) -> None:
        now = datetime.now(timezone.utc)
        if self._engine.dialect.name == "postgresql":
            # PostgreSQL: LEAST() is available — single round-trip
            stmt = (
                _CHUNK_TBL.update()
                .where(_CHUNK_TBL.c.id == chunk_id)
                .values(
                    access_count=_CHUNK_TBL.c.access_count + 1,
                    last_accessed_at=now,
                    importance=sa_func.least(
                        1.0,
                        _CHUNK_TBL.c.importance + 0.05,
                    ),
                )
            )
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
        else:
            # SQLite: no LEAST() — fetch then write
            chunk = await self.get_chunk(chunk_id)
            if chunk is None:
                return
            new_importance = min(1.0, chunk.importance + 0.05)
            stmt = (
                _CHUNK_TBL.update()
                .where(_CHUNK_TBL.c.id == chunk_id)
                .values(
                    access_count=chunk.access_count + 1,
                    last_accessed_at=now,
                    importance=new_importance,
                )
            )
            async with self._engine.begin() as conn:
                await conn.execute(stmt)

    async def update_chunk(self, chunk: ContextChunk) -> None:
        stmt = (
            _CHUNK_TBL.update()
            .where(_CHUNK_TBL.c.id == chunk.id)
            .values(
                content=chunk.content,
                importance=chunk.importance,
                access_count=chunk.access_count,
                last_accessed_at=chunk.last_accessed_at,
                metadata=chunk.metadata,
            )
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def delete_chunk(self, chunk_id: str) -> bool:
        stmt = sa_delete(_CHUNK_TBL).where(_CHUNK_TBL.c.id == chunk_id)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------


def _build_filter_clauses(
    project_id: str,
    scope: ContextScope | None,
    scope_id: str | None,
    source: ContextSource | None,
    status: str | None,
    search: str | None,
    tags: list[str] | None,
    *,
    dialect: str,
) -> list[Any]:
    """Return the WHERE clauses shared by list_documents and count_documents.

    Tags containment
    ----------------
    * PostgreSQL: the ``@>`` containment operator is added as a SQL clause.
    * SQLite: no native array containment — the clause is **omitted** and the
      caller is responsible for Python-side post-filtering when ``tags`` is
      provided.  ``_build_list_stmt`` does this via ``needs_python_tag_filter``;
      ``count_documents`` falls back to a full-fetch before calling this helper.
    """
    tbl = _DOC_TBL
    clauses: list[Any] = [tbl.c.project_id == project_id]

    if scope is not None:
        clauses.append(tbl.c.scope == scope.value)
    if scope_id is not None:
        clauses.append(tbl.c.scope_id == scope_id)
    if source is not None:
        clauses.append(tbl.c.source == source.value)
    if status is not None:
        clauses.append(tbl.c.status == status)
    if search:
        like = f"%{search}%"
        clauses.append(tbl.c.title.ilike(like) | tbl.c.source_uri.ilike(like))
    if tags and dialect == "postgresql":
        # Use the Postgres @> (contains) operator on TEXT[].
        # tbl.c.tags is typed as ArrayText = JSON().with_variant(ARRAY(Text()), "postgresql"),
        # so .op("@>")(cast(tags, ARRAY(Text()))) renders as:
        #   context_documents.tags @> ARRAY[$1, $2, ...]::TEXT[]
        clauses.append(tbl.c.tags.op("@>")(cast(tags, ARRAY(Text()))))
    # SQLite: tags containment omitted from SQL — handled in Python by callers.

    return clauses


def _build_list_stmt(
    project_id: str,
    scope: ContextScope | None,
    scope_id: str | None,
    source: ContextSource | None,
    status: str | None,
    search: str | None,
    tags: list[str] | None,
    sort_by: str | None,
    sort_order: str | None,
    limit: int | None,
    offset: int | None,
    dialect: str,
) -> tuple[Any, bool]:
    """Build a SELECT statement for list_documents.

    Returns ``(stmt, needs_python_tag_filter)``.

    On PostgreSQL the tags containment filter is pushed to SQL via
    :func:`_build_filter_clauses`.  On SQLite we cannot express array
    containment in SQL, so we omit the tag clause and return
    ``needs_python_tag_filter=True``; the caller applies the subset check in
    Python after fetching rows.
    """
    filters = _build_filter_clauses(
        project_id, scope, scope_id, source, status, search, tags, dialect=dialect,
    )
    needs_python_tag_filter = bool(tags and dialect != "postgresql")

    col_name = sort_by if sort_by in _SORTABLE_COLUMNS else "created_at"
    col = _DOC_TBL.c[col_name]
    direction = col.asc() if sort_order == "asc" else col.desc()

    stmt = select(_DOC_TBL).where(*filters).order_by(direction)
    if limit is not None:
        stmt = stmt.limit(limit)
    if offset is not None:
        stmt = stmt.offset(offset)

    return stmt, needs_python_tag_filter


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def _row_to_document(row: Any) -> ContextDocument:
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)

    tags = row["tags"]
    if isinstance(tags, str):
        tags = json.loads(tags)

    return ContextDocument(
        id=row["id"],
        scope=ContextScope(row["scope"]),
        scope_id=row["scope_id"],
        project_id=row["project_id"],
        title=row["title"],
        source=ContextSource(row["source"]),
        source_uri=row["source_uri"],
        mime_type=row["mime_type"],
        file_size_bytes=row["file_size_bytes"],
        chunk_count=row["chunk_count"],
        status=row["status"],
        confidence=row["confidence"],
        freshness_at=_ensure_aware(row["freshness_at"]),
        tags=list(tags or []),
        metadata=meta or {},
        created_at=_ensure_aware(row["created_at"]),
        updated_at=_ensure_aware(row["updated_at"]),
    )


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
        last_accessed_at=(
            _ensure_aware(row["last_accessed_at"])
            if row["last_accessed_at"] is not None
            else None
        ),
        metadata=meta or {},
        created_at=_ensure_aware(row["created_at"]),
    )


def _ensure_aware(dt: datetime | None) -> datetime:
    """Normalise a possibly-naive datetime to UTC-aware.

    SQLite returns naive datetimes from timezone-aware columns; PostgreSQL
    returns tz-aware values.  We normalise to UTC-aware in both cases.
    """
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
