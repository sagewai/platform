# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for PostgresContextStore — runs against both SQLite and Postgres.

Covers: save_document, get_document, list_documents (incl. tags containment
filter), count_documents, update_document, delete_document, save_chunks,
get_chunks, get_chunk, delete_chunks, get_existing_hashes,
update_chunk_access, update_chunk, delete_chunk.

Uses the dialect_engine fixture from tests/db/conftest.py (SQLite always;
Postgres when SAGEWAI_TEST_DATABASE_URL is set).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest

from sagewai.context.models import (
    ContextChunk,
    ContextDocument,
    ContextScope,
    ContextSource,
)
from sagewai.context.pg_store import PostgresContextStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doc(
    *,
    title: str = "Test Doc",
    tags: list[str] | None = None,
    project_id: str = "proj1",
    status: str = "ready",
    scope: ContextScope = ContextScope.PROJECT,
) -> ContextDocument:
    return ContextDocument(
        id=str(uuid.uuid4()),
        scope=scope,
        scope_id=project_id,
        project_id=project_id,
        title=title,
        source=ContextSource.UPLOAD,
        status=status,
        tags=tags or [],
        metadata={"key": "value", "num": 42},
    )


def _chunk(doc: ContextDocument, *, index: int = 0, content: str = "hello world") -> ContextChunk:
    return ContextChunk(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        scope=doc.scope,
        scope_id=doc.scope_id,
        project_id=doc.project_id,
        content=content,
        chunk_index=index,
        token_count=len(content.split()),
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        metadata={"chunk_meta": "data"},
    )


# ---------------------------------------------------------------------------
# Document operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_save_and_get_document(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc = _doc(title="My Document", tags=["python", "ai"])
    await store.save_document(doc)

    result = await store.get_document(doc.id)
    assert result is not None
    assert result.id == doc.id
    assert result.title == "My Document"
    assert result.tags == ["python", "ai"]
    assert result.metadata == {"key": "value", "num": 42}
    assert result.status == "ready"


@pytest.mark.asyncio
async def test_context_get_document_missing(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    result = await store.get_document("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_context_save_document_upsert(dialect_engine):
    """save_document on same id should update, not raise."""
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc = _doc(title="Original", status="pending")
    await store.save_document(doc)

    doc.title = "Updated"
    doc.status = "ready"
    doc.chunk_count = 3
    await store.save_document(doc)

    result = await store.get_document(doc.id)
    assert result is not None
    assert result.status == "ready"
    assert result.chunk_count == 3


@pytest.mark.asyncio
async def test_context_list_documents_basic(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc1 = _doc(title="Alpha")
    doc2 = _doc(title="Beta")
    await store.save_document(doc1)
    await store.save_document(doc2)

    results = await store.list_documents("proj1")
    assert len(results) == 2
    titles = {r.title for r in results}
    assert titles == {"Alpha", "Beta"}


@pytest.mark.asyncio
async def test_context_list_documents_tags_containment(dialect_engine):
    """Tags containment: doc with [a,b] matches query [a], not query [c]."""
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc_ab = _doc(title="AB Doc", tags=["python", "ai"])
    doc_c = _doc(title="C Doc", tags=["rust"])
    await store.save_document(doc_ab)
    await store.save_document(doc_c)

    # Filter by single tag that is in doc_ab
    matched = await store.list_documents("proj1", tags=["python"])
    assert len(matched) == 1
    assert matched[0].title == "AB Doc"

    # Filter by tag not in any doc
    no_match = await store.list_documents("proj1", tags=["java"])
    assert len(no_match) == 0

    # Both tags present — doc_ab has both
    both = await store.list_documents("proj1", tags=["python", "ai"])
    assert len(both) == 1
    assert both[0].title == "AB Doc"

    # Filter by tag only in doc_c
    c_match = await store.list_documents("proj1", tags=["rust"])
    assert len(c_match) == 1
    assert c_match[0].title == "C Doc"


@pytest.mark.asyncio
async def test_context_list_documents_project_isolation(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc1 = _doc(project_id="proj1", title="P1")
    doc2 = _doc(project_id="proj2", title="P2")
    await store.save_document(doc1)
    await store.save_document(doc2)

    p1_docs = await store.list_documents("proj1")
    p2_docs = await store.list_documents("proj2")
    assert len(p1_docs) == 1 and p1_docs[0].title == "P1"
    assert len(p2_docs) == 1 and p2_docs[0].title == "P2"


@pytest.mark.asyncio
async def test_context_list_documents_status_filter(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc_ready = _doc(title="Ready Doc", status="ready")
    doc_pending = _doc(title="Pending Doc", status="pending")
    await store.save_document(doc_ready)
    await store.save_document(doc_pending)

    ready = await store.list_documents("proj1", status="ready")
    assert len(ready) == 1
    assert ready[0].title == "Ready Doc"


@pytest.mark.asyncio
async def test_context_count_documents(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    await store.save_document(_doc(title="D1", tags=["a"]))
    await store.save_document(_doc(title="D2", tags=["b"]))
    await store.save_document(_doc(title="D3", tags=["a", "b"]))

    total = await store.count_documents("proj1")
    assert total == 3

    tagged_a = await store.count_documents("proj1", tags=["a"])
    assert tagged_a == 2  # D1 and D3

    tagged_ab = await store.count_documents("proj1", tags=["a", "b"])
    assert tagged_ab == 1  # only D3 has both


@pytest.mark.asyncio
async def test_context_update_document(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc = _doc(title="Original", status="pending", tags=["old"])
    await store.save_document(doc)

    doc.status = "ready"
    doc.chunk_count = 5
    doc.tags = ["new"]
    doc.metadata = {"updated": True}
    await store.update_document(doc)

    result = await store.get_document(doc.id)
    assert result is not None
    assert result.status == "ready"
    assert result.chunk_count == 5
    assert result.tags == ["new"]
    assert result.metadata == {"updated": True}


@pytest.mark.asyncio
async def test_context_delete_document(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc = _doc()
    await store.save_document(doc)

    deleted = await store.delete_document(doc.id)
    assert deleted is True

    result = await store.get_document(doc.id)
    assert result is None


@pytest.mark.asyncio
async def test_context_delete_document_missing(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    deleted = await store.delete_document("nonexistent")
    assert deleted is False


# ---------------------------------------------------------------------------
# Chunk operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_save_and_get_chunks(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc = _doc()
    await store.save_document(doc)

    c0 = _chunk(doc, index=0, content="first chunk")
    c1 = _chunk(doc, index=1, content="second chunk")
    await store.save_chunks([c0, c1])

    chunks = await store.get_chunks(doc.id)
    assert len(chunks) == 2
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1
    assert chunks[0].content == "first chunk"


@pytest.mark.asyncio
async def test_context_get_chunk_by_id(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc = _doc()
    await store.save_document(doc)

    c = _chunk(doc, content="specific content")
    await store.save_chunks([c])

    result = await store.get_chunk(c.id)
    assert result is not None
    assert result.id == c.id
    assert result.content == "specific content"
    assert result.metadata == {"chunk_meta": "data"}


@pytest.mark.asyncio
async def test_context_get_chunk_missing(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    result = await store.get_chunk("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_context_delete_chunks(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc = _doc()
    await store.save_document(doc)

    c0 = _chunk(doc, index=0)
    c1 = _chunk(doc, index=1)
    await store.save_chunks([c0, c1])

    deleted = await store.delete_chunks(doc.id)
    assert deleted == 2

    remaining = await store.get_chunks(doc.id)
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_context_get_existing_hashes(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc = _doc()
    await store.save_document(doc)

    c0 = _chunk(doc, content="unique content alpha")
    c1 = _chunk(doc, content="unique content beta")
    await store.save_chunks([c0, c1])

    hashes = await store.get_existing_hashes("proj1")
    assert c0.content_hash in hashes
    assert c1.content_hash in hashes

    # Different project — no hashes
    other_hashes = await store.get_existing_hashes("proj2")
    assert len(other_hashes) == 0


@pytest.mark.asyncio
async def test_context_update_chunk_access(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc = _doc()
    await store.save_document(doc)

    c = _chunk(doc)
    await store.save_chunks([c])

    # access_count starts at 0
    before = await store.get_chunk(c.id)
    assert before is not None
    assert before.access_count == 0

    await store.update_chunk_access(c.id)

    after = await store.get_chunk(c.id)
    assert after is not None
    assert after.access_count == 1
    # importance should have increased
    assert after.importance > before.importance


@pytest.mark.asyncio
async def test_context_update_chunk(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc = _doc()
    await store.save_document(doc)

    c = _chunk(doc, content="original")
    await store.save_chunks([c])

    c.content = "updated content"
    c.importance = 0.9
    c.access_count = 5
    c.metadata = {"updated": True}
    await store.update_chunk(c)

    result = await store.get_chunk(c.id)
    assert result is not None
    assert result.content == "updated content"
    assert result.importance == pytest.approx(0.9, abs=1e-6)
    assert result.access_count == 5
    assert result.metadata == {"updated": True}


@pytest.mark.asyncio
async def test_context_delete_chunk(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc = _doc()
    await store.save_document(doc)

    c = _chunk(doc)
    await store.save_chunks([c])

    deleted = await store.delete_chunk(c.id)
    assert deleted is True

    result = await store.get_chunk(c.id)
    assert result is None


@pytest.mark.asyncio
async def test_context_delete_chunk_missing(dialect_engine):
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    deleted = await store.delete_chunk("nonexistent")
    assert deleted is False


@pytest.mark.asyncio
async def test_context_save_chunks_idempotent(dialect_engine):
    """save_chunks with same id twice must not error (ON CONFLICT DO NOTHING)."""
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc = _doc()
    await store.save_document(doc)

    c = _chunk(doc, content="same chunk")
    await store.save_chunks([c])
    await store.save_chunks([c])  # must not raise

    chunks = await store.get_chunks(doc.id)
    assert len(chunks) == 1


@pytest.mark.asyncio
async def test_context_chunks_query_by_document(dialect_engine):
    """Chunks for different documents are isolated by document_id."""
    store = PostgresContextStore(engine=dialect_engine)
    await store.initialize()

    doc1 = _doc(title="Doc1")
    doc2 = _doc(title="Doc2")
    await store.save_document(doc1)
    await store.save_document(doc2)

    c1 = _chunk(doc1, content="doc1 content")
    c2 = _chunk(doc2, content="doc2 content")
    await store.save_chunks([c1, c2])

    chunks1 = await store.get_chunks(doc1.id)
    chunks2 = await store.get_chunks(doc2.id)
    assert len(chunks1) == 1 and chunks1[0].content == "doc1 content"
    assert len(chunks2) == 1 and chunks2[0].content == "doc2 content"


# ---------------------------------------------------------------------------
# Migration-faithful upsert correctness for PostgresContextStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_document_upsert_against_migration_schema():
    """Prove ON CONFLICT (id) works on tables built exactly as migration 001.

    The parity dialect_engine fixture builds tables via create_all, which on
    Postgres uses the model-defined schema (possibly extra indices).  This test
    bypasses create_all entirely and reconstructs context_documents and
    context_chunks from raw DDL matching migration 001 exactly:

    * context_documents: ``id`` PRIMARY KEY, tags as ARRAY(Text), JSONB metadata
    * context_chunks: ``id`` PRIMARY KEY, FK to context_documents

    No unique constraint beyond the PKs — so index_elements=["id"] is the
    only valid conflict target.
    """
    import os

    import pytest

    from sqlalchemy import func, select, text

    from sagewai.db.engine import create_engine

    pg_url = os.environ.get("SAGEWAI_TEST_DATABASE_URL")
    if not pg_url:
        pytest.skip("SAGEWAI_TEST_DATABASE_URL not set — Postgres-only test")

    engine = create_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS context_chunks CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS context_documents CASCADE"))
            await conn.execute(
                text(
                    """
                    CREATE TABLE context_documents (
                        id              TEXT PRIMARY KEY,
                        scope           TEXT NOT NULL,
                        scope_id        TEXT NOT NULL,
                        project_id      TEXT NOT NULL DEFAULT 'default',
                        title           TEXT NOT NULL,
                        source          TEXT NOT NULL DEFAULT 'upload',
                        source_uri      TEXT,
                        mime_type       TEXT NOT NULL DEFAULT 'text/plain',
                        file_size_bytes INTEGER DEFAULT 0,
                        chunk_count     INTEGER DEFAULT 0,
                        status          TEXT NOT NULL DEFAULT 'pending',
                        confidence      FLOAT DEFAULT 1.0,
                        freshness_at    TIMESTAMPTZ DEFAULT now(),
                        metadata        JSONB DEFAULT '{}'::jsonb,
                        created_at      TIMESTAMPTZ DEFAULT now(),
                        updated_at      TIMESTAMPTZ DEFAULT now(),
                        tags            TEXT[] NOT NULL DEFAULT '{}'
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX idx_ctx_docs_project ON context_documents (project_id)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX idx_ctx_docs_tags ON context_documents USING gin (tags)"
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE context_chunks (
                        id               TEXT PRIMARY KEY,
                        document_id      TEXT NOT NULL REFERENCES context_documents(id) ON DELETE CASCADE,
                        scope            TEXT NOT NULL,
                        scope_id         TEXT NOT NULL,
                        project_id       TEXT NOT NULL DEFAULT 'default',
                        content          TEXT NOT NULL,
                        chunk_index      INTEGER DEFAULT 0,
                        token_count      INTEGER DEFAULT 0,
                        embedding_model  TEXT DEFAULT 'text-embedding-3-small',
                        content_hash     TEXT NOT NULL,
                        importance       FLOAT DEFAULT 0.5,
                        access_count     INTEGER DEFAULT 0,
                        last_accessed_at TIMESTAMPTZ,
                        metadata         JSONB DEFAULT '{}'::jsonb,
                        created_at       TIMESTAMPTZ DEFAULT now()
                    )
                    """
                )
            )

        store = PostgresContextStore(engine=engine)

        doc = ContextDocument(
            id="migration-faithful-doc",
            scope=ContextScope.PROJECT,
            scope_id="proj1",
            project_id="proj1",
            title="First Title",
            source=ContextSource.UPLOAD,
            status="pending",
            tags=["a", "b"],
            metadata={"v": 1},
        )
        await store.save_document(doc)

        # Second save (upsert) — must NOT raise
        doc.status = "ready"
        doc.chunk_count = 2
        doc.tags = ["a", "b", "c"]
        await store.save_document(doc)

        result = await store.get_document("migration-faithful-doc")
        assert result is not None
        assert result.status == "ready", "upsert should have updated status"
        assert result.chunk_count == 2
        assert set(result.tags) == {"a", "b", "c"}

        # Confirm exactly one row after two saves
        from sagewai.db.models import ContextDocumentModel

        tbl = ContextDocumentModel.__table__
        async with engine.connect() as conn:
            count = (
                await conn.execute(select(func.count()).select_from(tbl))
            ).scalar_one()
        assert count == 1, f"expected 1 row after upsert, got {count}"

        # Save a chunk and verify FK roundtrip
        chunk = ContextChunk(
            id="migration-faithful-chunk",
            document_id="migration-faithful-doc",
            scope=ContextScope.PROJECT,
            scope_id="proj1",
            project_id="proj1",
            content="hello migration",
            chunk_index=0,
            token_count=2,
            content_hash=hashlib.sha256(b"hello migration").hexdigest(),
        )
        await store.save_chunks([chunk])
        chunks = await store.get_chunks("migration-faithful-doc")
        assert len(chunks) == 1
        assert chunks[0].content == "hello migration"

    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS context_chunks CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS context_documents CASCADE"))
        await engine.dispose()
