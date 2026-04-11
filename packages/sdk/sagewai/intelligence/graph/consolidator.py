# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""MemoryConsolidator — deduplicate, decay, and detect contradictions in memory.

Features:
- Semantic dedup: merge facts with cosine similarity above a threshold.
- Importance decay: reduce weight of old facts over time (exponential).
- Contradiction detection: flag facts with overlapping entities but divergent content.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from sagewai.intelligence.models import ExtractedFact

if TYPE_CHECKING:
    from sagewai.intelligence.embeddings.protocol import Embedder

logger = logging.getLogger(__name__)


class ConsolidationResult(BaseModel):
    """Result of a memory consolidation operation.

    Attributes:
        unique_facts: Facts remaining after deduplication.
        merged_count: Number of duplicate facts that were merged away.
        contradictions: Serialized contradiction pairs (new, existing, score).
    """

    unique_facts: list[ExtractedFact]
    merged_count: int = 0
    contradictions: list[dict] = Field(default_factory=list)


class MemoryConsolidator:
    """Consolidate, deduplicate, and manage memory quality over time.

    Args:
        embedder: Embedding backend for semantic similarity. When ``None``,
            deduplication and contradiction detection are skipped.
        similarity_threshold: Cosine similarity above which two facts are
            considered duplicates (0.0--1.0, default 0.9).
        decay_rate: Exponential decay rate per day for importance weighting
            (default 0.01).
    """

    def __init__(
        self,
        embedder: Embedder | None = None,
        similarity_threshold: float = 0.9,
        decay_rate: float = 0.01,
    ) -> None:
        self._embedder = embedder
        self._threshold = similarity_threshold
        self._decay_rate = decay_rate

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    async def deduplicate_facts(
        self,
        facts: list[ExtractedFact],
    ) -> ConsolidationResult:
        """Find and merge near-duplicate facts.

        Strategy:
        1. Embed all fact contents.
        2. Compute pairwise cosine similarity.
        3. Group facts with similarity >= threshold.
        4. Keep the highest-confidence fact from each group.
        5. Return unique facts and a merge report.

        When no embedder is available or fewer than 2 facts are given,
        all facts are returned unchanged.
        """
        if not self._embedder or len(facts) < 2:
            return ConsolidationResult(
                unique_facts=list(facts),
                merged_count=0,
                contradictions=[],
            )

        texts = [f.content for f in facts]
        vectors = await self._embedder.embed(texts)

        merged_indices: set[int] = set()
        groups: list[list[int]] = []

        for i in range(len(vectors)):
            if i in merged_indices:
                continue
            group = [i]
            for j in range(i + 1, len(vectors)):
                if j in merged_indices:
                    continue
                sim = _cosine_similarity(vectors[i], vectors[j])
                if sim >= self._threshold:
                    group.append(j)
                    merged_indices.add(j)
            groups.append(group)

        unique: list[ExtractedFact] = []
        for group in groups:
            best_idx = max(group, key=lambda idx: facts[idx].confidence)
            unique.append(facts[best_idx])

        return ConsolidationResult(
            unique_facts=unique,
            merged_count=len(facts) - len(unique),
            contradictions=[],
        )

    # ------------------------------------------------------------------
    # Importance decay
    # ------------------------------------------------------------------

    def apply_decay(
        self,
        facts: list[ExtractedFact],
        ages_days: list[float],
    ) -> list[tuple[ExtractedFact, float]]:
        """Apply time-based importance decay to facts.

        Weight formula: ``confidence * exp(-decay_rate * age_days)``,
        floored at 0.01 to avoid completely discarding old facts.

        Args:
            facts: The facts to decay.
            ages_days: Age in days for each fact (parallel list).

        Returns:
            List of ``(fact, decayed_weight)`` tuples.
        """
        results: list[tuple[ExtractedFact, float]] = []
        for fact, age in zip(facts, ages_days):
            weight = fact.confidence * math.exp(-self._decay_rate * age)
            results.append((fact, max(weight, 0.01)))
        return results

    # ------------------------------------------------------------------
    # Contradiction detection
    # ------------------------------------------------------------------

    async def detect_contradictions(
        self,
        new_facts: list[ExtractedFact],
        existing_facts: list[ExtractedFact],
    ) -> list[tuple[ExtractedFact, ExtractedFact, float]]:
        """Detect potential contradictions between new and existing facts.

        Strategy: find pairs with high entity overlap but low content
        similarity. A high ``entity_overlap_ratio`` combined with low
        cosine similarity on the fact text indicates the same topic is
        being discussed with different (possibly contradictory) content.

        Args:
            new_facts: Recently extracted facts.
            existing_facts: Previously stored facts.

        Returns:
            List of ``(new_fact, existing_fact, contradiction_score)``
            tuples sorted by score descending.
        """
        if not self._embedder:
            return []

        contradictions: list[tuple[ExtractedFact, ExtractedFact, float]] = []

        for new in new_facts:
            new_entities = {e.lower() for e in new.entities}
            if not new_entities:
                continue

            for existing in existing_facts:
                existing_entities = {e.lower() for e in existing.entities}
                if not existing_entities:
                    continue

                overlap = new_entities & existing_entities
                if not overlap:
                    continue

                entity_overlap_ratio = len(overlap) / min(
                    len(new_entities), len(existing_entities)
                )
                if entity_overlap_ratio <= 0.5:
                    continue

                vecs = await self._embedder.embed(
                    [new.content, existing.content]
                )
                sim = _cosine_similarity(vecs[0], vecs[1])

                if sim < 0.5:
                    score = entity_overlap_ratio * (1.0 - sim)
                    contradictions.append((new, existing, score))

        contradictions.sort(key=lambda t: t[2], reverse=True)
        return contradictions


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors (no numpy)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
