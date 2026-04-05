# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Summarizer protocol — pluggable summarization backend interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Summarizer(Protocol):
    """Protocol for summarization backends.

    Implementations must provide ``summarize`` which compresses text
    while preserving content relevant to a query.

    The SDK ships two backends:

    * **SemanticSummarizer** — embedding-based sentence scoring (no LLM)
    * **BARTSummarizer** — abstractive via ``facebook/bart-large-cnn``
      (requires ``transformers`` + ``torch``)
    """

    async def summarize(self, text: str, query: str, max_tokens: int) -> str:
        """Summarize *text*, prioritizing content relevant to *query*.

        Args:
            text: The text to summarize.
            query: User query for relevance scoring.
            max_tokens: Approximate token budget for the output.

        Returns:
            Summarized text fitting within *max_tokens*.
        """
        ...
