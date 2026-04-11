# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Abstractive summarization using pre-trained transformer models.

Requires ``transformers`` and ``torch`` (~500 MB download on first use).
Install with::

    pip install sagewai[intelligence-full]

The default model (``facebook/bart-large-cnn``) produces high-quality
abstractive summaries but requires significant memory (~1.6 GB).  For
lighter-weight summarization, prefer :class:`SemanticSummarizer`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class BARTSummarizer:
    """Abstractive summarization via ``facebook/bart-large-cnn``.

    The model is loaded lazily on first call to :meth:`summarize`.

    Args:
        model_name: HuggingFace model identifier.  Defaults to
            ``facebook/bart-large-cnn``.

    Raises:
        ImportError: If ``transformers`` is not installed.
    """

    def __init__(self, model_name: str = "facebook/bart-large-cnn") -> None:
        try:
            from transformers import pipeline  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "transformers is required for BARTSummarizer. "
                "Install with: pip install sagewai[intelligence-full]"
            ) from exc
        self._model_name = model_name
        self._pipeline: Any | None = None

    def _ensure_pipeline(self) -> Any:
        """Lazily initialise the HuggingFace summarization pipeline."""
        if self._pipeline is None:
            from transformers import pipeline

            logger.info("Loading summarization model: %s", self._model_name)
            self._pipeline = pipeline("summarization", model=self._model_name)
        return self._pipeline

    async def summarize(self, text: str, query: str, max_tokens: int) -> str:
        """Generate an abstractive summary of *text*.

        The *query* parameter is accepted for protocol compatibility but
        is not used by the BART pipeline (it summarises the full input).

        Args:
            text: Text to summarise.
            query: Ignored (kept for ``Summarizer`` protocol compliance).
            max_tokens: Maximum output length in tokens.

        Returns:
            Abstractive summary string.
        """
        pipe = self._ensure_pipeline()
        result = await asyncio.to_thread(
            pipe,
            text,
            max_length=max_tokens,
            min_length=min(30, max_tokens),
            do_sample=False,
        )
        return result[0]["summary_text"]
