# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Deduplication for context chunks — hash-based and semantic similarity."""

from __future__ import annotations

import logging
import math
from typing import Any

from sagewai.context.models import ChunkText

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class Deduplicator:
    """Remove duplicate chunks by content hash and optional cosine similarity.

    Usage::

        dedup = Deduplicator(existing_hashes={"abc123..."})
        unique = dedup.filter(chunks)

        # With semantic dedup (requires embeddings):
        unique = await dedup.filter_with_similarity(chunks, existing_embeddings)
    """

    def __init__(
        self,
        existing_hashes: set[str] | None = None,
        similarity_threshold: float = 0.95,
    ) -> None:
        self.existing_hashes = existing_hashes or set()
        self.similarity_threshold = similarity_threshold

    def filter(self, chunks: list[ChunkText]) -> list[ChunkText]:
        """Remove chunks whose content_hash already exists.

        Checks against both ``existing_hashes`` (from the store) and hashes
        seen within this batch.
        """
        seen: set[str] = set(self.existing_hashes)
        unique: list[ChunkText] = []

        for chunk in chunks:
            if chunk.content_hash in seen:
                logger.debug("Duplicate chunk (hash=%s), skipping", chunk.content_hash[:12])
                continue
            seen.add(chunk.content_hash)
            unique.append(chunk)

        if len(unique) < len(chunks):
            logger.info(
                "Deduplication: %d → %d chunks (%d duplicates removed)",
                len(chunks),
                len(unique),
                len(chunks) - len(unique),
            )
        return unique

    async def filter_with_similarity(
        self,
        chunks: list[ChunkText],
        new_embeddings: list[list[float]] | None = None,
        existing_embeddings: list[tuple[str, list[float]]] | None = None,
    ) -> list[ChunkText]:
        """Filter by hash first, then by cosine similarity.

        Parameters
        ----------
        chunks:
            New chunks to deduplicate.
        new_embeddings:
            Embedding vectors for the new chunks (same order as ``chunks``).
        existing_embeddings:
            List of (content_hash, embedding_vector) pairs from the store.
        """
        # Phase 1: hash-based
        unique = self.filter(chunks)

        if not existing_embeddings or not new_embeddings or not unique:
            return unique

        # Build index mapping: which unique chunks have embeddings?
        # After hash dedup, unique is a subset of chunks. Map back to embeddings.
        chunk_index = {c.content_hash: i for i, c in enumerate(chunks)}
        original_indices = [chunk_index[u.content_hash] for u in unique]

        # Phase 2: semantic similarity check
        kept: list[ChunkText] = []
        removed = 0

        for idx, chunk in zip(original_indices, unique):
            if idx >= len(new_embeddings):
                kept.append(chunk)
                continue

            new_vec = new_embeddings[idx]
            is_duplicate = False

            for _hash, existing_vec in existing_embeddings:
                if _hash == chunk.content_hash:
                    continue  # same hash, already handled
                sim = _cosine_similarity(new_vec, existing_vec)
                if sim >= self.similarity_threshold:
                    logger.debug(
                        "Semantic duplicate (sim=%.3f, hash=%s), skipping",
                        sim,
                        chunk.content_hash[:12],
                    )
                    is_duplicate = True
                    removed += 1
                    break

            if not is_duplicate:
                kept.append(chunk)

        if removed > 0:
            logger.info(
                "Semantic dedup: %d → %d chunks (%d near-duplicates removed)",
                len(unique),
                len(kept),
                removed,
            )
        return kept
