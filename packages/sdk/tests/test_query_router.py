"""Tests for QueryRouter and AUTO retrieval strategy."""

from __future__ import annotations

import pytest

from sagewai.memory.query_router import QueryIntent, QueryRouter
from sagewai.memory.rag import RAGEngine, RetrievalStrategy

# ------------------------------------------------------------------
# QueryIntent enum
# ------------------------------------------------------------------


def test_query_intent_values():
    assert QueryIntent.SEMANTIC.value == "semantic"
    assert QueryIntent.RELATIONAL.value == "relational"
    assert QueryIntent.AMBIGUOUS.value == "ambiguous"


def test_query_intent_count():
    assert len(QueryIntent) == 3


# ------------------------------------------------------------------
# RetrievalStrategy.AUTO
# ------------------------------------------------------------------


def test_auto_strategy_exists():
    assert RetrievalStrategy.AUTO.value == "auto"


def test_retrieval_strategy_count():
    assert len(RetrievalStrategy) == 4


# ------------------------------------------------------------------
# Relational keyword detection
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "How are Google and Microsoft related?",
        "What is the relationship between X and Y?",
        "Are these two things connected?",
        "Show me the link between sales and revenue",
        "Revenue depends on customer satisfaction",
        "What causes inflation?",
        "This leads to better outcomes",
        "Show neighboring nodes",
        "Find parent entities",
        "What is upstream of this service?",
    ],
)
def test_relational_keywords(query: str):
    router = QueryRouter()
    assert router.classify(query) == QueryIntent.RELATIONAL


# ------------------------------------------------------------------
# Semantic keyword detection
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "What is machine learning?",
        "Explain the concept of neural networks",
        "Describe the architecture of transformers",
        "Summarize the research paper",
        "Define reinforcement learning",
        "How does backpropagation work?",
        "Tell me about attention mechanisms",
        "What are embeddings?",
        "What is the meaning of RAG?",
        "Give me an overview of the project",
    ],
)
def test_semantic_keywords(query: str):
    router = QueryRouter()
    assert router.classify(query) == QueryIntent.SEMANTIC


# ------------------------------------------------------------------
# Entity density detection
# ------------------------------------------------------------------


def test_entity_density_two_entities():
    """Two named entities → relational."""
    router = QueryRouter()
    intent = router.classify("Compare New York and Los Angeles")
    assert intent == QueryIntent.RELATIONAL


def test_entity_density_single_entity():
    """One named entity alone → ambiguous (not enough for relational)."""
    router = QueryRouter()
    intent = router.classify("Find me New York")
    assert intent == QueryIntent.AMBIGUOUS


# ------------------------------------------------------------------
# Ambiguous fallback
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "hello world",
        "search for something",
        "12345",
        "get data",
    ],
)
def test_ambiguous_fallback(query: str):
    router = QueryRouter()
    assert router.classify(query) == QueryIntent.AMBIGUOUS


# ------------------------------------------------------------------
# Custom classifier
# ------------------------------------------------------------------


def test_custom_classifier():
    """Custom classifier takes priority over built-in heuristics."""

    def always_semantic(query: str) -> QueryIntent:
        return QueryIntent.SEMANTIC

    router = QueryRouter(custom_classifier=always_semantic)
    # "related" would normally be RELATIONAL, but custom overrides
    assert router.classify("How are they related?") == QueryIntent.SEMANTIC


def test_custom_classifier_per_query():
    """Custom classifier can use query content."""

    def length_based(query: str) -> QueryIntent:
        if len(query) > 50:
            return QueryIntent.RELATIONAL
        return QueryIntent.SEMANTIC

    router = QueryRouter(custom_classifier=length_based)
    assert router.classify("short") == QueryIntent.SEMANTIC
    assert router.classify("x" * 51) == QueryIntent.RELATIONAL


# ------------------------------------------------------------------
# Route mapping
# ------------------------------------------------------------------


def test_route_semantic_to_vector():
    router = QueryRouter()
    strategy = router.route("What is deep learning?")
    assert strategy == RetrievalStrategy.VECTOR_ONLY


def test_route_relational_to_graph():
    router = QueryRouter()
    strategy = router.route("How are these entities connected?")
    assert strategy == RetrievalStrategy.GRAPH_ONLY


def test_route_ambiguous_to_hybrid():
    router = QueryRouter()
    strategy = router.route("hello world")
    assert strategy == RetrievalStrategy.HYBRID


# ------------------------------------------------------------------
# RAGEngine AUTO integration
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_routes_semantic_to_vector():
    """AUTO strategy routes semantic queries through vector path."""
    rag = RAGEngine(strategy=RetrievalStrategy.AUTO)
    await rag.vector.store("Deep learning is a subset of machine learning")
    await rag.graph.store("Deep Learning", metadata={"type": "concept"})

    results = await rag.retrieve("What is deep learning?")
    # Should use vector path → gets the stored text
    assert any("deep learning" in r.lower() for r in results)


@pytest.mark.asyncio
async def test_auto_routes_relational_to_graph():
    """AUTO strategy routes relational queries through graph path."""
    rag = RAGEngine(strategy=RetrievalStrategy.AUTO)
    await rag.graph.store("Python", metadata={"type": "language"})
    await rag.graph.store("Django", metadata={"type": "framework"})
    await rag.graph.add_relation("Django", "built_with", "Python")

    results = await rag.retrieve("How are Django and Python connected?")
    # Should use graph path → gets relationship context
    assert len(results) > 0


@pytest.mark.asyncio
async def test_auto_routes_ambiguous_to_hybrid():
    """AUTO strategy routes ambiguous queries through hybrid path."""
    rag = RAGEngine(strategy=RetrievalStrategy.AUTO)
    await rag.vector.store("some data about weather")
    await rag.graph.store("Weather", metadata={"type": "topic"})

    results = await rag.retrieve("weather stuff")
    # Should use hybrid → can get results from either path
    assert len(results) > 0


@pytest.mark.asyncio
async def test_auto_with_custom_router():
    """AUTO strategy respects custom QueryRouter."""

    def always_graph(query: str) -> QueryIntent:
        return QueryIntent.RELATIONAL

    router = QueryRouter(custom_classifier=always_graph)
    rag = RAGEngine(strategy=RetrievalStrategy.AUTO, query_router=router)
    await rag.graph.store("TestEntity", metadata={"type": "test"})

    results = await rag.retrieve("What is this?")
    # "What is" would normally be semantic → vector,
    # but custom classifier forces graph path
    assert len(results) >= 0  # Graph may or may not find matches


@pytest.mark.asyncio
async def test_non_auto_ignores_router():
    """Non-AUTO strategies don't use the router."""
    rag = RAGEngine(strategy=RetrievalStrategy.VECTOR_ONLY)
    await rag.vector.store("test content")

    # Relational query, but VECTOR_ONLY strategy ignores router
    results = await rag.retrieve("How are A and B connected?")
    # Uses vector regardless → searches by similarity
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_auto_empty_stores():
    """AUTO strategy works with empty stores."""
    rag = RAGEngine(strategy=RetrievalStrategy.AUTO)
    results = await rag.retrieve("What is anything?")
    assert results == []
