# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for MilvusContextVectorStore — Milvus-backed context vector store.

These tests mock only ``pymilvus.MilvusClient`` (the part that needs a live
Milvus server) and let the store build *real* pymilvus ``CollectionSchema`` /
``FieldSchema`` objects and call the *real* ``prepare_index_params`` API. They
therefore exercise the live pymilvus API surface and would fail if a pymilvus
upgrade changed the schema or index API — which is exactly the regression a
major-version bump (e.g. 2.6 -> 3.0) could introduce.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sagewai.context.milvus_store import MilvusContextVectorStore, _escape_milvus


def _mock_client(*, has_collection: bool = False, indexes: list | None = None) -> MagicMock:
    """Build a mock MilvusClient with the call surface the store exercises."""
    client = MagicMock()
    client.has_collection.return_value = has_collection
    client.list_indexes.return_value = [] if indexes is None else indexes
    client.prepare_index_params.return_value = MagicMock()
    return client


class TestEscapeMilvus:
    def test_escapes_double_quote(self):
        assert _escape_milvus('a"b') == 'a\\"b'

    def test_escapes_backslash(self):
        assert _escape_milvus("a\\b") == "a\\\\b"

    def test_plain_string_unchanged(self):
        assert _escape_milvus("project-123") == "project-123"


class TestInit:
    def test_default_config(self):
        store = MilvusContextVectorStore()
        assert store.uri == "http://localhost:19530"
        assert store.collection_name == "context_vectors"
        assert store.dim == 1536
        assert store._client is None

    def test_custom_config(self):
        store = MilvusContextVectorStore(
            uri="http://milvus:19530", collection="ctx", dim=768
        )
        assert store.uri == "http://milvus:19530"
        assert store.collection_name == "ctx"
        assert store.dim == 768


class TestInitialize:
    @pytest.mark.asyncio
    async def test_creates_collection_and_index_when_missing(self):
        client = _mock_client(has_collection=False, indexes=[])
        with patch("pymilvus.MilvusClient", return_value=client):
            store = MilvusContextVectorStore()
            await store.initialize()

        # A real pymilvus CollectionSchema was built and passed through —
        # this is the assertion that guards the pymilvus API surface.
        from pymilvus import CollectionSchema

        client.create_collection.assert_called_once()
        schema = client.create_collection.call_args.kwargs["schema"]
        assert isinstance(schema, CollectionSchema)
        field_names = {f.name for f in schema.fields}
        assert field_names == {
            "id",
            "vector",
            "project_id",
            "scope",
            "scope_id",
            "document_id",
        }
        client.create_index.assert_called_once()
        client.load_collection.assert_called_once_with("context_vectors")

    @pytest.mark.asyncio
    async def test_skips_create_when_collection_exists(self):
        client = _mock_client(has_collection=True, indexes=["vector_idx"])
        with patch("pymilvus.MilvusClient", return_value=client):
            store = MilvusContextVectorStore()
            await store.initialize()

        client.create_collection.assert_not_called()
        client.create_index.assert_not_called()
        client.load_collection.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_missing_index_on_existing_collection(self):
        client = _mock_client(has_collection=True, indexes=[])
        with patch("pymilvus.MilvusClient", return_value=client):
            store = MilvusContextVectorStore()
            await store.initialize()

        client.create_collection.assert_not_called()
        client.create_index.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_failure_is_swallowed(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        client.load_collection.side_effect = RuntimeError("collection not ready")
        with patch("pymilvus.MilvusClient", return_value=client):
            store = MilvusContextVectorStore()
            await store.initialize()  # must not raise


class TestInsert:
    @pytest.mark.asyncio
    async def test_insert_maps_metadata_fields(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        store = MilvusContextVectorStore()
        store._client = client
        await store.insert(
            "chunk-1",
            [0.1, 0.2],
            {
                "project_id": "p1",
                "scope": "document",
                "scope_id": "s1",
                "document_id": "d1",
            },
        )
        client.insert.assert_called_once()
        data = client.insert.call_args.kwargs["data"]
        assert data == [
            {
                "id": "chunk-1",
                "vector": [0.1, 0.2],
                "project_id": "p1",
                "scope": "document",
                "scope_id": "s1",
                "document_id": "d1",
            }
        ]

    @pytest.mark.asyncio
    async def test_insert_uses_defaults_for_missing_metadata(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        store = MilvusContextVectorStore()
        store._client = client
        await store.insert("c", [0.0], {})
        data = client.insert.call_args.kwargs["data"][0]
        assert data["project_id"] == "default"
        assert data["scope"] == "project"
        assert data["scope_id"] == "default"
        assert data["document_id"] == ""

    @pytest.mark.asyncio
    async def test_insert_initializes_when_client_missing(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        with patch("pymilvus.MilvusClient", return_value=client):
            store = MilvusContextVectorStore()
            await store.insert("c", [0.1], {})
        client.insert.assert_called_once()


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_parses_hits_into_tuples(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        client.search.return_value = [
            [{"id": "a", "distance": 0.9}, {"id": "b", "distance": 0.7}]
        ]
        store = MilvusContextVectorStore()
        store._client = client
        hits = await store.search([0.1, 0.2], top_k=2)
        assert hits == [("a", 0.9), ("b", 0.7)]

    @pytest.mark.asyncio
    async def test_search_builds_filter_expression(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        client.search.return_value = [[]]
        store = MilvusContextVectorStore()
        store._client = client
        await store.search([0.1], top_k=5, filters={"project_id": "p1", "scope": "doc"})
        expr = client.search.call_args.kwargs["filter"]
        assert 'project_id == "p1"' in expr
        assert 'scope == "doc"' in expr
        assert " and " in expr

    @pytest.mark.asyncio
    async def test_search_escapes_filter_values(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        client.search.return_value = [[]]
        store = MilvusContextVectorStore()
        store._client = client
        await store.search([0.1], filters={"project_id": 'a"b'})
        expr = client.search.call_args.kwargs["filter"]
        assert expr == 'project_id == "a\\"b"'

    @pytest.mark.asyncio
    async def test_search_without_filters_passes_none(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        client.search.return_value = [[]]
        store = MilvusContextVectorStore()
        store._client = client
        await store.search([0.1])
        assert client.search.call_args.kwargs["filter"] is None

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        client.search.return_value = []
        store = MilvusContextVectorStore()
        store._client = client
        assert await store.search([0.1]) == []


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_builds_id_in_filter(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        store = MilvusContextVectorStore()
        store._client = client
        await store.delete(["a", "b"])
        expr = client.delete.call_args.kwargs["filter"]
        assert expr == 'id in ["a", "b"]'

    @pytest.mark.asyncio
    async def test_delete_escapes_ids(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        store = MilvusContextVectorStore()
        store._client = client
        await store.delete(['a"b'])
        assert client.delete.call_args.kwargs["filter"] == 'id in ["a\\"b"]'

    @pytest.mark.asyncio
    async def test_delete_empty_list_is_noop(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        store = MilvusContextVectorStore()
        store._client = client
        await store.delete([])
        client.delete.assert_not_called()


class TestClose:
    @pytest.mark.asyncio
    async def test_close_closes_client_and_clears_handle(self):
        client = _mock_client(has_collection=True, indexes=["i"])
        store = MilvusContextVectorStore()
        store._client = client
        await store.close()
        client.close.assert_called_once()
        assert store._client is None

    @pytest.mark.asyncio
    async def test_close_when_not_initialized_is_noop(self):
        store = MilvusContextVectorStore()
        await store.close()  # must not raise
        assert store._client is None
