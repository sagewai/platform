# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Milvus vector memory integration tests — require docker-compose.dev.yml.

Run: pytest tests/test_integration_milvus.py -v -m integration
"""

import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def collection_name():
    return f"test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def milvus_memory(collection_name):
    from sagewai.memory.milvus import MilvusVectorMemory

    mem = MilvusVectorMemory(
        uri="http://localhost:19530",
        collection=collection_name,
        embedding_model="text-embedding-3-small",
        dim=1536,
    )
    yield mem
    mem.clear()


class TestMilvusIntegration:
    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, milvus_memory):
        """Store content and retrieve it via semantic search."""
        from unittest.mock import AsyncMock, patch

        import numpy as np

        fake_embedding = list(np.random.randn(1536).astype(float))
        with patch(
            "sagewai.memory.milvus._embed",
            new_callable=AsyncMock,
            return_value=fake_embedding,
        ):
            doc_id = await milvus_memory.store("Python is a programming language")
            assert doc_id is not None

            results = await milvus_memory.retrieve("What is Python?", top_k=3)
            assert len(results) >= 1
            assert "Python" in results[0]

    @pytest.mark.asyncio
    async def test_delete_by_id(self, milvus_memory):
        """Delete a stored document by ID."""
        from unittest.mock import AsyncMock, patch

        import numpy as np

        fake_embedding = list(np.random.randn(1536).astype(float))
        with patch(
            "sagewai.memory.milvus._embed",
            new_callable=AsyncMock,
            return_value=fake_embedding,
        ):
            doc_id = await milvus_memory.store("Temporary content")
            assert doc_id is not None
            deleted = await milvus_memory.delete(doc_id)
            assert deleted is True

    @pytest.mark.asyncio
    async def test_clear_removes_all(self, milvus_memory):
        """clear() should remove the collection entirely."""
        from unittest.mock import AsyncMock, patch

        import numpy as np

        fake_embedding = list(np.random.randn(1536).astype(float))
        with patch(
            "sagewai.memory.milvus._embed",
            new_callable=AsyncMock,
            return_value=fake_embedding,
        ):
            await milvus_memory.store("Content 1")
            await milvus_memory.store("Content 2")
            await milvus_memory.clear()
            results = await milvus_memory.retrieve("anything", top_k=10)
            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_retrieve_respects_top_k(self, milvus_memory):
        """retrieve() should return at most top_k results."""
        from unittest.mock import patch

        import numpy as np

        call_count = 0

        async def varying_embedding(text, model):
            nonlocal call_count
            call_count += 1
            vec = list(np.random.randn(1536).astype(float))
            return vec

        with patch("sagewai.memory.milvus._embed", side_effect=varying_embedding):
            for i in range(5):
                await milvus_memory.store(f"Document number {i}")
            results = await milvus_memory.retrieve("documents", top_k=2)
            assert len(results) <= 2
