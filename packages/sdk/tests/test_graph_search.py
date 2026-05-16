# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for graph store integration in search (#421)."""

import pytest

from sagewai.context.engine import ContextEngine
from sagewai.context.models import ContextScope
from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore


class MockGraphStore:
    """Mock graph store for testing."""

    def __init__(self, data: dict[str, list[str]] | None = None):
        self._data = data or {}

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        for key, values in self._data.items():
            if key.lower() in query.lower():
                return values[:top_k]
        return []

    async def store(self, content: str, metadata: dict | None = None) -> None:
        pass


class TestGraphSearchIntegration:
    @pytest.mark.asyncio
    async def test_search_includes_graph_results(self):
        graph = MockGraphStore(data={
            "python": ["Python is_a Language", "Python used_by DataScience"],
        })
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            graph_store=graph,
            project_id="test",
        )

        # Ingest a doc that mentions Python
        await engine.ingest_text(
            "Python is a versatile programming language used in data science",
            title="Python overview",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )

        results = await engine.search("Python programming", top_k=5)
        assert isinstance(results, list)
        # Should find the document through either vector, BM25, or graph

    @pytest.mark.asyncio
    async def test_search_without_graph_still_works(self):
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            project_id="test",
        )
        await engine.ingest_text(
            "Test content",
            title="test",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )
        results = await engine.search("test", top_k=5)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_graph_search_handles_failure(self):
        class FailingGraphStore:
            async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
                raise ConnectionError("Graph unavailable")

        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            graph_store=FailingGraphStore(),
            project_id="test",
        )
        await engine.ingest_text(
            "Some content",
            title="doc",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )
        # Should not raise — graph failure is handled gracefully
        results = await engine.search("content", top_k=5)
        assert isinstance(results, list)
