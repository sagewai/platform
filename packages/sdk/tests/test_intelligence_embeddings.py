# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the intelligence embeddings layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.intelligence.config import IntelligenceConfig
from sagewai.intelligence.embeddings.hash_embedder import HashEmbedder
from sagewai.intelligence.embeddings.litellm_embedder import LiteLLMEmbedder
from sagewai.intelligence.embeddings.protocol import Embedder
from sagewai.intelligence.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# HashEmbedder
# ---------------------------------------------------------------------------


class TestHashEmbedder:
    """Tests for the deterministic hash-based embedder."""

    def test_dimension(self) -> None:
        embedder = HashEmbedder(dimension=384)
        assert embedder.dimension == 384

    def test_custom_dimension(self) -> None:
        embedder = HashEmbedder(dimension=768)
        assert embedder.dimension == 768

    @pytest.mark.asyncio
    async def test_embed_returns_correct_shape(self) -> None:
        embedder = HashEmbedder(dimension=128)
        texts = ["hello world", "test input"]
        vectors = await embedder.embed(texts)
        assert len(vectors) == 2
        assert len(vectors[0]) == 128
        assert len(vectors[1]) == 128

    @pytest.mark.asyncio
    async def test_embed_query_returns_correct_shape(self) -> None:
        embedder = HashEmbedder(dimension=256)
        vector = await embedder.embed_query("test query")
        assert len(vector) == 256

    @pytest.mark.asyncio
    async def test_deterministic_output(self) -> None:
        embedder = HashEmbedder(dimension=64)
        v1 = await embedder.embed_query("deterministic test")
        v2 = await embedder.embed_query("deterministic test")
        assert v1 == v2

    @pytest.mark.asyncio
    async def test_different_inputs_different_vectors(self) -> None:
        embedder = HashEmbedder(dimension=64)
        v1 = await embedder.embed_query("input one")
        v2 = await embedder.embed_query("input two")
        assert v1 != v2

    @pytest.mark.asyncio
    async def test_values_in_range(self) -> None:
        embedder = HashEmbedder(dimension=384)
        vector = await embedder.embed_query("range test")
        for v in vector:
            assert -1.0 <= v <= 1.0

    @pytest.mark.asyncio
    async def test_embed_empty_list(self) -> None:
        embedder = HashEmbedder(dimension=64)
        vectors = await embedder.embed([])
        assert vectors == []

    def test_protocol_compliance(self) -> None:
        embedder = HashEmbedder()
        assert isinstance(embedder, Embedder)


# ---------------------------------------------------------------------------
# LiteLLMEmbedder
# ---------------------------------------------------------------------------


class TestLiteLLMEmbedder:
    """Tests for the LiteLLM API-based embedder."""

    def test_dimension(self) -> None:
        embedder = LiteLLMEmbedder(model="text-embedding-3-small", dimension=1536)
        assert embedder.dimension == 1536

    def test_custom_model(self) -> None:
        embedder = LiteLLMEmbedder(model="text-embedding-ada-002", dimension=1536)
        assert embedder._model == "text-embedding-ada-002"

    @pytest.mark.asyncio
    async def test_embed_calls_litellm(self) -> None:
        embedder = LiteLLMEmbedder(dimension=4)
        mock_response = MagicMock()
        mock_response.data = [
            {"embedding": [0.1, 0.2, 0.3, 0.4]},
            {"embedding": [0.5, 0.6, 0.7, 0.8]},
        ]
        with patch("litellm.aembedding", new_callable=AsyncMock, return_value=mock_response):
            vectors = await embedder.embed(["hello", "world"])
        assert len(vectors) == 2
        assert vectors[0] == [0.1, 0.2, 0.3, 0.4]

    @pytest.mark.asyncio
    async def test_embed_query_calls_litellm(self) -> None:
        embedder = LiteLLMEmbedder(dimension=4)
        mock_response = MagicMock()
        mock_response.data = [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
        with patch("litellm.aembedding", new_callable=AsyncMock, return_value=mock_response):
            vector = await embedder.embed_query("test query")
        assert vector == [0.1, 0.2, 0.3, 0.4]

    @pytest.mark.asyncio
    async def test_embed_batches_large_input(self) -> None:
        embedder = LiteLLMEmbedder(dimension=2, batch_size=3)
        mock_response = MagicMock()
        mock_response.data = [
            {"embedding": [0.1, 0.2]},
            {"embedding": [0.3, 0.4]},
            {"embedding": [0.5, 0.6]},
        ]
        call_count = 0

        async def mock_aembedding(**kwargs):
            nonlocal call_count
            call_count += 1
            batch_size = len(kwargs["input"])
            resp = MagicMock()
            resp.data = [{"embedding": [0.1, 0.2]} for _ in range(batch_size)]
            return resp

        with patch("litellm.aembedding", side_effect=mock_aembedding):
            texts = [f"text {i}" for i in range(7)]
            vectors = await embedder.embed(texts)
        assert len(vectors) == 7
        assert call_count == 3  # ceil(7/3) = 3 batches

    def test_protocol_compliance(self) -> None:
        embedder = LiteLLMEmbedder()
        assert isinstance(embedder, Embedder)


# ---------------------------------------------------------------------------
# SentenceTransformerEmbedder
# ---------------------------------------------------------------------------


class TestSentenceTransformerEmbedder:
    """Tests for the local sentence-transformers embedder."""

    def test_import_error_when_not_installed(self) -> None:
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            from sagewai.intelligence.embeddings.sentence_transformer import (
                SentenceTransformerEmbedder,
            )

            with pytest.raises(ImportError, match="sentence-transformers"):
                SentenceTransformerEmbedder()

    def test_protocol_compliance_with_mock(self) -> None:
        """Verify the class signature matches the Embedder protocol."""
        from sagewai.intelligence.embeddings.sentence_transformer import (
            SentenceTransformerEmbedder,
        )

        # Manually construct without triggering __init__ (which needs the lib)
        embedder = object.__new__(SentenceTransformerEmbedder)
        embedder._model = MagicMock()
        embedder._dimension = 384
        assert isinstance(embedder, Embedder)
        assert embedder.dimension == 384


# ---------------------------------------------------------------------------
# ProviderRegistry
# ---------------------------------------------------------------------------


class TestProviderRegistry:
    """Tests for the provider fallback chain."""

    def test_hash_provider(self) -> None:
        config = IntelligenceConfig(embedding_provider="hash")
        embedder = ProviderRegistry.get_embedder(config)
        assert isinstance(embedder, HashEmbedder)

    def test_api_provider(self) -> None:
        config = IntelligenceConfig(embedding_provider="api")
        embedder = ProviderRegistry.get_embedder(config)
        assert isinstance(embedder, LiteLLMEmbedder)

    def test_api_provider_uses_config(self) -> None:
        config = IntelligenceConfig(
            embedding_provider="api",
            embedding_api_model="text-embedding-ada-002",
            embedding_api_dimension=1536,
        )
        embedder = ProviderRegistry.get_embedder(config)
        assert isinstance(embedder, LiteLLMEmbedder)
        assert embedder._model == "text-embedding-ada-002"
        assert embedder.dimension == 1536

    def test_auto_falls_back_to_api_or_hash(self) -> None:
        """Auto mode with sentence-transformers unavailable should fallback."""
        config = IntelligenceConfig(embedding_provider="auto")
        with patch(
            "sagewai.intelligence.registry._try_local",
            side_effect=ImportError("no sentence-transformers"),
        ):
            embedder = ProviderRegistry.get_embedder(config)
        # Should be either LiteLLM or Hash
        assert isinstance(embedder, (LiteLLMEmbedder, HashEmbedder))

    def test_auto_falls_back_to_hash_when_all_fail(self) -> None:
        config = IntelligenceConfig(embedding_provider="auto")
        with patch(
            "sagewai.intelligence.registry._try_local",
            side_effect=ImportError("no sentence-transformers"),
        ):
            with patch(
                "sagewai.intelligence.registry.LiteLLMEmbedder",
                side_effect=Exception("litellm broken"),
            ):
                embedder = ProviderRegistry.get_embedder(config)
        assert isinstance(embedder, HashEmbedder)

    def test_default_config(self) -> None:
        """Default config with no args should not raise."""
        with patch(
            "sagewai.intelligence.registry._try_local",
            side_effect=ImportError("no sentence-transformers"),
        ):
            embedder = ProviderRegistry.get_embedder()
        assert isinstance(embedder, (LiteLLMEmbedder, HashEmbedder))


# ---------------------------------------------------------------------------
# IntelligenceConfig
# ---------------------------------------------------------------------------


class TestIntelligenceConfig:
    """Tests for the configuration model."""

    def test_defaults(self) -> None:
        config = IntelligenceConfig()
        assert config.embedding_provider == "auto"
        assert config.embedding_model == "all-MiniLM-L6-v2"
        assert config.embedding_api_model == "text-embedding-3-small"
        assert config.embedding_api_dimension == 1536

    def test_custom_values(self) -> None:
        config = IntelligenceConfig(
            embedding_provider="local",
            embedding_model="all-mpnet-base-v2",
            embedding_api_model="text-embedding-ada-002",
            embedding_api_dimension=768,
        )
        assert config.embedding_provider == "local"
        assert config.embedding_model == "all-mpnet-base-v2"


# ---------------------------------------------------------------------------
# Backward compatibility: ContextEngine without explicit embedder
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Verify that existing code works without an explicit embedder."""

    @pytest.mark.asyncio
    async def test_context_engine_without_embedder(self) -> None:
        """ContextEngine should still work when no embedder is passed."""
        from sagewai.context.models import ChunkingConfig, ContextScope
        from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore

        engine_module = __import__(
            "sagewai.context.engine", fromlist=["ContextEngine"]
        )
        ContextEngine = engine_module.ContextEngine

        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
        )
        # Should have no embedder set
        assert engine._embedder is None
        # Pipeline should also have no embedder
        assert engine._pipeline._embedder is None

    @pytest.mark.asyncio
    async def test_context_engine_with_embedder(self) -> None:
        """ContextEngine should wire the embedder through to the pipeline."""
        from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore

        embedder = HashEmbedder(dimension=128)

        engine_module = __import__(
            "sagewai.context.engine", fromlist=["ContextEngine"]
        )
        ContextEngine = engine_module.ContextEngine

        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            embedder=embedder,
        )
        assert engine._embedder is embedder
        assert engine._pipeline._embedder is embedder

    @pytest.mark.asyncio
    async def test_context_engine_embed_query_uses_embedder(self) -> None:
        """When embedder is set, _embed_query should delegate to it."""
        from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore

        embedder = HashEmbedder(dimension=64)

        engine_module = __import__(
            "sagewai.context.engine", fromlist=["ContextEngine"]
        )
        ContextEngine = engine_module.ContextEngine

        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            embedder=embedder,
        )
        vector = await engine._embed_query("test query")
        assert len(vector) == 64

    @pytest.mark.asyncio
    async def test_ingestion_pipeline_with_embedder(self) -> None:
        """IngestionPipeline._embed_chunks should use the embedder."""
        from sagewai.context.models import ChunkText
        from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore

        embedder = HashEmbedder(dimension=32)

        from sagewai.context.ingestion import IngestionPipeline

        pipeline = IngestionPipeline(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
            embedder=embedder,
        )
        chunks = [
            ChunkText(
                content="hello world",
                chunk_index=0,
                token_count=2,
                content_hash="abc123",
            ),
            ChunkText(
                content="test chunk",
                chunk_index=1,
                token_count=2,
                content_hash="def456",
            ),
        ]
        vectors = await pipeline._embed_chunks(chunks)
        assert len(vectors) == 2
        assert len(vectors[0]) == 32

    @pytest.mark.asyncio
    async def test_episode_store_with_embedder(self) -> None:
        """EpisodeStore should use the embedder for embedding."""
        embedder = HashEmbedder(dimension=64)

        from sagewai.context.episodes import EpisodeStore

        store = EpisodeStore(embedder=embedder)
        vector = await store._embed("test episode goal")
        assert len(vector) == 64


# ---------------------------------------------------------------------------
# Top-level imports
# ---------------------------------------------------------------------------


class TestPublicExports:
    """Verify that public exports are available from the top-level package."""

    def test_imports_from_sagewai(self) -> None:
        from sagewai import (
            Embedder,
            HashEmbedder,
            IntelligenceConfig,
            LiteLLMEmbedder,
            ProviderRegistry,
            SentenceTransformerEmbedder,
        )

        assert Embedder is not None
        assert HashEmbedder is not None
        assert IntelligenceConfig is not None
        assert LiteLLMEmbedder is not None
        assert ProviderRegistry is not None
        assert SentenceTransformerEmbedder is not None

    def test_imports_from_intelligence(self) -> None:
        from sagewai.intelligence import (
            Embedder,
            HashEmbedder,
            IntelligenceConfig,
            LiteLLMEmbedder,
            ProviderRegistry,
            SentenceTransformerEmbedder,
        )

        assert Embedder is not None
