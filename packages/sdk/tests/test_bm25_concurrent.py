# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for BM25 thread safety and circuit breaker (#417)."""

import threading

import pytest

from sagewai.context.bm25 import BM25Index
from sagewai.context.engine import ContextEngine
from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore


class TestBM25ThreadSafety:
    def test_has_lock(self):
        idx = BM25Index()
        assert hasattr(idx, "_lock")
        assert isinstance(idx._lock, type(threading.Lock()))

    def test_concurrent_add_search(self):
        """Simulate concurrent add and search via threads."""
        idx = BM25Index()
        errors: list[Exception] = []

        def add_docs(start: int, count: int):
            try:
                for i in range(start, start + count):
                    idx.add(f"chunk-{i}", f"Document number {i} about topic {i % 10}")
            except Exception as e:
                errors.append(e)

        def search_loop(iterations: int):
            try:
                for _ in range(iterations):
                    idx.search("topic about document", top_k=5)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=add_docs, args=(0, 100)),
            threading.Thread(target=add_docs, args=(100, 100)),
            threading.Thread(target=search_loop, args=(50,)),
            threading.Thread(target=search_loop, args=(50,)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent access caused errors: {errors}"
        assert len(idx) == 200

    def test_concurrent_add_remove(self):
        idx = BM25Index()
        for i in range(50):
            idx.add(f"chunk-{i}", f"Content {i}")

        errors: list[Exception] = []

        def remove_docs():
            try:
                for i in range(25):
                    idx.remove(f"chunk-{i}")
            except Exception as e:
                errors.append(e)

        def add_docs():
            try:
                for i in range(50, 100):
                    idx.add(f"chunk-{i}", f"Content {i}")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=remove_docs),
            threading.Thread(target=add_docs),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


class TestEmbeddingCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_breaker_fields_exist(self):
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
        )
        assert engine._embed_failures == 0
        assert engine._embed_circuit_open_until == 0.0

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_failures(self):
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
        )
        # Simulate 3 failures
        engine._embed_failures = 3
        import time

        engine._embed_circuit_open_until = time.time() + 60

        # Should use hash fallback without trying API
        result = await engine._embed_query("test query")
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_resets_on_success(self):
        from unittest.mock import AsyncMock, patch

        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
        )
        engine._embed_failures = 2

        # Mock litellm.aembedding to simulate a successful call
        mock_response = AsyncMock()
        mock_response.return_value.data = [{"embedding": [0.1] * 128}]
        with patch("litellm.aembedding", mock_response):
            await engine._embed_query("test")
        # Successful embed resets the failure counter
        assert engine._embed_failures == 0
