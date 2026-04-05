# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Re-ranking for context search results.

Provides cross-encoder re-ranking to refine initial retrieval results.
Optional — falls back to NoopReranker when no re-ranking model is configured.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from sagewai.context.ingestion import _FALLBACK_ERRORS

logger = logging.getLogger(__name__)


@runtime_checkable
class Reranker(Protocol):
    """Protocol for search result re-ranking."""

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 10,
    ) -> list[tuple[int, float]]:
        """Re-rank documents by relevance to query.

        Returns (original_index, score) pairs sorted by relevance descending.
        """
        ...


class NoopReranker:
    """Passthrough reranker — preserves original ordering."""

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 10,
    ) -> list[tuple[int, float]]:
        return [(i, 1.0 - i * 0.001) for i in range(min(len(documents), top_k))]


class CrossEncoderReranker:
    """Re-rank using litellm.arerank() (Cohere, Voyage, etc.).

    Usage::

        reranker = CrossEncoderReranker(model="cohere/rerank-english-v3.0")
        results = await reranker.rerank("query", ["doc1", "doc2"], top_k=5)
    """

    def __init__(self, model: str = "cohere/rerank-english-v3.0") -> None:
        self.model = model

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 10,
    ) -> list[tuple[int, float]]:
        if not documents:
            return []

        try:
            import litellm

            response = await litellm.arerank(
                model=self.model,
                query=query,
                documents=documents,
                top_n=min(top_k, len(documents)),
            )

            results: list[tuple[int, float]] = []
            for item in response.results:
                results.append((item.index, item.relevance_score))

            results.sort(key=lambda x: x[1], reverse=True)
            return results[:top_k]

        except _FALLBACK_ERRORS:
            logger.info("Re-ranking unavailable, using original ordering")
            return [(i, 1.0 - i * 0.001) for i in range(min(len(documents), top_k))]


def reciprocal_rank_fusion(
    result_sets: list[list[tuple[str, float]]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Merge multiple result sets using Reciprocal Rank Fusion.

    Parameters
    ----------
    result_sets:
        List of result sets, each being a list of (chunk_id, score) pairs.
    k:
        RRF constant (default 60, standard in literature).

    Returns
    -------
    Merged (chunk_id, rrf_score) pairs sorted by score descending.
    """
    scores: dict[str, float] = {}

    for results in result_sets:
        for rank, (chunk_id, _) in enumerate(results):
            rrf = 1.0 / (k + rank + 1)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + rrf

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return merged
