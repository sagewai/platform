"""Tests for context-specific error types (#423)."""

import pytest

from sagewai.errors import (
    ContextDocumentNotFoundError,
    ContextIngestionError,
    ContextSearchError,
    SagewaiContextError,
    SagewaiError,
)


class TestErrorHierarchy:
    def test_context_errors_inherit_from_sagewai(self):
        assert issubclass(SagewaiContextError, SagewaiError)
        assert issubclass(ContextIngestionError, SagewaiContextError)
        assert issubclass(ContextSearchError, SagewaiContextError)
        assert issubclass(ContextDocumentNotFoundError, SagewaiContextError)

    def test_ingestion_error_fields(self):
        err = ContextIngestionError(
            "Parse failed",
            document_id="doc-123",
            stage="parse",
        )
        assert str(err) == "Parse failed"
        assert err.document_id == "doc-123"
        assert err.stage == "parse"

    def test_search_error_fields(self):
        err = ContextSearchError(
            "All strategies failed",
            query="test query",
            strategies_attempted=["vector", "bm25"],
        )
        assert err.query == "test query"
        assert err.strategies_attempted == ["vector", "bm25"]

    def test_document_not_found_error(self):
        err = ContextDocumentNotFoundError("doc-456")
        assert "doc-456" in str(err)
        assert err.document_id == "doc-456"

    def test_catch_all_with_sagewai_error(self):
        """All context errors should be catchable with SagewaiError."""
        try:
            raise ContextDocumentNotFoundError("x")
        except SagewaiError:
            pass  # should be caught

    def test_catch_with_context_error(self):
        """All context errors should be catchable with SagewaiContextError."""
        try:
            raise ContextIngestionError("fail", stage="embed")
        except SagewaiContextError:
            pass


class TestEngineUsesContextErrors:
    @pytest.mark.asyncio
    async def test_reprocess_missing_doc_raises(self):
        from sagewai.context.engine import ContextEngine
        from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore

        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
        )
        with pytest.raises(ContextDocumentNotFoundError):
            await engine.reprocess_document("nonexistent-id")
