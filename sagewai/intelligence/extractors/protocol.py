# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Protocols for entity, relation, and fact extraction backends."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sagewai.intelligence.models import ExtractedFact, ExtractionResult, RelationTriple


@runtime_checkable
class EntityExtractor(Protocol):
    """Extract named entities from text."""

    async def extract(
        self,
        text: str,
        entity_types: list[str] | None = None,
    ) -> list[ExtractionResult]:
        """Extract entities from *text*.

        Args:
            text: Source text.
            entity_types: Optional list of entity type labels to extract.
                When ``None``, the backend uses its default set.

        Returns:
            Extraction results with character offsets and confidence.
        """
        ...


@runtime_checkable
class RelationExtractor(Protocol):
    """Extract subject-predicate-object triples from text."""

    async def extract(self, text: str) -> list[RelationTriple]:
        """Extract relation triples from *text*.

        Returns:
            A list of :class:`RelationTriple` instances.
        """
        ...


@runtime_checkable
class FactExtractor(Protocol):
    """Protocol for fact extraction backends.

    Implementations extract structured facts from conversation text.
    The SDK ships three backends:

    * **RuleBasedFactExtractor** — pattern-matching, no LLM needed
    * **LLMFactExtractor** — LLM-powered extraction (existing pattern)
    * **HybridFactExtractor** — rules first, LLM fills gaps
    """

    async def extract(self, conversation: str) -> list[ExtractedFact]:
        """Extract facts from conversation text.

        Args:
            conversation: Raw conversation text (multi-line, with role prefixes).

        Returns:
            List of extracted facts with type, confidence, and entities.
        """
        ...
