# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Context compression for small model token budgets.

Implements extractive compression: scores sentences by relevance to the user
query and keeps the most relevant ones within the token budget.

Multilingual support (v2): uses :class:`UniversalSegmenter` from the
intelligence layer when available, falling back to a simple regex splitter
for environments without the optional dependency.

Semantic mode (v3): when a :class:`Summarizer` is provided, delegates
compression to embedding-based sentence scoring instead of keyword overlap.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from sagewai.directives.budget import estimate_tokens

if TYPE_CHECKING:
    from sagewai.intelligence.summarizer.protocol import Summarizer

logger = logging.getLogger(__name__)

# Lazy-initialised universal segmenter (graceful fallback)
try:
    from sagewai.intelligence.language import LanguageDetector, UniversalSegmenter

    _segmenter: UniversalSegmenter | None = UniversalSegmenter(LanguageDetector())
except Exception:  # noqa: BLE001
    _segmenter = None


def compress_text(
    text: str,
    query: str,
    target_tokens: int,
    min_sentences: int = 2,
    boost_first: float = 1.3,
    boost_last: float = 1.1,
    summarizer: Summarizer | None = None,
) -> str:
    """Compress text to fit within a token budget using extractive summarization.

    When *summarizer* is provided, delegates to its ``summarize()`` method
    (semantic / abstractive).  Otherwise falls back to keyword-overlap scoring.

    Args:
        text: The text to compress.
        query: The user's query (used for relevance scoring).
        target_tokens: Maximum tokens for the output.
        min_sentences: Minimum sentences to keep regardless of budget.
        boost_first: Score multiplier for the first sentence.
        boost_last: Score multiplier for the last sentence.
        summarizer: Optional :class:`Summarizer` for semantic compression.

    Returns:
        Compressed text, or the original if it already fits.
    """
    current_tokens = estimate_tokens(text)
    if current_tokens <= target_tokens:
        return text

    # Pluggable summarizer is async-only — use compress_text_async() instead.
    # The sync path falls through to keyword-overlap below.
    if summarizer is not None:
        import logging as _log

        _log.getLogger(__name__).warning(
            "compress_text() ignores summarizer in sync context. "
            "Use compress_text_async() for semantic summarization."
        )

    # --- Keyword-overlap fallback ---
    sentences = _split_sentences(text)
    if len(sentences) <= min_sentences:
        # Can't compress further — truncate
        return _truncate_to_tokens(text, target_tokens)

    # Score sentences by relevance to query
    query_terms = _extract_terms(query)
    scored = []
    for i, sent in enumerate(sentences):
        score = _sentence_relevance(sent, query_terms)
        # Boost first and last sentences (often most informative)
        if i == 0:
            score *= boost_first
        elif i == len(sentences) - 1:
            score *= boost_last
        scored.append((i, sent, score))

    # Sort by score (descending), keep best
    scored.sort(key=lambda x: x[2], reverse=True)

    kept: list[tuple[int, str]] = []
    tokens_used = 0
    for i, sent, score in scored:
        sent_tokens = estimate_tokens(sent)
        if tokens_used + sent_tokens > target_tokens and len(kept) >= min_sentences:
            break
        kept.append((i, sent))
        tokens_used += sent_tokens

    # Restore original order
    kept.sort(key=lambda x: x[0])
    result = " ".join(sent for _, sent in kept)

    if tokens_used < current_tokens:
        return result
    return _truncate_to_tokens(result, target_tokens)


async def compress_text_async(
    text: str,
    query: str,
    target_tokens: int,
    min_sentences: int = 2,
    boost_first: float = 1.3,
    boost_last: float = 1.1,
    summarizer: Summarizer | None = None,
) -> str:
    """Async variant of :func:`compress_text`.

    Awaits the summarizer directly when provided, avoiding the thread-pool
    workaround needed by the synchronous version.
    """
    current_tokens = estimate_tokens(text)
    if current_tokens <= target_tokens:
        return text

    if summarizer is not None:
        return await summarizer.summarize(text, query, target_tokens)

    # Fallback to keyword-overlap (sync, lightweight)
    return compress_text(
        text,
        query,
        target_tokens,
        min_sentences=min_sentences,
        boost_first=boost_first,
        boost_last=boost_last,
    )


def compress_blocks(
    contents: list[str],
    query: str,
    target_tokens: int,
    summarizer: Summarizer | None = None,
) -> list[str]:
    """Compress multiple content blocks to fit a total token budget.

    Each block gets a proportional share of the budget based on its original
    size, then is individually compressed.

    Args:
        contents: List of text blocks to compress.
        query: The user's query.
        target_tokens: Total token budget across all blocks.
        summarizer: Optional :class:`Summarizer` for semantic compression.
    """
    total_tokens = sum(estimate_tokens(c) for c in contents)
    if total_tokens <= target_tokens:
        return contents

    result = []
    for content in contents:
        proportion = estimate_tokens(content) / max(total_tokens, 1)
        block_budget = max(50, int(proportion * target_tokens))
        result.append(
            compress_text(content, query, block_budget, summarizer=summarizer)
        )

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences.

    Uses the universal segmenter (multi-language) when available,
    otherwise falls back to a simple Latin-script regex.
    """
    if _segmenter is not None:
        return _segmenter.split_sentences(text)
    # Fallback: split on .!? followed by whitespace (no uppercase requirement)
    sentences = _SENTENCE_RE.split(text.strip())
    return [s.strip() for s in sentences if s.strip()]


def _extract_terms(text: str) -> set[str]:
    """Extract lowercase terms from text, filtering stopwords."""
    _STOPWORDS = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "about", "like",
        "through", "after", "before", "between", "under", "above", "up",
        "out", "and", "but", "or", "not", "no", "if", "then", "than",
        "so", "that", "this", "it", "i", "me", "my", "we", "you", "he",
        "she", "they", "what", "how", "when", "where", "which", "who",
    }
    words = re.findall(r"\b\w+\b", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _sentence_relevance(sentence: str, query_terms: set[str]) -> float:
    """Score a sentence's relevance to a set of query terms."""
    if not query_terms:
        return 0.5  # neutral score

    sent_terms = _extract_terms(sentence)
    if not sent_terms:
        return 0.0

    overlap = len(sent_terms & query_terms)
    return overlap / len(query_terms)


def _truncate_to_tokens(text: str, target_tokens: int) -> str:
    """Hard truncate text to fit token budget."""
    char_limit = target_tokens * 4
    if len(text) <= char_limit:
        return text
    truncated = text[:char_limit].rsplit(" ", 1)[0]
    return truncated + "..." if truncated != text else truncated
