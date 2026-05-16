# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Hallucination detection guardrail.

Checks if LLM output is grounded in provided RAG context using
keyword overlap scoring. Lightweight — no additional LLM calls required.

Usage:
    guard = HallucinationGuard(threshold=0.5, action="warn")
    agent = UniversalAgent(guardrails=[guard])
"""

from __future__ import annotations

import re
from typing import Any, Literal

from sagewai.safety.guardrails import Guardrail, GuardrailResult


def _tokenize(text: str) -> set[str]:
    """Simple word tokenization for overlap scoring."""
    return set(re.findall(r"\b[a-z]+\b", text.lower()))


def _grounding_score(response: str, contexts: list[str]) -> float:
    """Calculate how well the response is grounded in context.

    Returns a score from 0.0 (no overlap) to 1.0 (fully grounded).
    Uses keyword overlap — lightweight alternative to semantic similarity.
    """
    if not contexts:
        return 1.0  # No context to check against

    response_tokens = _tokenize(response)
    if not response_tokens:
        return 1.0

    # Combine all context tokens
    context_tokens: set[str] = set()
    for ctx in contexts:
        context_tokens.update(_tokenize(ctx))

    if not context_tokens:
        return 1.0

    # Remove common stop words from both
    stop_words = {
        "the", "a", "an", "is", "was", "are", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "and",
        "but", "or", "nor", "not", "no", "so", "if", "then", "than",
        "that", "this", "these", "those", "it", "its", "i", "you",
        "he", "she", "we", "they", "me", "him", "her", "us", "them",
    }
    response_meaningful = response_tokens - stop_words
    if not response_meaningful:
        return 1.0

    overlap = response_meaningful & context_tokens
    return len(overlap) / len(response_meaningful)


class HallucinationGuard(Guardrail):
    """Detect potential hallucinations by checking response grounding.

    Uses keyword overlap between response and RAG context to estimate
    grounding. Higher threshold = stricter (more false positives).

    Args:
        threshold: Minimum grounding score (0-1). Below this triggers violation.
        action: block, warn, or escalate.
    """

    def __init__(
        self,
        *,
        threshold: float = 0.3,
        action: Literal["block", "warn", "escalate"] = "warn",
    ) -> None:
        self.threshold = threshold
        self.action: Literal["block", "warn", "escalate"] = action

    async def check_input(
        self, message: str, context: dict[str, Any]
    ) -> GuardrailResult:
        """Input check always passes — hallucination is an output concern."""
        return GuardrailResult(passed=True)

    async def check_output(
        self, response: str, context: dict[str, Any]
    ) -> GuardrailResult:
        """Check if response is grounded in RAG context."""
        rag_context = context.get("rag_context")
        if not rag_context:
            # No context to check against — pass
            return GuardrailResult(passed=True)

        score = _grounding_score(response, rag_context)
        if score >= self.threshold:
            return GuardrailResult(passed=True)

        return GuardrailResult(
            passed=False,
            violation=(
                f"Low grounding score: {score:.2f} (threshold: {self.threshold}). "
                "Response may contain hallucinated content."
            ),
            action=self.action,
        )
