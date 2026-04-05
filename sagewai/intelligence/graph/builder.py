# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""ConversationGraphBuilder — build a knowledge graph from conversation turns.

Deterministic: same conversation always produces the same graph.
Incremental: processes only new messages since last build.
Uses I3 extractors (GLiNER or LLM) for entity/relation extraction.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from sagewai.intelligence.extractors.protocol import (
    EntityExtractor,
    RelationExtractor,
)
from sagewai.intelligence.models import ExtractionResult, RelationTriple

logger = logging.getLogger(__name__)

# Minimum content length to attempt extraction (skip greetings, etc.)
_MIN_CONTENT_LENGTH = 10


class GraphBuildResult(BaseModel):
    """Result of a graph building operation.

    Attributes:
        entities_found: Total entities extracted (before dedup).
        entities_unique: Unique entities after dedup.
        relations_found: Total relations extracted.
        messages_processed: Number of new messages processed.
    """

    entities_found: int = 0
    entities_unique: int = 0
    relations_found: int = 0
    messages_processed: int = 0


class ConversationGraphBuilder:
    """Build a knowledge graph incrementally from conversation turns.

    Deterministic: same conversation always produces the same graph.
    Incremental: processes only new messages since last ``process_messages``
    call (tracked via ``_processed_count``).

    Args:
        entity_extractor: Backend for named entity recognition.
        relation_extractor: Backend for relation triple extraction.
        graph_store: Optional graph backend (``GraphMemory`` or
            ``NebulaGraphMemory``).  When provided, extracted entities and
            relations are persisted automatically.
    """

    def __init__(
        self,
        entity_extractor: EntityExtractor,
        relation_extractor: RelationExtractor,
        graph_store: Any | None = None,
    ) -> None:
        self._ner = entity_extractor
        self._rel = relation_extractor
        self._graph = graph_store
        self._processed_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_messages(
        self,
        messages: list[dict[str, str]],
        start_from: int | None = None,
    ) -> GraphBuildResult:
        """Process conversation messages into graph triples.

        Args:
            messages: List of ``{"role": str, "content": str}`` dicts.
            start_from: Message index to start from (for incremental).
                ``None`` means continue from where we left off.

        Returns:
            :class:`GraphBuildResult` with extraction statistics.
        """
        start = start_from if start_from is not None else self._processed_count
        new_messages = messages[start:]

        all_entities: list[ExtractionResult] = []
        all_relations: list[RelationTriple] = []

        for msg in new_messages:
            text = msg.get("content", "")
            if not text or len(text) < _MIN_CONTENT_LENGTH:
                continue

            entities = await self._ner.extract(text)
            all_entities.extend(entities)

            relations = await self._rel.extract(text)
            all_relations.extend(relations)

        unique_entities = self._deduplicate_entities(all_entities)

        if self._graph:
            await self._store_in_graph(unique_entities, all_relations)

        self._processed_count = len(messages)

        return GraphBuildResult(
            entities_found=len(all_entities),
            entities_unique=len(unique_entities),
            relations_found=len(all_relations),
            messages_processed=len(new_messages),
        )

    def reset(self) -> None:
        """Reset processing state so the next call reprocesses from the start."""
        self._processed_count = 0

    @property
    def processed_count(self) -> int:
        """Number of messages processed so far."""
        return self._processed_count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate_entities(
        entities: list[ExtractionResult],
    ) -> list[ExtractionResult]:
        """Merge entities with the same normalized name, keep highest confidence."""
        seen: dict[str, ExtractionResult] = {}
        for e in entities:
            key = e.text.lower().strip()
            if key not in seen or e.confidence > seen[key].confidence:
                seen[key] = e
        return list(seen.values())

    async def _store_in_graph(
        self,
        entities: list[ExtractionResult],
        relations: list[RelationTriple],
    ) -> None:
        """Persist extracted entities and relations in the graph backend."""
        # Store entities
        if hasattr(self._graph, "store"):
            for ent in entities:
                await self._graph.store(
                    ent.text,
                    {"label": ent.label, "confidence": ent.confidence},
                )

        # Store relations
        if hasattr(self._graph, "add_relation"):
            for rel in relations:
                await self._graph.add_relation(
                    rel.subject, rel.predicate, rel.object
                )
