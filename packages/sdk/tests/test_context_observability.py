# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for observability hooks (#422)."""

import pytest

from sagewai.context.engine import ContextEngine
from sagewai.context.models import ContextScope
from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore


class TestObservabilityHooks:
    @pytest.mark.asyncio
    async def test_ingestion_events_emitted(self):
        events: list[tuple[str, dict]] = []

        def callback(event: str, data: dict):
            events.append((event, data))

        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            project_id="test",
            event_callback=callback,
        )

        await engine.ingest_text(
            "Test content",
            title="test-doc",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )

        event_types = [e[0] for e in events]
        assert "CONTEXT_INGESTION_STARTED" in event_types
        assert "CONTEXT_INGESTION_COMPLETED" in event_types

        completed = next(d for t, d in events if t == "CONTEXT_INGESTION_COMPLETED")
        assert "document_id" in completed
        assert "duration_ms" in completed
        assert "chunk_count" in completed

    @pytest.mark.asyncio
    async def test_search_events_emitted(self):
        events: list[tuple[str, dict]] = []

        def callback(event: str, data: dict):
            events.append((event, data))

        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            project_id="test",
            event_callback=callback,
        )

        await engine.ingest_text(
            "Some knowledge base content",
            title="kb",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )
        events.clear()  # clear ingestion events

        await engine.search("knowledge", top_k=3)

        event_types = [e[0] for e in events]
        assert "CONTEXT_SEARCH_STARTED" in event_types
        assert "CONTEXT_SEARCH_COMPLETED" in event_types

        completed = next(d for t, d in events if t == "CONTEXT_SEARCH_COMPLETED")
        assert "duration_ms" in completed
        assert "result_count" in completed
        assert "strategies" in completed
        assert "vector" in completed["strategies"]
        assert "bm25" in completed["strategies"]

    @pytest.mark.asyncio
    async def test_no_callback_no_error(self):
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            project_id="test",
        )
        # Should work fine without callback
        await engine.ingest_text(
            "Content",
            title="doc",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )
        await engine.search("content")

    @pytest.mark.asyncio
    async def test_callback_error_doesnt_break(self):
        def bad_callback(event: str, data: dict):
            raise RuntimeError("callback failed")

        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            project_id="test",
            event_callback=bad_callback,
        )
        # Should not raise even with broken callback
        await engine.ingest_text(
            "Content",
            title="doc",
            scope=ContextScope.PROJECT,
            scope_id="test",
        )
