# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the ChunkManager — chunking strategies, token counting, overlap."""

import pytest

from sagewai.context.chunking import ChunkManager, _content_hash, _count_tokens
from sagewai.context.models import ChunkingConfig


class TestTokenCounting:
    def test_count_tokens_nonempty(self):
        count = _count_tokens("Hello, world!")
        assert count > 0

    def test_count_tokens_empty(self):
        count = _count_tokens("")
        assert count == 0 or count == 1  # depends on fallback

    def test_count_tokens_long_text(self):
        text = "word " * 1000
        count = _count_tokens(text)
        assert 500 < count < 2000  # reasonable range


class TestContentHash:
    def test_deterministic(self):
        h1 = _content_hash("test content")
        h2 = _content_hash("test content")
        assert h1 == h2

    def test_different_content(self):
        h1 = _content_hash("content A")
        h2 = _content_hash("content B")
        assert h1 != h2

    def test_sha256_format(self):
        h = _content_hash("hello")
        assert len(h) == 64  # SHA-256 hex digest


class TestChunkManagerFixed:
    def test_short_text_single_chunk(self):
        mgr = ChunkManager(ChunkingConfig(strategy="fixed", chunk_size=800))
        chunks = mgr.chunk("Short text.")
        assert len(chunks) == 1
        assert chunks[0].content == "Short text."

    def test_empty_text(self):
        mgr = ChunkManager(ChunkingConfig(strategy="fixed"))
        chunks = mgr.chunk("")
        assert len(chunks) == 0

    def test_whitespace_only(self):
        mgr = ChunkManager(ChunkingConfig(strategy="fixed"))
        chunks = mgr.chunk("   \n\n  ")
        assert len(chunks) == 0

    def test_long_text_multiple_chunks(self):
        mgr = ChunkManager(ChunkingConfig(strategy="fixed", chunk_size=10, chunk_overlap=0))
        text = "word " * 200  # ~200 tokens
        chunks = mgr.chunk(text)
        assert len(chunks) > 1

    def test_chunk_metadata_propagated(self):
        mgr = ChunkManager(ChunkingConfig(strategy="fixed", chunk_size=800))
        chunks = mgr.chunk("Text", metadata={"source": "test.md"})
        assert chunks[0].metadata["source"] == "test.md"

    def test_chunk_index_sequential(self):
        mgr = ChunkManager(ChunkingConfig(strategy="fixed", chunk_size=10, chunk_overlap=0))
        text = "word " * 100
        chunks = mgr.chunk(text)
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i


class TestChunkManagerRecursive:
    def test_short_text(self):
        mgr = ChunkManager(ChunkingConfig(strategy="recursive", chunk_size=800))
        chunks = mgr.chunk("Short paragraph.")
        assert len(chunks) == 1

    def test_splits_on_paragraphs(self):
        para1 = "First paragraph. " * 50
        para2 = "Second paragraph. " * 50
        text = para1 + "\n\n" + para2
        mgr = ChunkManager(ChunkingConfig(strategy="recursive", chunk_size=100, chunk_overlap=0))
        chunks = mgr.chunk(text)
        assert len(chunks) >= 2

    def test_falls_back_to_sentences(self):
        text = "Sentence one. Sentence two. Sentence three. " * 50
        mgr = ChunkManager(ChunkingConfig(strategy="recursive", chunk_size=50, chunk_overlap=0))
        chunks = mgr.chunk(text)
        assert len(chunks) >= 2

    def test_content_hash_unique_per_chunk(self):
        text = "A" * 200 + "\n\n" + "B" * 200
        mgr = ChunkManager(ChunkingConfig(strategy="recursive", chunk_size=50, chunk_overlap=0))
        chunks = mgr.chunk(text)
        hashes = {c.content_hash for c in chunks}
        assert len(hashes) == len(chunks)

    def test_token_count_populated(self):
        mgr = ChunkManager(ChunkingConfig(strategy="recursive"))
        chunks = mgr.chunk("Hello world, this is a test sentence.")
        assert all(c.token_count > 0 for c in chunks)


class TestChunkManagerDefaults:
    def test_default_config(self):
        mgr = ChunkManager()
        assert mgr.config.strategy == "recursive"
        assert mgr.config.chunk_size == 800
        assert mgr.config.chunk_overlap == 200
