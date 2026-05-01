# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for MemoryConsolidator (Phase I9).

Covers:
- Deduplicate near-identical facts (mock embedder with similar vectors)
- Non-duplicate facts preserved
- Importance decay: old facts get lower weight
- Contradiction detection: overlapping entities + divergent content
- Works without embedder (returns facts unchanged)
- ConsolidationResult serialization
- Edge cases (empty lists, single fact, zero-norm vectors)
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock

import pytest

from sagewai.intelligence.graph.consolidator import (
    ConsolidationResult,
    MemoryConsolidator,
    _cosine_similarity,
)
from sagewai.intelligence.models import ExtractedFact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fact(
    content: str,
    confidence: float = 0.9,
    entities: list[str] | None = None,
    fact_type: str = "general",
) -> ExtractedFact:
    return ExtractedFact(
        content=content,
        confidence=confidence,
        entities=entities or [],
        fact_type=fact_type,
    )


def _mock_embedder(vectors_map: dict[str, list[float]] | None = None):
    """Return a mock embedder whose embed() returns vectors keyed by text."""
    emb = AsyncMock()
    emb.dimension = 3

    async def _embed(texts: list[str]) -> list[list[float]]:
        if vectors_map:
            return [vectors_map.get(t, [0.0, 0.0, 0.0]) for t in texts]
        return [[0.0, 0.0, 0.0]] * len(texts)

    emb.embed = AsyncMock(side_effect=_embed)
    return emb


# ---------------------------------------------------------------------------
# ConsolidationResult model
# ---------------------------------------------------------------------------


class TestConsolidationResult:
    def test_default_values(self):
        r = ConsolidationResult(unique_facts=[])
        assert r.merged_count == 0
        assert r.contradictions == []

    def test_serialization(self):
        fact = _fact("The sky is blue.")
        r = ConsolidationResult(
            unique_facts=[fact], merged_count=2, contradictions=[{"a": "b"}]
        )
        d = r.model_dump()
        assert d["merged_count"] == 2
        assert len(d["unique_facts"]) == 1
        assert d["contradictions"] == [{"a": "b"}]


# ---------------------------------------------------------------------------
# Cosine similarity helper
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert _cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_zero_vector(self):
        assert _cosine_similarity([0, 0], [1, 1]) == 0.0

    def test_both_zero_vectors(self):
        assert _cosine_similarity([0, 0], [0, 0]) == 0.0


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_near_duplicates_merged(self):
        """Facts with similarity >= threshold are merged."""
        # Two nearly identical vectors
        embedder = _mock_embedder(
            {
                "Alice likes Python.": [1.0, 0.0, 0.0],
                "Alice enjoys Python.": [0.99, 0.1, 0.0],
                "Bob uses Java.": [0.0, 1.0, 0.0],
            }
        )

        consolidator = MemoryConsolidator(
            embedder=embedder, similarity_threshold=0.9
        )
        facts = [
            _fact("Alice likes Python.", confidence=0.8),
            _fact("Alice enjoys Python.", confidence=0.9),
            _fact("Bob uses Java.", confidence=0.85),
        ]

        result = await consolidator.deduplicate_facts(facts)
        assert result.merged_count == 1
        assert len(result.unique_facts) == 2
        # The higher-confidence version of the Alice fact should survive
        alice_facts = [
            f for f in result.unique_facts if "Alice" in f.content
        ]
        assert len(alice_facts) == 1
        assert alice_facts[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_non_duplicates_preserved(self):
        """Dissimilar facts remain intact."""
        embedder = _mock_embedder(
            {
                "A": [1.0, 0.0, 0.0],
                "B": [0.0, 1.0, 0.0],
                "C": [0.0, 0.0, 1.0],
            }
        )
        consolidator = MemoryConsolidator(
            embedder=embedder, similarity_threshold=0.9
        )
        facts = [_fact("A"), _fact("B"), _fact("C")]

        result = await consolidator.deduplicate_facts(facts)
        assert result.merged_count == 0
        assert len(result.unique_facts) == 3

    @pytest.mark.asyncio
    async def test_single_fact_no_dedup(self):
        """A single fact is returned unchanged."""
        embedder = _mock_embedder()
        consolidator = MemoryConsolidator(embedder=embedder)
        facts = [_fact("Only one fact.")]

        result = await consolidator.deduplicate_facts(facts)
        assert result.merged_count == 0
        assert len(result.unique_facts) == 1

    @pytest.mark.asyncio
    async def test_empty_facts(self):
        """Empty list returns empty result."""
        embedder = _mock_embedder()
        consolidator = MemoryConsolidator(embedder=embedder)

        result = await consolidator.deduplicate_facts([])
        assert result.merged_count == 0
        assert result.unique_facts == []

    @pytest.mark.asyncio
    async def test_without_embedder(self):
        """Without embedder, all facts are returned unchanged."""
        consolidator = MemoryConsolidator(embedder=None)
        facts = [_fact("A"), _fact("B")]

        result = await consolidator.deduplicate_facts(facts)
        assert result.merged_count == 0
        assert len(result.unique_facts) == 2


# ---------------------------------------------------------------------------
# Importance decay
# ---------------------------------------------------------------------------


class TestImportanceDecay:
    def test_no_decay_at_zero_age(self):
        """Fresh facts retain full confidence."""
        consolidator = MemoryConsolidator(decay_rate=0.01)
        facts = [_fact("Fresh", confidence=0.9)]
        results = consolidator.apply_decay(facts, [0.0])
        assert len(results) == 1
        assert results[0][1] == pytest.approx(0.9)

    def test_decay_over_time(self):
        """Older facts get exponentially lower weight."""
        consolidator = MemoryConsolidator(decay_rate=0.1)
        facts = [_fact("Old fact", confidence=1.0)]
        results = consolidator.apply_decay(facts, [10.0])
        expected = 1.0 * math.exp(-0.1 * 10.0)
        assert results[0][1] == pytest.approx(expected)

    def test_decay_floor(self):
        """Weight never drops below 0.01."""
        consolidator = MemoryConsolidator(decay_rate=1.0)
        facts = [_fact("Ancient", confidence=0.5)]
        results = consolidator.apply_decay(facts, [100.0])
        assert results[0][1] == 0.01

    def test_multiple_facts_different_ages(self):
        """Each fact decays independently."""
        consolidator = MemoryConsolidator(decay_rate=0.05)
        facts = [
            _fact("Young", confidence=0.9),
            _fact("Middle", confidence=0.8),
            _fact("Old", confidence=0.7),
        ]
        ages = [1.0, 30.0, 365.0]
        results = consolidator.apply_decay(facts, ages)
        weights = [w for _, w in results]
        # Young > Middle > Old
        assert weights[0] > weights[1] > weights[2]


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------


class TestContradictionDetection:
    @pytest.mark.asyncio
    async def test_detects_contradiction(self):
        """Overlapping entities + low content similarity = contradiction."""
        embedder = _mock_embedder(
            {
                "Alice prefers Python.": [1.0, 0.0, 0.0],
                "Alice dislikes Python.": [0.0, 1.0, 0.0],
            }
        )
        consolidator = MemoryConsolidator(embedder=embedder)

        new = [_fact("Alice prefers Python.", entities=["Alice", "Python"])]
        existing = [_fact("Alice dislikes Python.", entities=["Alice", "Python"])]

        contradictions = await consolidator.detect_contradictions(new, existing)
        assert len(contradictions) == 1
        assert contradictions[0][2] > 0  # positive contradiction score

    @pytest.mark.asyncio
    async def test_no_contradiction_similar_content(self):
        """Overlapping entities + high content similarity = not a contradiction."""
        embedder = _mock_embedder(
            {
                "Alice likes Python.": [1.0, 0.0, 0.0],
                "Alice enjoys Python.": [0.99, 0.1, 0.0],
            }
        )
        consolidator = MemoryConsolidator(embedder=embedder)

        new = [_fact("Alice likes Python.", entities=["Alice", "Python"])]
        existing = [_fact("Alice enjoys Python.", entities=["Alice", "Python"])]

        contradictions = await consolidator.detect_contradictions(new, existing)
        assert len(contradictions) == 0

    @pytest.mark.asyncio
    async def test_no_contradiction_no_entity_overlap(self):
        """No entity overlap = no contradiction reported."""
        embedder = _mock_embedder(
            {
                "Alice likes Python.": [1.0, 0.0, 0.0],
                "Bob uses Java.": [0.0, 1.0, 0.0],
            }
        )
        consolidator = MemoryConsolidator(embedder=embedder)

        new = [_fact("Alice likes Python.", entities=["Alice"])]
        existing = [_fact("Bob uses Java.", entities=["Bob"])]

        contradictions = await consolidator.detect_contradictions(new, existing)
        assert len(contradictions) == 0

    @pytest.mark.asyncio
    async def test_no_contradiction_without_embedder(self):
        """Without embedder, contradiction detection returns empty."""
        consolidator = MemoryConsolidator(embedder=None)
        contradictions = await consolidator.detect_contradictions(
            [_fact("A", entities=["X"])],
            [_fact("B", entities=["X"])],
        )
        assert contradictions == []

    @pytest.mark.asyncio
    async def test_empty_entities_skipped(self):
        """Facts without entities are skipped in contradiction check."""
        embedder = _mock_embedder()
        consolidator = MemoryConsolidator(embedder=embedder)

        contradictions = await consolidator.detect_contradictions(
            [_fact("No entities here.")],
            [_fact("Also no entities.")],
        )
        assert contradictions == []

    @pytest.mark.asyncio
    async def test_low_entity_overlap_skipped(self):
        """Entity overlap ratio <= 0.5 is not flagged."""
        embedder = _mock_embedder(
            {
                "Alice and Bob work at Acme.": [1.0, 0.0, 0.0],
                "Bob and Carol go hiking.": [0.0, 1.0, 0.0],
            }
        )
        consolidator = MemoryConsolidator(embedder=embedder)

        # 1 out of 3 entities overlap on new side, 1 out of 2 on existing
        # ratio = 1 / min(3, 2) = 0.5, which is NOT > 0.5, so skipped
        new = [
            _fact(
                "Alice and Bob work at Acme.",
                entities=["Alice", "Bob", "Acme"],
            )
        ]
        existing = [
            _fact("Bob and Carol go hiking.", entities=["Bob", "Carol"])
        ]

        contradictions = await consolidator.detect_contradictions(new, existing)
        assert len(contradictions) == 0

    @pytest.mark.asyncio
    async def test_contradictions_sorted_by_score(self):
        """Multiple contradictions are returned sorted by score descending."""
        async def _embed(texts):
            vec_map = {
                "Alice prefers Python.": [1.0, 0.0, 0.0],
                "Alice dislikes Python.": [0.0, 1.0, 0.0],
                "Alice avoids Python completely.": [-0.5, 0.8, 0.0],
            }
            return [vec_map.get(t, [0.0, 0.0, 0.0]) for t in texts]

        embedder = AsyncMock()
        embedder.embed = AsyncMock(side_effect=_embed)

        consolidator = MemoryConsolidator(embedder=embedder)

        new = [_fact("Alice prefers Python.", entities=["Alice", "Python"])]
        existing = [
            _fact("Alice dislikes Python.", entities=["Alice", "Python"]),
            _fact(
                "Alice avoids Python completely.",
                entities=["Alice", "Python"],
            ),
        ]

        contradictions = await consolidator.detect_contradictions(new, existing)
        assert len(contradictions) >= 1
        # Verify sorted descending
        scores = [c[2] for c in contradictions]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_get_graph_builder(self):
        """ProviderRegistry.get_graph_builder returns a ConversationGraphBuilder."""
        from sagewai.intelligence.graph.builder import ConversationGraphBuilder
        from sagewai.intelligence.registry import ProviderRegistry

        try:
            builder = ProviderRegistry.get_graph_builder()
            assert isinstance(builder, ConversationGraphBuilder)
        except ImportError:
            # GLiNER/LLM not installed — acceptable in CI
            pytest.skip("extraction backends not available")

    def test_get_consolidator(self):
        """ProviderRegistry.get_consolidator returns a MemoryConsolidator."""
        from sagewai.intelligence.registry import ProviderRegistry

        consolidator = ProviderRegistry.get_consolidator()
        assert isinstance(consolidator, MemoryConsolidator)
