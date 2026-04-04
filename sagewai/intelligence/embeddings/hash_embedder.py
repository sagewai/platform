# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""HashEmbedder — deterministic hash-based pseudo-embeddings.

Emergency fallback that works with zero dependencies. Low quality but
guaranteed to produce consistent vectors for the same input text.
"""

from __future__ import annotations

import hashlib


class HashEmbedder:
    """Deterministic hash-based pseudo-embedding backend.

    Produces fixed-dimension vectors by hashing the input text with SHA-256
    and mapping bytes to floats in ``[-1, 1]``. Same text always yields
    the same vector, but semantic similarity is **not** preserved.

    Use as a last-resort fallback when neither a local model nor an API
    key is available.

    Args:
        dimension: Output vector dimension. Defaults to 384 to match the
            local ``all-MiniLM-L6-v2`` default.
    """

    def __init__(self, dimension: int = 384) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts using deterministic hashing."""
        return [self._hash_vector(t) for t in texts]

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query string."""
        return self._hash_vector(query)

    def _hash_vector(self, text: str) -> list[float]:
        """Generate a deterministic pseudo-vector from text hash."""
        h = hashlib.sha256(text.encode("utf-8")).digest()
        extended = h * ((self._dimension * 4 // len(h)) + 1)
        values: list[float] = []
        for i in range(self._dimension):
            byte_val = extended[i % len(extended)]
            values.append((byte_val / 255.0) * 2 - 1)
        return values
