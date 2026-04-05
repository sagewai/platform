# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LiteLLMEmbedder — API-based embedding via LiteLLM.

Wraps ``litellm.aembedding()`` to support OpenAI, Cohere, Azure,
and any other provider that LiteLLM supports.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class LiteLLMEmbedder:
    """API-based embedding backend using LiteLLM.

    Requires an API key for the chosen provider (e.g. ``OPENAI_API_KEY``).

    Args:
        model: LiteLLM model string (e.g. ``"text-embedding-3-small"``).
        dimension: Output vector dimension. Must match the model's output.
        batch_size: Maximum texts per API call. Defaults to 100.
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        dimension: int = 1536,
        batch_size: int = 100,
    ) -> None:
        self._model = model
        self._dimension = dimension
        self._batch_size = batch_size

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts via LiteLLM API, batching as needed."""
        import litellm

        vectors: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            response = await litellm.aembedding(model=self._model, input=batch)
            for item in response.data:
                vectors.append(item["embedding"])
        return vectors

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query string."""
        result = await self.embed([query])
        return result[0]
