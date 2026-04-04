"""Tests for memory subsystem: VectorMemory, GraphMemory, and RAGEngine."""

from __future__ import annotations

import pytest

from sagewai.memory import MemoryProvider
from sagewai.memory.graph import GraphMemory
from sagewai.memory.rag import RAGEngine, RetrievalStrategy
from sagewai.memory.vector import VectorMemory

# ===========================================================================
# VectorMemory tests
# ===========================================================================


class TestVectorMemory:
    @pytest.fixture
    def vmem(self):
        return VectorMemory()

    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, vmem):
        """Store entries and retrieve by similarity."""
        await vmem.store("Python is a programming language")
        await vmem.store("JavaScript runs in the browser")
        await vmem.store("Cooking recipes for pasta")

        results = await vmem.retrieve("programming language")
        assert len(results) > 0
        assert "Python is a programming language" in results[0]

    @pytest.mark.asyncio
    async def test_retrieve_empty(self, vmem):
        """Retrieve from empty store returns empty list."""
        results = await vmem.retrieve("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_top_k(self, vmem):
        """Retrieve respects top_k limit."""
        for i in range(10):
            await vmem.store(f"Document number {i} about programming")
        results = await vmem.retrieve("programming", top_k=3)
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_store_with_metadata(self, vmem):
        """Store accepts metadata without errors."""
        await vmem.store("test content", metadata={"source": "test", "timestamp": 123})
        assert len(vmem) == 1

    @pytest.mark.asyncio
    async def test_delete(self, vmem):
        """Delete removes matching entries."""
        await vmem.store("to be deleted")
        await vmem.store("to keep")
        result = await vmem.delete("to be deleted")
        assert result is True
        assert len(vmem) == 1

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, vmem):
        """Delete returns False when content not found."""
        result = await vmem.delete("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_clear(self, vmem):
        """Clear removes all entries."""
        await vmem.store("a")
        vmem.clear()
        assert len(vmem) == 0

    @pytest.mark.asyncio
    async def test_similarity_ordering(self, vmem):
        """Results are ordered by similarity."""
        await vmem.store("machine learning and artificial intelligence")
        await vmem.store("deep learning neural networks")
        await vmem.store("cooking Italian pasta recipes")

        results = await vmem.retrieve("machine learning AI")
        # ML entry should rank higher than cooking
        assert "machine learning" in results[0].lower()

    @pytest.mark.asyncio
    async def test_similarity_threshold(self):
        """Entries below threshold are excluded."""
        vmem = VectorMemory(similarity_threshold=0.5)
        await vmem.store("completely unrelated gibberish xyz abc")
        await vmem.store("Python programming language code")

        results = await vmem.retrieve("Python code")
        # Unrelated entry should be excluded by threshold
        for r in results:
            assert "gibberish" not in r

    def test_implements_memory_provider(self, vmem):
        """VectorMemory implements MemoryProvider protocol."""
        assert isinstance(vmem, MemoryProvider)


# ===========================================================================
# GraphMemory tests
# ===========================================================================


class TestGraphMemory:
    @pytest.fixture
    def gmem(self):
        return GraphMemory()

    @pytest.mark.asyncio
    async def test_store_entity(self, gmem):
        """Store creates a graph entity."""
        await gmem.store("Python", metadata={"type": "language"})
        entity = await gmem.get_entity("Python")
        assert entity == {"type": "language"}

    @pytest.mark.asyncio
    async def test_add_relation(self, gmem):
        """add_relation creates entities and links them."""
        await gmem.add_relation("Alice", "works_at", "Sagecurator")
        assert len(gmem) == 2  # Both entities created
        rels = await gmem.get_relations("Alice")
        assert len(rels) == 1
        assert rels[0] == ("Alice", "works_at", "Sagecurator")

    @pytest.mark.asyncio
    async def test_retrieve_with_entity_match(self, gmem):
        """retrieve() finds context when query mentions an entity."""
        await gmem.store("Python", metadata={"type": "language"})
        await gmem.add_relation("Python", "used_by", "DataScience")

        results = await gmem.retrieve("Tell me about Python")
        assert len(results) > 0
        assert any("Python" in r for r in results)

    @pytest.mark.asyncio
    async def test_retrieve_traverses_relations(self, gmem):
        """retrieve() traverses graph to find related entities."""
        await gmem.add_relation("Alice", "works_at", "Sagecurator")
        await gmem.add_relation("Sagecurator", "builds", "AI Products")

        results = await gmem.retrieve("Tell me about Alice", top_k=10)
        # Should find Alice and traverse to Sagecurator and AI Products
        all_text = " ".join(results)
        assert "Alice" in all_text
        assert "Sagecurator" in all_text

    @pytest.mark.asyncio
    async def test_retrieve_no_match(self, gmem):
        """retrieve() returns empty when no entities match."""
        await gmem.store("Python", metadata={"type": "language"})
        results = await gmem.retrieve("cooking recipes")
        assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_respects_top_k(self, gmem):
        """retrieve() respects top_k limit."""
        for i in range(10):
            await gmem.add_relation("Hub", f"connects_{i}", f"Node{i}")
        results = await gmem.retrieve("Hub", top_k=3)
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_delete_entity(self, gmem):
        """delete() removes entity and its relations."""
        await gmem.add_relation("Alice", "knows", "Bob")
        result = await gmem.delete("Alice")
        assert result is True
        assert len(gmem) == 1  # Bob remains
        rels = await gmem.get_relations("Alice")
        assert rels == []

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, gmem):
        """delete() returns False for unknown entity."""
        result = await gmem.delete("Ghost")
        assert result is False

    @pytest.mark.asyncio
    async def test_clear(self, gmem):
        """clear() removes everything."""
        await gmem.add_relation("A", "r", "B")
        gmem.clear()
        assert len(gmem) == 0

    @pytest.mark.asyncio
    async def test_get_entity_nonexistent(self, gmem):
        """get_entity() returns None for unknown entity."""
        result = await gmem.get_entity("Ghost")
        assert result is None

    def test_implements_memory_provider(self, gmem):
        """GraphMemory implements MemoryProvider protocol."""
        assert isinstance(gmem, MemoryProvider)


# ===========================================================================
# RAGEngine tests
# ===========================================================================


class TestRAGEngine:
    @pytest.fixture
    def rag(self):
        return RAGEngine()

    @pytest.mark.asyncio
    async def test_hybrid_retrieval(self, rag):
        """Hybrid strategy merges vector and graph results."""
        await rag.vector.store("Python is great for data science")
        await rag.graph.store("Python", metadata={"type": "language"})
        await rag.graph.add_relation("Python", "used_in", "DataScience")

        results = await rag.retrieve("Python data science")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_vector_only_strategy(self):
        """Vector-only strategy ignores graph."""
        rag = RAGEngine(strategy=RetrievalStrategy.VECTOR_ONLY)
        await rag.vector.store("Python programming")
        await rag.graph.store("Python", metadata={"type": "language"})

        results = await rag.retrieve("Python")
        # Should only have vector results
        assert len(results) >= 1
        assert "programming" in results[0].lower()

    @pytest.mark.asyncio
    async def test_graph_only_strategy(self):
        """Graph-only strategy ignores vector."""
        rag = RAGEngine(strategy=RetrievalStrategy.GRAPH_ONLY)
        await rag.vector.store("Python programming")
        await rag.graph.store("Python", metadata={"type": "language"})

        results = await rag.retrieve("Python")
        assert len(results) >= 1
        # Graph results have entity format, not plain text
        assert any("Python" in r for r in results)

    @pytest.mark.asyncio
    async def test_store_basic(self, rag):
        """store() adds to vector store."""
        await rag.store("test content")
        assert len(rag.vector) == 1
        assert len(rag.graph) == 0  # No entity flag

    @pytest.mark.asyncio
    async def test_store_with_entity_flag(self, rag):
        """store() with entity=True adds to both stores."""
        await rag.store("Python", metadata={"entity": True, "type": "language"})
        assert len(rag.vector) == 1
        assert len(rag.graph) == 1

    @pytest.mark.asyncio
    async def test_store_relation(self, rag):
        """store_relation() adds to graph and vector."""
        await rag.store_relation("Alice", "works_at", "Sagecurator")
        assert len(rag.graph) == 2  # Both entities
        assert len(rag.vector) == 1  # Relation text

    @pytest.mark.asyncio
    async def test_hybrid_deduplication(self, rag):
        """Hybrid strategy deduplicates results."""
        # Store same content in both
        await rag.vector.store("Python is a language")
        await rag.graph.store("Python", metadata={"desc": "is a language"})

        results = await rag.retrieve("Python", top_k=10)
        # No exact duplicates
        assert len(results) == len(set(results))

    @pytest.mark.asyncio
    async def test_retrieve_empty(self, rag):
        """Retrieve from empty RAG returns empty list."""
        results = await rag.retrieve("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_custom_weights(self):
        """Custom weights affect result allocation."""
        rag = RAGEngine(vector_weight=0.8, graph_weight=0.2)
        assert rag.vector_weight == 0.8
        assert rag.graph_weight == 0.2

    @pytest.mark.asyncio
    async def test_clear(self, rag):
        """clear() empties both stores."""
        await rag.store("test")
        await rag.clear()
        assert len(rag.vector) == 0
        assert len(rag.graph) == 0

    def test_implements_memory_provider(self, rag):
        """RAGEngine implements MemoryProvider protocol."""
        assert isinstance(rag, MemoryProvider)

    @pytest.mark.asyncio
    async def test_default_stores_created(self):
        """RAGEngine creates default stores if none provided."""
        rag = RAGEngine()
        assert isinstance(rag.vector, VectorMemory)
        assert isinstance(rag.graph, GraphMemory)
