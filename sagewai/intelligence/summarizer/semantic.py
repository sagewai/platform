# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Semantic extractive summarizer — embedding-based sentence scoring.

Scores sentences by cosine similarity of their embedding to the query
embedding, then keeps top-scoring sentences in original order until the
token budget is reached.  Falls back to a keyword-overlap scorer when
no embedder is available.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from sagewai.directives.budget import estimate_tokens

if TYPE_CHECKING:
    from sagewai.intelligence.embeddings.protocol import Embedder

logger = logging.getLogger(__name__)

# Lazy-initialised segmenter (graceful fallback to regex)
try:
    from sagewai.intelligence.language import LanguageDetector, UniversalSegmenter

    _segmenter: UniversalSegmenter | None = UniversalSegmenter(LanguageDetector())
except Exception:  # noqa: BLE001
    _segmenter = None

import re

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using the best available backend."""
    if _segmenter is not None:
        return _segmenter.split_sentences(text)
    sentences = _SENTENCE_RE.split(text.strip())
    return [s.strip() for s in sentences if s.strip()]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    For normalised vectors this is just the dot product, but we handle
    the general case for safety.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _truncate_to_tokens(text: str, target_tokens: int) -> str:
    """Hard-truncate text to fit a token budget."""
    char_limit = target_tokens * 4
    if len(text) <= char_limit:
        return text
    truncated = text[:char_limit].rsplit(" ", 1)[0]
    return truncated + "..." if truncated != text else truncated


class SemanticSummarizer:
    """Score sentences by embedding similarity to query, keep top-scoring.

    Uses batch embedding for efficiency and preserves original sentence
    order in the output.

    Args:
        embedder: An :class:`Embedder` instance.  When ``None``, the
            registry auto-detects the best available backend at
            summarization time.
        boost_first: Score multiplier for the first sentence.
        boost_last: Score multiplier for the last sentence.
        min_sentences: Minimum sentences to keep regardless of budget.
    """

    def __init__(
        self,
        embedder: Embedder | None = None,
        *,
        boost_first: float = 1.2,
        boost_last: float = 1.1,
        min_sentences: int = 2,
    ) -> None:
        self._embedder = embedder
        self._boost_first = boost_first
        self._boost_last = boost_last
        self._min_sentences = min_sentences

    async def summarize(self, text: str, query: str, max_tokens: int) -> str:
        """Summarize *text*, keeping sentences most similar to *query*.

        Steps:
        1. Split text into sentences.
        2. Embed query + all sentences in a single batch call.
        3. Score each sentence by cosine similarity to query embedding.
        4. Apply positional boosts (first / last).
        5. Keep top-scoring sentences in original order within budget.
        """
        if estimate_tokens(text) <= max_tokens:
            return text

        sentences = _split_sentences(text)
        if not sentences:
            return text

        if len(sentences) <= self._min_sentences:
            return _truncate_to_tokens(text, max_tokens)

        # Resolve embedder lazily if not provided
        embedder = self._embedder
        if embedder is None:
            from sagewai.intelligence.registry import ProviderRegistry

            embedder = ProviderRegistry.get_embedder()

        # Batch embed: query + all sentences in one call
        all_texts = [query] + sentences
        all_vectors = await embedder.embed(all_texts)
        query_vec = all_vectors[0]
        sentence_vecs = all_vectors[1:]

        # Score each sentence
        scored: list[tuple[int, str, float]] = []
        for i, (sent, vec) in enumerate(zip(sentences, sentence_vecs)):
            score = cosine_similarity(query_vec, vec)
            if i == 0:
                score *= self._boost_first
            elif i == len(sentences) - 1:
                score *= self._boost_last
            scored.append((i, sent, score))

        # Sort by score descending
        scored.sort(key=lambda x: x[2], reverse=True)

        # Keep top-scoring within budget, preserving min_sentences
        kept: list[tuple[int, str]] = []
        tokens_used = 0
        for idx, sent, _score in scored:
            sent_tokens = estimate_tokens(sent)
            if (
                tokens_used + sent_tokens > max_tokens
                and len(kept) >= self._min_sentences
            ):
                break
            kept.append((idx, sent))
            tokens_used += sent_tokens

        # Restore original order
        kept.sort(key=lambda x: x[0])
        result = " ".join(sent for _, sent in kept)

        logger.debug(
            "Semantic summarizer: %d → %d sentences, ~%d tokens",
            len(sentences),
            len(kept),
            tokens_used,
        )
        return result
