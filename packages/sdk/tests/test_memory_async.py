# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests verifying Milvus/Nebula memory methods use asyncio.to_thread."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# NebulaGraphMemory tests
# ---------------------------------------------------------------------------


def _make_nebula_memory():
    """Create a NebulaGraphMemory with a mocked ConnectionPool."""
    with patch("sagewai.memory.nebula.ConnectionPool") as MockPool, patch(
        "sagewai.memory.nebula.NebulaConfig"
    ):
        pool_instance = MagicMock()
        MockPool.return_value = pool_instance

        from sagewai.memory.nebula import NebulaGraphMemory

        mem = NebulaGraphMemory(project_id="test-proj")
        return mem, pool_instance


def _mock_session(pool_instance):
    """Return a mock session that the pool will hand out."""
    session = MagicMock()
    pool_instance.get_session.return_value = session
    # Make execute results look successful
    result = MagicMock()
    result.is_succeeded.return_value = True
    result.row_size.return_value = 0
    session.execute.return_value = result
    return session


class TestNebulaGraphMemoryAsync:
    """Verify NebulaGraphMemory methods delegate to asyncio.to_thread."""

    @pytest.mark.asyncio
    async def test_add_relation_uses_to_thread(self):
        mem, pool = _make_nebula_memory()
        session = _mock_session(pool)

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy:
            await mem.add_relation("A", "knows", "B")
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_add_relation

        # Verify the sync helper actually ran (session.execute was called)
        assert session.execute.call_count >= 3  # 2 vertex inserts + 1 edge

    @pytest.mark.asyncio
    async def test_retrieve_uses_to_thread(self):
        mem, pool = _make_nebula_memory()
        _mock_session(pool)

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy:
            result = await mem.retrieve("test_entity")
            # retrieve now extracts entity names and queries each via to_thread
            assert spy.call_count >= 1
            assert spy.call_args[0][0] == mem._sync_retrieve_entity

        assert result == []

    @pytest.mark.asyncio
    async def test_get_neighbors_uses_to_thread(self):
        mem, pool = _make_nebula_memory()
        _mock_session(pool)

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy:
            result = await mem.get_neighbors("entity_x")
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_get_neighbors

        assert result == []

    @pytest.mark.asyncio
    async def test_list_entities_uses_to_thread(self):
        mem, pool = _make_nebula_memory()
        session = _mock_session(pool)
        # Make list_entities return an empty but successful result
        result_mock = MagicMock()
        result_mock.is_succeeded.return_value = True
        result_mock.row_size.return_value = 0
        session.execute.return_value = result_mock

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy:
            result = await mem.list_entities()
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_list_entities

        assert result == []

    @pytest.mark.asyncio
    async def test_get_relations_uses_to_thread(self):
        mem, pool = _make_nebula_memory()
        _mock_session(pool)

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy:
            result = await mem.get_relations("entity_x")
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_get_relations

        assert result == []

    @pytest.mark.asyncio
    async def test_supersede_uses_to_thread(self):
        mem, pool = _make_nebula_memory()
        session = _mock_session(pool)
        succ = MagicMock()
        succ.is_succeeded.return_value = True
        session.execute.return_value = succ

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy:
            result = await mem.supersede("old_entity")
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_supersede

        assert result is True

    @pytest.mark.asyncio
    async def test_supersede_by_document_uses_to_thread(self):
        mem, pool = _make_nebula_memory()
        session = _mock_session(pool)
        res = MagicMock()
        res.is_succeeded.return_value = True
        res.row_size.return_value = 0
        session.execute.return_value = res

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy:
            result = await mem.supersede_by_document("doc-123")
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_supersede_by_document

        assert result == 0

    @pytest.mark.asyncio
    async def test_retrieve_at_uses_to_thread(self):
        mem, pool = _make_nebula_memory()
        _mock_session(pool)

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy:
            result = await mem.retrieve_at("q", 1000000)
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_retrieve_at

        assert result == []

    @pytest.mark.asyncio
    async def test_delete_uses_to_thread(self):
        mem, pool = _make_nebula_memory()
        session = _mock_session(pool)
        succ = MagicMock()
        succ.is_succeeded.return_value = True
        session.execute.return_value = succ

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy:
            result = await mem.delete("old_entity")
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_delete

        assert result is True

    @pytest.mark.asyncio
    async def test_clear_uses_to_thread(self):
        mem, pool = _make_nebula_memory()
        _mock_session(pool)

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy:
            await mem.clear()
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_clear


# ---------------------------------------------------------------------------
# MilvusVectorMemory tests
# ---------------------------------------------------------------------------


def _make_milvus_memory():
    """Create a MilvusVectorMemory with a mocked MilvusClient."""
    with patch("sagewai.memory.milvus.MilvusClient") as MockClient:
        client_instance = MagicMock()
        MockClient.return_value = client_instance

        from sagewai.memory.milvus import MilvusVectorMemory

        mem = MilvusVectorMemory(project_id="test-proj")
        return mem, client_instance


class TestMilvusVectorMemoryAsync:
    """Verify MilvusVectorMemory methods delegate to asyncio.to_thread."""

    @pytest.mark.asyncio
    async def test_store_uses_to_thread(self):
        mem, client = _make_milvus_memory()
        client.has_collection.return_value = False

        with (
            patch.object(mem, "_do_embed", return_value=[0.1] * 1536) as _,
            patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy,
        ):
            doc_id = await mem.store("hello world")
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_insert

        assert isinstance(doc_id, str)

    @pytest.mark.asyncio
    async def test_retrieve_uses_to_thread(self):
        mem, client = _make_milvus_memory()
        client.has_collection.return_value = False

        with (
            patch.object(mem, "_do_embed", return_value=[0.1] * 1536) as _,
            patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy,
        ):
            result = await mem.retrieve("query")
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_retrieve

        assert result == []

    @pytest.mark.asyncio
    async def test_delete_uses_to_thread(self):
        mem, client = _make_milvus_memory()
        client.has_collection.return_value = True

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy:
            result = await mem.delete("doc-123")
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_delete

        assert result is True

    @pytest.mark.asyncio
    async def test_clear_uses_to_thread(self):
        mem, client = _make_milvus_memory()
        client.has_collection.return_value = True

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as spy:
            await mem.clear()
            spy.assert_called_once()
            assert spy.call_args[0][0] == mem._sync_clear

        client.drop_collection.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_preserves_async_embedding(self):
        """Verify that _do_embed is awaited (not run in thread), only the insert is threaded."""
        mem, client = _make_milvus_memory()
        client.has_collection.return_value = False
        embed_called = False

        async def fake_embed(text):
            nonlocal embed_called
            embed_called = True
            return [0.1] * 1536

        with patch.object(mem, "_do_embed", side_effect=fake_embed):
            await mem.store("test content")

        assert embed_called, "_do_embed should be awaited directly, not via to_thread"

    @pytest.mark.asyncio
    async def test_retrieve_preserves_async_embedding(self):
        """Verify that _do_embed is awaited (not run in thread), only the search is threaded."""
        mem, client = _make_milvus_memory()
        client.has_collection.return_value = False
        embed_called = False

        async def fake_embed(text):
            nonlocal embed_called
            embed_called = True
            return [0.1] * 1536

        with patch.object(mem, "_do_embed", side_effect=fake_embed):
            await mem.retrieve("query")

        assert embed_called, "_do_embed should be awaited directly, not via to_thread"

    @pytest.mark.asyncio
    async def test_clear_no_collection(self):
        """clear() should not error when collection doesn't exist."""
        mem, client = _make_milvus_memory()
        client.has_collection.return_value = False

        await mem.clear()
        client.drop_collection.assert_not_called()
