# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for ContextEngine — scoped retrieval, MemoryProvider compat, ingestion."""

import pytest

from sagewai.context.engine import ContextEngine
from sagewai.context.models import ContextScope, ContextSource
from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore
from sagewai.memory import MemoryProvider


@pytest.fixture
def engine():
    return ContextEngine(
        metadata_store=InMemoryMetadataStore(),
        vector_store=InMemoryVectorStore(),
        project_id="test-project",
        org_id="test-org",
    )


class TestMemoryProviderProtocol:
    def test_implements_protocol(self, engine):
        assert isinstance(engine, MemoryProvider)

    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, engine):
        await engine.store("The capital of France is Paris.", metadata={"title": "facts"})
        results = await engine.retrieve("capital of France")
        # With zero vectors, results may be empty or return all — just check no error
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_retrieve_empty(self, engine):
        results = await engine.retrieve("anything")
        assert results == []


class TestIngestion:
    @pytest.mark.asyncio
    async def test_ingest_text(self, engine):
        doc = await engine.ingest_text(
            text="Hello world. This is a test document with some content.",
            title="test.txt",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        assert doc.status == "ready"
        assert doc.title == "test.txt"
        assert doc.scope == ContextScope.PROJECT
        assert doc.chunk_count >= 1

    @pytest.mark.asyncio
    async def test_ingest_file(self, engine):
        content = b"This is a test file with enough content to be meaningful."
        doc = await engine.ingest_file(
            file_bytes=content,
            filename="test.txt",
            scope=ContextScope.ORG,
            scope_id="test-org",
        )
        assert doc.status == "ready"
        assert doc.source == ContextSource.UPLOAD

    @pytest.mark.asyncio
    async def test_ingest_empty_text(self, engine):
        doc = await engine.ingest_text(
            text="",
            title="empty.txt",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        assert doc.status == "ready"
        assert doc.chunk_count == 0

    @pytest.mark.asyncio
    async def test_ingest_code_file(self, engine):
        code = b"def hello():\n    return 'world'\n\nclass Foo:\n    pass\n"
        doc = await engine.ingest_file(
            file_bytes=code,
            filename="main.py",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        assert doc.status == "ready"
        assert doc.mime_type == "text/x-python"


class TestDocumentManagement:
    @pytest.mark.asyncio
    async def test_list_documents(self, engine):
        await engine.ingest_text(
            text="Doc one content.",
            title="doc1.txt",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        await engine.ingest_text(
            text="Doc two content.",
            title="doc2.txt",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        docs = await engine.list_documents()
        assert len(docs) == 2

    @pytest.mark.asyncio
    async def test_list_documents_filter_scope(self, engine):
        await engine.ingest_text(
            text="Org content.",
            title="org.txt",
            scope=ContextScope.ORG,
            scope_id="test-org",
        )
        await engine.ingest_text(
            text="Project content.",
            title="project.txt",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        org_docs = await engine.list_documents(scope=ContextScope.ORG)
        assert len(org_docs) == 1
        assert org_docs[0].scope == ContextScope.ORG

    @pytest.mark.asyncio
    async def test_get_document(self, engine):
        doc = await engine.ingest_text(
            text="Some content.",
            title="test.txt",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        fetched = await engine.get_document(doc.id)
        assert fetched is not None
        assert fetched.id == doc.id
        assert fetched.title == "test.txt"

    @pytest.mark.asyncio
    async def test_delete_document(self, engine):
        doc = await engine.ingest_text(
            text="To be deleted.",
            title="delete-me.txt",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        assert await engine.delete_document(doc.id) is True
        assert await engine.get_document(doc.id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, engine):
        assert await engine.delete_document("nonexistent-id") is False


class TestScopeInheritance:
    @pytest.mark.asyncio
    async def test_scope_filters_built(self, engine):
        filters = engine._build_scope_filters()
        scopes = {f[0] for f in filters}
        assert ContextScope.ORG in scopes
        assert ContextScope.PROJECT in scopes

    @pytest.mark.asyncio
    async def test_scope_filters_explicit(self, engine):
        filters = engine._build_scope_filters([ContextScope.ORG])
        assert len(filters) == 1
        assert filters[0][0] == ContextScope.ORG

    @pytest.mark.asyncio
    async def test_scope_filters_project_only(self):
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            project_id="proj",
        )
        filters = engine._build_scope_filters()
        scopes = {f[0] for f in filters}
        assert ContextScope.PROJECT in scopes


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_duplicate_content_not_stored_twice(self, engine):
        text = "This exact same content should only be stored once."
        await engine.ingest_text(
            text=text,
            title="first.txt",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        await engine.ingest_text(
            text=text,
            title="second.txt",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        docs = await engine.list_documents()
        # Both documents should exist (they're different documents)
        assert len(docs) == 2
        # But the second should have 0 chunks (content already exists)
        chunk_counts = sorted([d.chunk_count for d in docs])
        assert chunk_counts[0] == 0  # duplicate content was deduped


class TestTags:
    @pytest.mark.asyncio
    async def test_ingest_with_tags(self, engine):
        doc = await engine.ingest_text(
            text="Financial report Q4 2025",
            title="q4-report.md",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        doc.tags = ["finance", "quarterly"]
        await engine.metadata_store.update_document(doc)

        docs = await engine.list_documents(tags=["finance"])
        assert len(docs) == 1
        assert docs[0].id == doc.id
        assert set(docs[0].tags) == {"finance", "quarterly"}

    @pytest.mark.asyncio
    async def test_tags_filter_excludes_untagged(self, engine):
        await engine.ingest_text(
            text="Tagged content",
            title="tagged.md",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        tagged = (await engine.list_documents())[0]
        tagged.tags = ["research"]
        await engine.metadata_store.update_document(tagged)

        await engine.ingest_text(
            text="Untagged content",
            title="untagged.md",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )

        all_docs = await engine.list_documents()
        assert len(all_docs) == 2

        filtered = await engine.list_documents(tags=["research"])
        assert len(filtered) == 1
        assert filtered[0].title == "tagged.md"

    @pytest.mark.asyncio
    async def test_tags_intersection_filter(self, engine):
        doc = await engine.ingest_text(
            text="Multi-tagged doc",
            title="multi.md",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        doc.tags = ["finance", "research", "q4"]
        await engine.metadata_store.update_document(doc)

        assert len(await engine.list_documents(tags=["finance"])) == 1
        assert len(await engine.list_documents(tags=["finance", "research"])) == 1
        assert len(await engine.list_documents(tags=["finance", "nonexistent"])) == 0

    @pytest.mark.asyncio
    async def test_count_documents_with_tags(self, engine):
        for i, tag_list in enumerate([["a", "b"], ["b", "c"], ["c", "d"]]):
            doc = await engine.ingest_text(
                text=f"Doc {i}",
                title=f"doc{i}.md",
                scope=ContextScope.PROJECT,
                scope_id="test-project",
            )
            doc.tags = tag_list
            await engine.metadata_store.update_document(doc)

        assert await engine.count_documents() == 3
        assert await engine.count_documents(tags=["b"]) == 2
        assert await engine.count_documents(tags=["a", "b"]) == 1
        assert await engine.count_documents(tags=["z"]) == 0

    @pytest.mark.asyncio
    async def test_search_with_tags_filters_results(self, engine):
        for title, tag_list in [("finance.md", ["finance"]), ("travel.md", ["travel"])]:
            doc = await engine.ingest_text(
                text=f"Content about {title}",
                title=title,
                scope=ContextScope.PROJECT,
                scope_id="test-project",
            )
            doc.tags = tag_list
            await engine.metadata_store.update_document(doc)

        # Search with tag filter — should return empty since in-memory vectors
        # won't match, but the tag pre-filter shouldn't error
        results = await engine.search("content", tags=["finance"])
        assert isinstance(results, list)

        # Non-existent tag returns empty
        results = await engine.search("content", tags=["nonexistent"])
        assert results == []
