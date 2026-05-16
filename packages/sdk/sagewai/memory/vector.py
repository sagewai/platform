# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Vector memory — embedding-based similarity search.

Provides an in-memory vector store that implements the MemoryProvider protocol.
Uses cosine similarity over simple TF-IDF-style embeddings by default.

For production, swap with a Milvus-backed implementation.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from sagewai.core.context import resolve_project_id


class VectorMemory:
    """In-memory vector store using TF-IDF cosine similarity.

    Stores text chunks with metadata and retrieves the most similar
    entries for a given query.  All data is scoped by ``project_id``.

    Args:
        similarity_threshold: Minimum similarity score (0-1) to include in results.
        project_id: Explicit project scope.  ``None`` → auto-resolve from
            the active ``ProjectContext`` contextvar, falling back to ``"default"``.
    """

    def __init__(
        self,
        *,
        similarity_threshold: float = 0.0,
        project_id: str | None = None,
    ) -> None:
        # project_id → [_VectorEntry, ...]
        self._entries: dict[str, list[_VectorEntry]] = {}
        self._similarity_threshold = similarity_threshold
        self._project_id = project_id

    def _resolve_pid(self) -> str:
        """Resolve the effective project_id for this operation."""
        return resolve_project_id(self._project_id)

    def _project_entries(self, pid: str) -> list[_VectorEntry]:
        return self._entries.setdefault(pid, [])

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        """Retrieve entries most similar to the query.

        Args:
            query: Search query text.
            top_k: Maximum number of results.

        Returns:
            List of content strings sorted by descending similarity.
        """
        pid = self._resolve_pid()
        entries = self._project_entries(pid)
        if not entries:
            return []

        query_vec = self._tokenize(query)
        scored = []
        for entry in entries:
            score = self._cosine_similarity(query_vec, entry.vector)
            if score > self._similarity_threshold:
                scored.append((score, entry.content))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [content for _, content in scored[:top_k]]

    async def store(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        """Store a text entry with its vector representation.

        Args:
            content: Text content to store.
            metadata: Optional metadata dict.
        """
        pid = self._resolve_pid()
        vector = self._tokenize(content)
        self._project_entries(pid).append(
            _VectorEntry(content=content, vector=vector, metadata=metadata or {})
        )

    async def delete(self, content: str) -> bool:
        """Remove entries matching the given content.

        Returns:
            True if any entries were removed.
        """
        pid = self._resolve_pid()
        entries = self._project_entries(pid)
        initial = len(entries)
        self._entries[pid] = [e for e in entries if e.content != content]
        return len(self._entries[pid]) < initial

    def clear(self) -> None:
        """Remove all entries."""
        self._entries.clear()

    def __len__(self) -> int:
        pid = self._resolve_pid()
        return len(self._project_entries(pid))

    # ------------------------------------------------------------------
    # Internal: simple TF-IDF-style vectorization
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> dict[str, float]:
        """Convert text to a token frequency vector (bag of words)."""
        tokens = re.findall(r"\w+", text.lower())
        counts = Counter(tokens)
        total = len(tokens) or 1
        return {token: count / total for token, count in counts.items()}

    @staticmethod
    def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
        """Compute cosine similarity between two sparse vectors."""
        common_keys = set(a) & set(b)
        if not common_keys:
            return 0.0

        dot = sum(a[k] * b[k] for k in common_keys)
        norm_a = math.sqrt(sum(v * v for v in a.values()))
        norm_b = math.sqrt(sum(v * v for v in b.values()))

        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


class _VectorEntry:
    """Internal storage for a vector entry."""

    __slots__ = ("content", "vector", "metadata")

    def __init__(self, content: str, vector: dict[str, float], metadata: dict[str, Any]) -> None:
        self.content = content
        self.vector = vector
        self.metadata = metadata
