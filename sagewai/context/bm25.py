# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""BM25 keyword search for context chunks.

Provides keyword-based retrieval to complement vector semantic search.
Uses in-memory BM25 scoring for dev, can be backed by Postgres ts_vector.

Multilingual tokenization (v2): uses :class:`UniversalSegmenter` when
available for proper CJK bigram and Korean syllable tokenization.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

# Lazy-initialised universal segmenter for multilingual tokenization
try:
    from sagewai.intelligence.language import LanguageDetector, UniversalSegmenter

    _segmenter: UniversalSegmenter | None = UniversalSegmenter(LanguageDetector())
except Exception:  # noqa: BLE001
    _segmenter = None


def _tokenize(text: str) -> list[str]:
    """Tokenize text for BM25 scoring.

    Uses the universal segmenter (CJK bigrams, Korean syllables, etc.)
    when available; otherwise falls back to simple ``\\w+`` splitting.
    """
    if _segmenter is not None:
        return _segmenter.tokenize(text)
    return [w for w in re.findall(r"\w+", text.lower()) if len(w) > 1]


class BM25Index:
    """Thread-safe in-memory BM25 index over document chunks.

    All mutations (``add``/``remove``) are guarded by a lock so that
    concurrent ``search`` calls on an asyncio event loop are safe.

    Usage::

        idx = BM25Index()
        idx.add("chunk-1", "The quick brown fox")
        idx.add("chunk-2", "A lazy dog sleeps")
        results = idx.search("brown fox", top_k=5)
        # → [("chunk-1", 1.23)]
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._lock = threading.Lock()
        self._docs: dict[str, list[str]] = {}  # chunk_id → tokens
        self._doc_lens: dict[str, int] = {}
        self._df: Counter[str] = Counter()  # document frequency per term
        self._total_dl: int = 0  # running total of all doc lengths
        self._avg_dl: float = 0.0
        self._n: int = 0

    def add(self, chunk_id: str, text: str) -> None:
        """Add a document to the index (thread-safe)."""
        tokens = _tokenize(text)
        with self._lock:
            self._docs[chunk_id] = tokens
            self._doc_lens[chunk_id] = len(tokens)
            self._n += 1
            self._total_dl += len(tokens)

            for term in set(tokens):
                self._df[term] += 1

            self._avg_dl = self._total_dl / max(self._n, 1)

    def remove(self, chunk_id: str) -> None:
        """Remove a document from the index (thread-safe)."""
        with self._lock:
            if chunk_id not in self._docs:
                return
            tokens = self._docs[chunk_id]
            for term in set(tokens):
                self._df[term] = max(0, self._df[term] - 1)
            self._total_dl -= self._doc_lens[chunk_id]
            del self._docs[chunk_id]
            del self._doc_lens[chunk_id]
            self._n = max(0, self._n - 1)
            self._avg_dl = self._total_dl / max(self._n, 1) if self._n else 0.0

    def search(
        self,
        query: str,
        top_k: int = 10,
        chunk_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Search the index (thread-safe read via snapshot).

        Parameters
        ----------
        query:
            Search query string.
        top_k:
            Maximum results to return.
        chunk_ids:
            If provided, only search within these chunk IDs (scope filtering).
        """
        # Snapshot under lock to avoid races with concurrent add/remove
        with self._lock:
            if not self._docs:
                return []
            docs_snapshot = dict(self._docs)
            lens_snapshot = dict(self._doc_lens)
            df_snapshot = Counter(self._df)
            n = self._n
            avg_dl = self._avg_dl

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores: list[tuple[str, float]] = []

        for doc_id, doc_tokens in docs_snapshot.items():
            if chunk_ids is not None and doc_id not in chunk_ids:
                continue

            score = 0.0
            dl = lens_snapshot[doc_id]
            tf_counter = Counter(doc_tokens)

            for term in query_tokens:
                if term not in df_snapshot or df_snapshot[term] == 0:
                    continue

                tf = tf_counter.get(term, 0)
                if tf == 0:
                    continue

                # IDF component
                idf = math.log((n - df_snapshot[term] + 0.5) / (df_snapshot[term] + 0.5) + 1)

                # TF component with length normalization
                tf_norm = (tf * (self.k1 + 1)) / (
                    tf + self.k1 * (1 - self.b + self.b * dl / max(avg_dl, 1))
                )

                score += idf * tf_norm

            if score > 0:
                scores.append((doc_id, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def __len__(self) -> int:
        with self._lock:
            return self._n
