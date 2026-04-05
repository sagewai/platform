# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Embedder protocol — pluggable embedding backend interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Protocol for embedding backends.

    Implementations must provide ``dimension``, ``embed``, and ``embed_query``.
    The SDK ships three backends:

    * **SentenceTransformerEmbedder** — local, no API key, CPU-friendly
    * **LiteLLMEmbedder** — API-based (OpenAI, Cohere, etc.)
    * **HashEmbedder** — deterministic hash fallback (zero deps, low quality)
    """

    @property
    def dimension(self) -> int:
        """Embedding vector dimension (e.g., 384 for MiniLM, 1536 for OpenAI)."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts. Returns a list of float vectors."""
        ...

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query string.

        May use a different model or prefix for asymmetric retrieval.
        """
        ...
