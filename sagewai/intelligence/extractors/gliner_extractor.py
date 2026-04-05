# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""GLiNER-based entity extraction and heuristic relation extraction.

GLiNER is a zero-shot NER model (~50 MB, CPU-friendly, deterministic)
that replaces non-deterministic LLM-based extraction for graph building.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from sagewai.intelligence.models import ExtractionResult, RelationTriple

if TYPE_CHECKING:
    from sagewai.intelligence.extractors.protocol import EntityExtractor

logger = logging.getLogger(__name__)

# Sentence boundary for heuristic splitting (Latin script fallback).
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class GLiNEREntityExtractor:
    """Zero-shot NER using GLiNER (deterministic, CPU-only).

    Downloads the model on first use, not at import time.  The underlying
    ``gliner`` package is optional — install with::

        pip install sagewai[intelligence]

    Args:
        model_name: HuggingFace model ID.  The default
            ``urchade/gliner_medium-v2.1`` provides a good balance of speed
            and accuracy for English text.
        threshold: Minimum confidence for an entity to be included.
    """

    DEFAULT_ENTITY_TYPES: list[str] = [
        "person",
        "organization",
        "location",
        "technology",
        "product",
        "event",
        "date",
    ]

    def __init__(
        self,
        model_name: str = "urchade/gliner_medium-v2.1",
        threshold: float = 0.5,
    ) -> None:
        try:
            from gliner import GLiNER  # noqa: F401 — import check only
        except ImportError:
            raise ImportError(
                "gliner is required for GLiNEREntityExtractor. "
                "Install with: pip install sagewai[intelligence]"
            )
        self._model_name = model_name
        self._threshold = threshold
        self._model: Any | None = None

    def _ensure_model(self) -> Any:
        """Lazily load the GLiNER model on first prediction."""
        if self._model is None:
            from gliner import GLiNER

            self._model = GLiNER.from_pretrained(self._model_name)
        return self._model

    async def extract(
        self,
        text: str,
        entity_types: list[str] | None = None,
    ) -> list[ExtractionResult]:
        """Extract named entities using GLiNER.

        Runs the synchronous GLiNER prediction in a thread to avoid
        blocking the event loop.

        Args:
            text: Source text.
            entity_types: Entity type labels. Uses :attr:`DEFAULT_ENTITY_TYPES`
                when ``None``.

        Returns:
            List of :class:`ExtractionResult` with character offsets and
            confidence scores.
        """
        if not text.strip():
            return []

        types = entity_types or self.DEFAULT_ENTITY_TYPES
        model = self._ensure_model()

        entities = await asyncio.to_thread(
            model.predict_entities,
            text,
            types,
            threshold=self._threshold,
        )

        return [
            ExtractionResult(
                text=e["text"],
                label=e["label"].upper(),
                start=e["start"],
                end=e["end"],
                confidence=e["score"],
            )
            for e in entities
        ]


class HeuristicRelationExtractor:
    """Extract relations from entity co-occurrence within sentences.

    Strategy: for each sentence that contains two or more entities,
    create a :class:`RelationTriple` using the text between them as the
    predicate.  Falls back to ``"related_to"`` when no meaningful
    predicate can be extracted.

    Args:
        entity_extractor: An :class:`EntityExtractor` implementation (e.g.
            :class:`GLiNEREntityExtractor`).
        segmenter: Optional :class:`UniversalSegmenter` for sentence
            splitting.  Falls back to a simple regex split when ``None``.
    """

    def __init__(
        self,
        entity_extractor: EntityExtractor,
        segmenter: Any | None = None,
    ) -> None:
        self._ner = entity_extractor
        self._segmenter = segmenter

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences."""
        if self._segmenter is not None:
            return self._segmenter.split_sentences(text)
        parts = _SENTENCE_END.split(text.strip())
        return [s.strip() for s in parts if s.strip()]

    @staticmethod
    def _extract_predicate(
        sentence: str,
        ent_a: ExtractionResult,
        ent_b: ExtractionResult,
    ) -> str:
        """Derive a predicate from the text between two entities.

        Returns a cleaned, snake_cased verb phrase or ``"related_to"``
        as a fallback.
        """
        # Ensure ent_a starts before ent_b
        if ent_a.start > ent_b.start:
            ent_a, ent_b = ent_b, ent_a

        between = sentence[ent_a.end : ent_b.start].strip()
        if not between:
            return "related_to"

        # Strip leading/trailing punctuation and articles
        between = re.sub(r"^[,;:\-–—]+\s*", "", between)
        between = re.sub(r"\s*[,;:\-–—]+$", "", between)
        between = re.sub(r"^(the|a|an)\s+", "", between, flags=re.IGNORECASE)
        between = between.strip()

        if not between or len(between) > 80:
            return "related_to"

        # Convert to snake_case predicate
        predicate = re.sub(r"[^a-zA-Z0-9\s]", "", between)
        predicate = re.sub(r"\s+", "_", predicate.strip().lower())
        return predicate or "related_to"

    async def extract(self, text: str) -> list[RelationTriple]:
        """Extract relation triples from *text*.

        For each sentence with 2+ entities, creates pairwise triples.

        Returns:
            List of :class:`RelationTriple` instances.
        """
        if not text.strip():
            return []

        sentences = self._split_sentences(text)
        triples: list[RelationTriple] = []

        for sentence in sentences:
            entities = await self._ner.extract(sentence)
            if len(entities) < 2:
                continue

            # Sort by position for consistent predicate extraction
            entities.sort(key=lambda e: e.start)

            # Create triples for adjacent entity pairs
            for i in range(len(entities) - 1):
                ent_a = entities[i]
                ent_b = entities[i + 1]
                predicate = self._extract_predicate(sentence, ent_a, ent_b)
                confidence = min(ent_a.confidence, ent_b.confidence)

                triples.append(
                    RelationTriple(
                        subject=ent_a.text,
                        predicate=predicate,
                        object=ent_b.text,
                        confidence=confidence,
                        source_text=sentence,
                    )
                )

        return triples
