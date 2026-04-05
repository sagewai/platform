# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Lightweight query router for intelligent RAG retrieval.

Classifies incoming queries by intent (semantic, relational, or ambiguous)
and routes them to the appropriate retrieval strategy — without an LLM call.

Usage::

    from sagewai.memory.query_router import QueryRouter, QueryIntent

    router = QueryRouter()
    intent = router.classify("How are Google and Microsoft related?")
    # QueryIntent.RELATIONAL

    strategy = router.route("What is machine learning?")
    # RetrievalStrategy.VECTOR_ONLY
"""

from __future__ import annotations

import re
from collections.abc import Callable
from enum import Enum

from sagewai.memory.rag import RetrievalStrategy


class QueryIntent(str, Enum):
    """Classified intent of a retrieval query."""

    SEMANTIC = "semantic"
    RELATIONAL = "relational"
    AMBIGUOUS = "ambiguous"


# Regex patterns for relational queries (case-insensitive)
_RELATIONAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brelat(?:ed|ion|ionship)\b", re.IGNORECASE),
    re.compile(r"\bconnect(?:ed|ion|s)?\b", re.IGNORECASE),
    re.compile(r"\bbetween\s+\w+\s+and\b", re.IGNORECASE),
    re.compile(r"\blink(?:ed|s)?\s+(?:between|to)\b", re.IGNORECASE),
    re.compile(r"\bdepends?\s+on\b", re.IGNORECASE),
    re.compile(r"\bcauses?\b", re.IGNORECASE),
    re.compile(r"\bleads?\s+to\b", re.IGNORECASE),
    re.compile(r"\bneighbor(?:s|ing)?\b", re.IGNORECASE),
    re.compile(r"\badjacent\b", re.IGNORECASE),
    re.compile(r"\bupstream|downstream\b", re.IGNORECASE),
    re.compile(r"\bparent|child|sibling\b", re.IGNORECASE),
]

# Regex patterns for semantic/similarity queries (case-insensitive)
_SEMANTIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bwhat\s+is\b", re.IGNORECASE),
    re.compile(r"\bexplain\b", re.IGNORECASE),
    re.compile(r"\bdescribe\b", re.IGNORECASE),
    re.compile(r"\bsummar(?:y|ize|ise)\b", re.IGNORECASE),
    re.compile(r"\bdefin(?:e|ition)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+does\s+\w+\s+work\b", re.IGNORECASE),
    re.compile(r"\btell\s+me\s+about\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+are\b", re.IGNORECASE),
    re.compile(r"\bmeaning\s+of\b", re.IGNORECASE),
    re.compile(r"\boverview\b", re.IGNORECASE),
]

# Pattern for detecting capitalized named entities (2+ capitalized words)
_ENTITY_PATTERN = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")


class QueryRouter:
    """Routes queries to retrieval strategies based on lightweight heuristics.

    Classification pipeline (in order):
    1. Custom classifier (if provided) — takes full control
    2. Relational keyword patterns — "related to", "connected", "between X and Y"
    3. Semantic keyword patterns — "what is", "explain", "summarize"
    4. Entity density — 2+ named entities → relational
    5. Default → ambiguous (hybrid)

    Parameters
    ----------
    custom_classifier:
        Optional callable that overrides the built-in classification.
        Signature: ``(query: str) -> QueryIntent``.
    """

    def __init__(
        self,
        custom_classifier: Callable[[str], QueryIntent] | None = None,
    ) -> None:
        self._custom_classifier = custom_classifier

    def classify(self, query: str) -> QueryIntent:
        """Classify a query's retrieval intent.

        Returns:
            The detected intent: SEMANTIC, RELATIONAL, or AMBIGUOUS.
        """
        if self._custom_classifier is not None:
            return self._custom_classifier(query)

        # Check relational patterns first (more specific)
        for pattern in _RELATIONAL_PATTERNS:
            if pattern.search(query):
                return QueryIntent.RELATIONAL

        # Check semantic patterns
        for pattern in _SEMANTIC_PATTERNS:
            if pattern.search(query):
                return QueryIntent.SEMANTIC

        # Check entity density: 2+ named entities → relational
        entities = _ENTITY_PATTERN.findall(query)
        if len(entities) >= 2:
            return QueryIntent.RELATIONAL

        return QueryIntent.AMBIGUOUS

    def route(self, query: str) -> RetrievalStrategy:
        """Classify a query and return the appropriate retrieval strategy.

        Returns:
            The strategy to use for this query.
        """
        intent = self.classify(query)
        return _INTENT_TO_STRATEGY[intent]


_INTENT_TO_STRATEGY: dict[QueryIntent, RetrievalStrategy] = {
    QueryIntent.SEMANTIC: RetrievalStrategy.VECTOR_ONLY,
    QueryIntent.RELATIONAL: RetrievalStrategy.GRAPH_ONLY,
    QueryIntent.AMBIGUOUS: RetrievalStrategy.HYBRID,
}
