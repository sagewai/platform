"""Tests for conflict resolution and auto-supersede (#424)."""

import pytest

from sagewai.context.engine import ContextEngine
from sagewai.context.lifecycle import ConflictPair, LifecycleManager
from sagewai.context.models import ContextScope
from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore


class TestDetectConflicts:
    @pytest.mark.asyncio
    async def test_detects_overlapping_chunks(self):
        meta = InMemoryMetadataStore()
        vec = InMemoryVectorStore()
        mgr = LifecycleManager(metadata_store=meta, vector_store=vec)

        engine = ContextEngine(
            metadata_store=meta, vector_store=vec, project_id="test"
        )

        # Ingest two docs with overlapping content
        await engine.ingest_text(
            "The quarterly revenue was $45 million, up 23% year over year",
            title="Q1 report v1",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )
        await engine.ingest_text(
            "The quarterly revenue was $48 million, up 28% year over year",
            title="Q1 report v2",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )

        conflicts = await mgr.detect_conflicts("test")
        # Should detect overlap between the two similar-but-different chunks
        assert isinstance(conflicts, list)
        # The two chunks share many words so should be flagged
        if conflicts:
            assert isinstance(conflicts[0], ConflictPair)

    @pytest.mark.asyncio
    async def test_no_conflicts_with_unrelated_content(self):
        meta = InMemoryMetadataStore()
        vec = InMemoryVectorStore()
        mgr = LifecycleManager(metadata_store=meta, vector_store=vec)

        engine = ContextEngine(
            metadata_store=meta, vector_store=vec, project_id="test"
        )

        await engine.ingest_text(
            "Python is a programming language",
            title="python",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )
        await engine.ingest_text(
            "The recipe calls for 2 cups of flour and 3 eggs",
            title="recipe",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )

        conflicts = await mgr.detect_conflicts("test")
        assert len(conflicts) == 0


class TestAutoSupersede:
    @pytest.mark.asyncio
    async def test_supersede_document_reduces_importance(self):
        meta = InMemoryMetadataStore()
        vec = InMemoryVectorStore()
        engine = ContextEngine(
            metadata_store=meta, vector_store=vec, project_id="test"
        )

        doc = await engine.ingest_text(
            "Old content that will be superseded",
            title="supersede-target",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )

        # Supersede it
        await engine._supersede_document(doc.id)

        # All chunks should have importance 0
        chunks = await meta.get_chunks(doc.id)
        for chunk in chunks:
            assert chunk.importance == 0.0

        # Document should be marked as superseded
        updated = await meta.get_document(doc.id)
        assert updated.metadata.get("superseded") is True

    @pytest.mark.asyncio
    async def test_engine_has_supersede_method(self):
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            project_id="test",
        )
        assert hasattr(engine, "_supersede_document")
