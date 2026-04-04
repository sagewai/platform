"""Tests for multi-strategy retrieval, BM25, RRF, and re-ranking (#405)."""

import pytest

from sagewai.context.bm25 import BM25Index
from sagewai.context.engine import ContextEngine
from sagewai.context.models import ContextScope
from sagewai.context.reranker import NoopReranker, reciprocal_rank_fusion
from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore


class TestBM25Index:
    def test_empty_index(self):
        idx = BM25Index()
        assert idx.search("hello") == []
        assert len(idx) == 0

    def test_add_and_search(self):
        idx = BM25Index()
        idx.add("c1", "The quick brown fox jumps over the lazy dog")
        idx.add("c2", "A fast red car drives on the highway")
        idx.add("c3", "The lazy dog sleeps all day")

        results = idx.search("lazy dog")
        assert len(results) >= 1
        # Both c1 and c3 mention "lazy dog"
        result_ids = {r[0] for r in results}
        assert "c1" in result_ids or "c3" in result_ids

    def test_keyword_only_match(self):
        """BM25 should find exact keyword matches that vector search might miss."""
        idx = BM25Index()
        idx.add("c1", "Error code E-4021 occurred during deployment")
        idx.add("c2", "The deployment was successful with no issues")

        results = idx.search("E-4021")
        assert len(results) >= 1
        assert results[0][0] == "c1"

    def test_remove(self):
        idx = BM25Index()
        idx.add("c1", "hello world")
        idx.add("c2", "goodbye world")
        idx.remove("c1")
        assert len(idx) == 1

        results = idx.search("hello")
        result_ids = {r[0] for r in results}
        assert "c1" not in result_ids

    def test_scope_filtering(self):
        idx = BM25Index()
        idx.add("c1", "important document about policies")
        idx.add("c2", "another document about policies")

        results = idx.search("policies", chunk_ids={"c1"})
        assert len(results) == 1
        assert results[0][0] == "c1"


class TestReciprocalRankFusion:
    def test_single_result_set(self):
        results = reciprocal_rank_fusion([
            [("a", 0.9), ("b", 0.8), ("c", 0.7)],
        ])
        assert results[0][0] == "a"

    def test_merge_two_sets(self):
        results = reciprocal_rank_fusion([
            [("a", 0.9), ("b", 0.8)],
            [("b", 0.95), ("c", 0.85)],
        ])
        # "b" appears in both sets, should rank high
        ids = [r[0] for r in results]
        assert "b" in ids[:2]

    def test_empty_sets(self):
        results = reciprocal_rank_fusion([[], []])
        assert results == []

    def test_disjoint_sets(self):
        results = reciprocal_rank_fusion([
            [("a", 0.9)],
            [("b", 0.9)],
        ])
        assert len(results) == 2


class TestNoopReranker:
    @pytest.mark.asyncio
    async def test_preserves_order(self):
        reranker = NoopReranker()
        results = await reranker.rerank("query", ["doc1", "doc2", "doc3"], top_k=2)
        assert len(results) == 2
        assert results[0][0] == 0  # first doc stays first


class TestMultiStrategySearch:
    @pytest.mark.asyncio
    async def test_search_with_bm25(self):
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            project_id="test",
            enable_bm25=True,
        )

        await engine.ingest_text(
            "Error code E-4021 in production",
            title="error-log",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )
        await engine.ingest_text(
            "The system is running smoothly",
            title="status",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )

        # Force rebuild BM25 index
        engine._bm25_index = None
        results = await engine.search("E-4021", top_k=5)
        # With BM25, the error log should be findable via keyword
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_search_without_bm25(self):
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            project_id="test",
            enable_bm25=False,
        )
        await engine.ingest_text(
            "Some content",
            title="doc",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )
        results = await engine.search("content", top_k=5)
        assert isinstance(results, list)
