# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the LifecycleManager — importance decay, archive, discard."""

from datetime import datetime, timedelta, timezone

import pytest

from sagewai.context.lifecycle import LifecycleManager, LifecycleReport
from sagewai.context.models import ContextChunk, ContextDocument, ContextScope, ContextSource
from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore


@pytest.fixture
def stores():
    return InMemoryMetadataStore(), InMemoryVectorStore()


@pytest.fixture
def manager(stores):
    meta, vec = stores
    return LifecycleManager(metadata_store=meta, vector_store=vec)


async def _create_doc_with_chunks(
    meta_store,
    vec_store,
    doc_id: str = "doc-1",
    chunk_count: int = 3,
    status: str = "ready",
    importance: float = 0.5,
    age_days: int = 0,
) -> ContextDocument:
    now = datetime.now(timezone.utc) - timedelta(days=age_days)
    doc = ContextDocument(
        id=doc_id,
        scope=ContextScope.PROJECT,
        scope_id="test-project",
        project_id="test-project",
        title=f"Test doc {doc_id}",
        source=ContextSource.UPLOAD,
        status=status,
        created_at=now,
        updated_at=now,
        freshness_at=now,
    )
    await meta_store.save_document(doc)

    for i in range(chunk_count):
        chunk = ContextChunk(
            id=f"{doc_id}-chunk-{i}",
            document_id=doc_id,
            scope=ContextScope.PROJECT,
            scope_id="test-project",
            project_id="test-project",
            content=f"Content for chunk {i}",
            chunk_index=i,
            token_count=10,
            embedding_model="test",
            content_hash=f"hash-{doc_id}-{i}",
            importance=importance,
            created_at=now,
        )
        await meta_store.save_chunks([chunk])
        await vec_store.insert(
            chunk_id=chunk.id,
            vector=[0.1] * 10,
            metadata={"project_id": "test-project", "scope": "project", "scope_id": "test-project"},
        )

    return doc


class TestRunMaintenance:
    @pytest.mark.asyncio
    async def test_returns_report(self, manager, stores):
        meta, vec = stores
        await _create_doc_with_chunks(meta, vec)
        report = await manager.run_maintenance("test-project")
        assert isinstance(report, LifecycleReport)
        assert report.project_id == "test-project"
        assert report.duration_ms > 0

    @pytest.mark.asyncio
    async def test_empty_project(self, manager):
        report = await manager.run_maintenance("empty-project")
        assert report.chunks_compressed == 0
        assert report.documents_archived == 0
        assert report.chunks_discarded == 0


class TestRefreshImportance:
    @pytest.mark.asyncio
    async def test_decays_old_chunks(self, manager, stores):
        meta, vec = stores
        await _create_doc_with_chunks(meta, vec, age_days=30, importance=0.5)
        count = await manager.refresh_importance("test-project")
        assert count >= 1  # at least some chunks decayed

    @pytest.mark.asyncio
    async def test_no_decay_for_fresh_chunks(self, manager, stores):
        meta, vec = stores
        await _create_doc_with_chunks(meta, vec, age_days=0, importance=0.5)
        count = await manager.refresh_importance("test-project")
        assert count == 0


class TestCompressStale:
    @pytest.mark.asyncio
    async def test_identifies_stale_chunks(self, manager, stores):
        meta, vec = stores
        await _create_doc_with_chunks(meta, vec, age_days=100, importance=0.05)
        count = await manager.compress_stale("test-project")
        assert count >= 1

    @pytest.mark.asyncio
    async def test_skips_important_chunks(self, manager, stores):
        meta, vec = stores
        await _create_doc_with_chunks(meta, vec, age_days=100, importance=0.8)
        count = await manager.compress_stale("test-project")
        assert count == 0

    @pytest.mark.asyncio
    async def test_skips_recent_chunks(self, manager, stores):
        meta, vec = stores
        await _create_doc_with_chunks(meta, vec, age_days=10, importance=0.05)
        count = await manager.compress_stale("test-project")
        assert count == 0


class TestArchive:
    @pytest.mark.asyncio
    async def test_archives_low_importance_docs(self, manager, stores):
        meta, vec = stores
        await _create_doc_with_chunks(meta, vec, importance=0.01)
        archived = await manager.archive_low_importance("test-project")
        assert archived == 1
        doc = await meta.get_document("doc-1")
        assert doc.status == "archived"

    @pytest.mark.asyncio
    async def test_skips_important_docs(self, manager, stores):
        meta, vec = stores
        await _create_doc_with_chunks(meta, vec, importance=0.5)
        archived = await manager.archive_low_importance("test-project")
        assert archived == 0


class TestDiscard:
    @pytest.mark.asyncio
    async def test_discards_old_archived_docs(self, manager, stores):
        meta, vec = stores
        await _create_doc_with_chunks(meta, vec, status="archived", age_days=400)
        discarded = await manager.discard_old("test-project")
        assert discarded == 1
        assert await meta.get_document("doc-1") is None

    @pytest.mark.asyncio
    async def test_keeps_recent_archived_docs(self, manager, stores):
        meta, vec = stores
        await _create_doc_with_chunks(meta, vec, status="archived", age_days=100)
        discarded = await manager.discard_old("test-project")
        assert discarded == 0

    @pytest.mark.asyncio
    async def test_ignores_ready_docs(self, manager, stores):
        meta, vec = stores
        await _create_doc_with_chunks(meta, vec, status="ready", age_days=400)
        discarded = await manager.discard_old("test-project")
        assert discarded == 0
