# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Summarizer sub-package — semantic and abstractive summarization.

Phase I7: Semantic summarization replacing keyword-overlap compression.
"""

from sagewai.intelligence.summarizer.protocol import Summarizer
from sagewai.intelligence.summarizer.semantic import (
    SemanticSummarizer,
    cosine_similarity,
)

__all__ = [
    "Summarizer",
    "SemanticSummarizer",
    "cosine_similarity",
]
