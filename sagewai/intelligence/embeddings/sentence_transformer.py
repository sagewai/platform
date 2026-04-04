# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""SentenceTransformerEmbedder — local embedding using sentence-transformers.

Runs entirely on CPU with no API key required. The model is downloaded
on first use (~80 MB for ``all-MiniLM-L6-v2``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SentenceTransformerEmbedder:
    """Local embedding backend using sentence-transformers.

    Default model: ``all-MiniLM-L6-v2`` (384-dim, ~80 MB, multilingual).
    Alternative: ``all-mpnet-base-v2`` (768-dim, higher quality).

    Requires the ``sentence-transformers`` package::

        pip install sagewai[intelligence]

    Args:
        model_name: HuggingFace model identifier.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers is required for local embeddings. "
                "Install with: pip install sagewai[intelligence]"
            )
        self._model: Any = SentenceTransformer(model_name)
        self._dimension: int = self._model.get_sentence_embedding_dimension()
        logger.info(
            "Loaded sentence-transformer model=%s dim=%d",
            model_name,
            self._dimension,
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts using the local model.

        Runs inference in a background thread via ``asyncio.to_thread``
        to avoid blocking the event loop.
        """
        vectors = await asyncio.to_thread(
            self._model.encode, texts, normalize_embeddings=True
        )
        return vectors.tolist()

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query string."""
        result = await self.embed([query])
        return result[0]
