# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for MilvusVectorMemory — Milvus-backed vector store."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.memory.milvus import MilvusVectorMemory


class TestMilvusVectorMemoryInit:
    def test_default_config(self):
        with patch("sagewai.memory.milvus.MilvusClient"):
            mem = MilvusVectorMemory()
            assert mem.collection == "agent_memory"
            assert mem.embedding_model == "text-embedding-3-small"
            assert mem.dim == 1536

    def test_custom_config(self):
        with patch("sagewai.memory.milvus.MilvusClient"):
            mem = MilvusVectorMemory(
                uri="http://milvus:19530",
                collection="custom",
                embedding_model="text-embedding-ada-002",
                dim=1536,
            )
            assert mem.collection == "custom"
            assert mem.embedding_model == "text-embedding-ada-002"


class TestMilvusVectorMemoryStore:
    @pytest.mark.asyncio
    async def test_store_creates_collection_if_missing(self):
        with (
            patch("sagewai.memory.milvus.MilvusClient") as mock_cls,
            patch("sagewai.memory.milvus.CollectionSchema") as mock_schema_cls,
            patch("sagewai.memory.milvus.FieldSchema") as _mock_field,
            patch("sagewai.memory.milvus.DataType") as _mock_dt,
        ):
            mock_client = MagicMock()
            mock_client.has_collection.return_value = False
            mock_cls.return_value = mock_client
            mock_schema_cls.return_value = MagicMock()

            with patch("sagewai.memory.milvus._embed", new_callable=AsyncMock) as mock_embed:
                mock_embed.return_value = [0.1] * 1536
                mem = MilvusVectorMemory()
                await mem.store("hello world", metadata={"source": "test"})

                mock_client.create_collection.assert_called_once()
                mock_client.insert.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_returns_doc_id(self):
        with patch("sagewai.memory.milvus.MilvusClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.has_collection.return_value = True
            mock_client.insert.return_value = {"insert_count": 1}
            mock_cls.return_value = mock_client

            with patch("sagewai.memory.milvus._embed", new_callable=AsyncMock) as mock_embed:
                mock_embed.return_value = [0.1] * 1536
                mem = MilvusVectorMemory()
                doc_id = await mem.store("test content")
                assert isinstance(doc_id, str)
                assert len(doc_id) > 0


class TestMilvusVectorMemoryRetrieve:
    @pytest.mark.asyncio
    async def test_retrieve_returns_strings(self):
        with patch("sagewai.memory.milvus.MilvusClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.has_collection.return_value = True
            mock_client.search.return_value = [
                [
                    {"entity": {"content": "result 1", "metadata": "{}"}, "distance": 0.9},
                    {"entity": {"content": "result 2", "metadata": "{}"}, "distance": 0.8},
                ]
            ]
            mock_cls.return_value = mock_client

            with patch("sagewai.memory.milvus._embed", new_callable=AsyncMock) as mock_embed:
                mock_embed.return_value = [0.1] * 1536
                mem = MilvusVectorMemory()
                results = await mem.retrieve("test query", top_k=5)
                assert isinstance(results, list)
                assert all(isinstance(r, str) for r in results)
                assert len(results) == 2

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_retrieve_empty_collection(self):
        with patch("sagewai.memory.milvus.MilvusClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.has_collection.return_value = False
            mock_cls.return_value = mock_client

            mem = MilvusVectorMemory()
            results = await mem.retrieve("query")
            assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_respects_top_k(self):
        with patch("sagewai.memory.milvus.MilvusClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.has_collection.return_value = True
            mock_client.search.return_value = [[]]
            mock_cls.return_value = mock_client

            with patch("sagewai.memory.milvus._embed", new_callable=AsyncMock) as mock_embed:
                mock_embed.return_value = [0.1] * 1536
                mem = MilvusVectorMemory()
                await mem.retrieve("query", top_k=3)
                call_kwargs = mock_client.search.call_args
                assert call_kwargs[1].get("limit") == 3 or call_kwargs[0][3] == 3


class TestMilvusVectorMemoryDelete:
    @pytest.mark.asyncio
    async def test_delete_by_id(self):
        with patch("sagewai.memory.milvus.MilvusClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.has_collection.return_value = True
            mock_client.delete.return_value = {"delete_count": 1}
            mock_cls.return_value = mock_client

            mem = MilvusVectorMemory()
            result = await mem.delete("doc-123")
            assert result is True
            mock_client.delete.assert_called_once()


class TestMilvusMemoryProtocol:
    """Verify MilvusVectorMemory satisfies MemoryProvider protocol."""

    def test_satisfies_protocol(self):
        from sagewai.memory import MemoryProvider

        with patch("sagewai.memory.milvus.MilvusClient"):
            mem = MilvusVectorMemory()
            assert isinstance(mem, MemoryProvider)
