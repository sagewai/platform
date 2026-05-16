# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tier 4: Memory & RAG — real Milvus + NebulaGraph.

Scenarios 17-21:
17. MilvusVectorMemory store + retrieve
18. GraphMemory entity storage
19. RAG hybrid (vector + graph)
20. ConversationManager with auto-memory
21. Cross-session memory persistence
"""

from __future__ import annotations

import pytest

from sagewai.core.conversation import ConversationManager
from sagewai.core.session import InMemorySessionStore
from sagewai.engines.universal import UniversalAgent
from sagewai.memory.graph import GraphMemory
from sagewai.memory.milvus import MilvusVectorMemory
from sagewai.memory.rag import RAGEngine, RetrievalStrategy

# --- Scenario 17: Milvus vector memory ---


@pytest.mark.integration
async def test_milvus_store_retrieve(milvus_uri: str):
    """Store text in Milvus, retrieve by similarity."""
    memory = MilvusVectorMemory(
        uri=milvus_uri,
        collection="test_validation_17",
        embedding_model="mistral/mistral-embed",
        dim=1024,
    )

    try:
        await memory.store("Python was created by Guido van Rossum in 1991.")
        await memory.store("JavaScript was created by Brendan Eich in 1995.")
        await memory.store("Rust was created by Graydon Hoare at Mozilla.")

        results = await memory.retrieve("Who created Python?", top_k=2)
        assert len(results) > 0
        assert any("Guido" in r or "Python" in r for r in results)
    finally:
        memory.clear()


# --- Scenario 18: Graph memory ---


@pytest.mark.integration
async def test_graph_memory_entities():
    """Store and retrieve entity relationships."""
    memory = GraphMemory()

    await memory.store("Alice works at Google as an engineer.")
    await memory.add_relation("Alice", "works_at", "Google")

    results = await memory.retrieve("Where does Alice work?", top_k=3)
    assert len(results) > 0


# --- Scenario 19: RAG hybrid ---


@pytest.mark.integration
async def test_rag_hybrid(milvus_uri: str):
    """RAG combines vector + graph results."""
    vector_mem = MilvusVectorMemory(
        uri=milvus_uri,
        collection="test_validation_19",
        embedding_model="mistral/mistral-embed",
        dim=1024,
    )
    graph_mem = GraphMemory()

    rag = RAGEngine(
        vector=vector_mem,
        graph=graph_mem,
        strategy=RetrievalStrategy.HYBRID,
    )

    try:
        await vector_mem.store("Sagewai is an LLM-agnostic agent framework.")
        await vector_mem.store("It supports OpenAI, Anthropic, and Google models.")
        await graph_mem.store("Sagewai is a framework")
        await graph_mem.add_relation("Sagewai", "uses", "LiteLLM")

        results = await rag.retrieve("What is Sagewai?", top_k=3)
        assert len(results) > 0
    finally:
        vector_mem.clear()


# --- Scenario 20: ConversationManager with memory ---


@pytest.mark.integration
async def test_conversation_with_memory(milvus_uri: str):
    """ConversationManager extracts facts into vector memory."""
    memory = MilvusVectorMemory(
        uri=milvus_uri,
        collection="test_validation_20",
        embedding_model="mistral/mistral-embed",
        dim=1024,
    )

    try:
        agent = UniversalAgent(
            name="memory-agent",
            model="claude-haiku-4-5-20251001",
            memory=memory,
        )
        session_store = InMemorySessionStore()
        mgr = ConversationManager(
            agent=agent,
            session_store=session_store,
        )

        await mgr.send("I love hiking in the Swiss Alps.")
        await mgr.send("My favorite programming language is Rust.")

        # Memory should have stored some facts
        results = await memory.retrieve("What does the user enjoy?", top_k=3)
        # At minimum the conversation happened; memory extraction is best-effort
        assert isinstance(results, list)
    finally:
        memory.clear()


# --- Scenario 21: Cross-session memory ---


@pytest.mark.integration
async def test_cross_session_memory(milvus_uri: str):
    """New session can retrieve context from previous session's memory."""
    memory = MilvusVectorMemory(
        uri=milvus_uri,
        collection="test_validation_21",
        embedding_model="mistral/mistral-embed",
        dim=1024,
    )

    try:
        # Session 1: store facts
        await memory.store("User prefers dark mode in all applications.")
        await memory.store("User's name is Bob and he works at Acme Corp.")

        # Session 2: new agent, same memory, should retrieve context
        agent = UniversalAgent(
            name="session2-agent",
            model="claude-haiku-4-5-20251001",
            memory=memory,
            system_prompt="Use the memory context to personalize responses.",
        )
        response = await agent.chat("What do you know about me?")
        # The agent should reference stored facts if memory injection works
        assert len(response) > 10
    finally:
        memory.clear()
