# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for ConversationGraphBuilder (Phase I8).

Covers:
- Build graph from a sample conversation
- Incremental processing (only new messages)
- Entity deduplication (same entity, different case)
- Reset and reprocess
- Empty/short messages skipped
- GraphBuildResult stats accuracy
- Graph store persistence
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sagewai.intelligence.graph.builder import (
    ConversationGraphBuilder,
    GraphBuildResult,
)
from sagewai.intelligence.models import ExtractionResult, RelationTriple


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_entity(text: str, label: str = "PERSON", confidence: float = 0.9):
    return ExtractionResult(
        text=text, label=label, start=0, end=len(text), confidence=confidence
    )


def _make_relation(
    subject: str, predicate: str, obj: str, confidence: float = 0.85
):
    return RelationTriple(
        subject=subject,
        predicate=predicate,
        object=obj,
        confidence=confidence,
    )


@pytest.fixture
def mock_ner():
    ner = AsyncMock()
    ner.extract = AsyncMock(return_value=[])
    return ner


@pytest.fixture
def mock_rel():
    rel = AsyncMock()
    rel.extract = AsyncMock(return_value=[])
    return rel


@pytest.fixture
def sample_messages():
    return [
        {"role": "user", "content": "Alice works at Acme Corp as a software engineer."},
        {"role": "assistant", "content": "That's great! Acme Corp is a well-known company."},
        {"role": "user", "content": "She collaborates with Bob on the Phoenix project."},
    ]


# ---------------------------------------------------------------------------
# GraphBuildResult model
# ---------------------------------------------------------------------------


class TestGraphBuildResult:
    def test_default_values(self):
        r = GraphBuildResult()
        assert r.entities_found == 0
        assert r.entities_unique == 0
        assert r.relations_found == 0
        assert r.messages_processed == 0

    def test_serialization(self):
        r = GraphBuildResult(
            entities_found=5, entities_unique=3, relations_found=2, messages_processed=4
        )
        d = r.model_dump()
        assert d["entities_found"] == 5
        assert d["entities_unique"] == 3


# ---------------------------------------------------------------------------
# Basic graph building
# ---------------------------------------------------------------------------


class TestConversationGraphBuilder:
    @pytest.mark.asyncio
    async def test_build_from_conversation(self, mock_ner, mock_rel, sample_messages):
        """Full conversation produces correct stats."""
        mock_ner.extract = AsyncMock(
            side_effect=[
                [_make_entity("Alice"), _make_entity("Acme Corp", "ORG")],
                [_make_entity("Acme Corp", "ORG")],
                [_make_entity("Bob"), _make_entity("Phoenix", "PROJECT", 0.8)],
            ]
        )
        mock_rel.extract = AsyncMock(
            side_effect=[
                [_make_relation("Alice", "works_at", "Acme Corp")],
                [],
                [_make_relation("Alice", "collaborates_with", "Bob")],
            ]
        )

        builder = ConversationGraphBuilder(
            entity_extractor=mock_ner, relation_extractor=mock_rel
        )
        result = await builder.process_messages(sample_messages)

        assert result.messages_processed == 3
        assert result.entities_found == 5  # 2 + 1 + 2
        # "acme corp" appears twice — deduped
        assert result.entities_unique == 4  # Alice, Acme Corp, Bob, Phoenix
        assert result.relations_found == 2
        assert builder.processed_count == 3

    @pytest.mark.asyncio
    async def test_empty_messages(self, mock_ner, mock_rel):
        """Empty message list produces zero stats."""
        builder = ConversationGraphBuilder(
            entity_extractor=mock_ner, relation_extractor=mock_rel
        )
        result = await builder.process_messages([])
        assert result.messages_processed == 0
        assert result.entities_found == 0

    @pytest.mark.asyncio
    async def test_short_messages_skipped(self, mock_ner, mock_rel):
        """Messages with fewer than 10 characters are skipped."""
        builder = ConversationGraphBuilder(
            entity_extractor=mock_ner, relation_extractor=mock_rel
        )
        result = await builder.process_messages(
            [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hey!"},
                {"role": "user", "content": ""},
            ]
        )
        assert result.messages_processed == 3
        assert result.entities_found == 0
        # Extractors should not have been called
        mock_ner.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_content_key(self, mock_ner, mock_rel):
        """Messages without a 'content' key are silently skipped."""
        builder = ConversationGraphBuilder(
            entity_extractor=mock_ner, relation_extractor=mock_rel
        )
        result = await builder.process_messages([{"role": "user"}])
        assert result.messages_processed == 1
        assert result.entities_found == 0


# ---------------------------------------------------------------------------
# Incremental processing
# ---------------------------------------------------------------------------


class TestIncrementalProcessing:
    @pytest.mark.asyncio
    async def test_incremental_only_new_messages(self, mock_ner, mock_rel):
        """Second call processes only new messages."""
        mock_ner.extract = AsyncMock(
            return_value=[_make_entity("Alice")]
        )
        mock_rel.extract = AsyncMock(return_value=[])

        builder = ConversationGraphBuilder(
            entity_extractor=mock_ner, relation_extractor=mock_rel
        )

        msgs = [
            {"role": "user", "content": "Alice is a developer at Acme."},
        ]
        r1 = await builder.process_messages(msgs)
        assert r1.messages_processed == 1
        assert builder.processed_count == 1

        # Add another message
        msgs.append(
            {"role": "user", "content": "Bob joined the team last week."}
        )
        r2 = await builder.process_messages(msgs)
        assert r2.messages_processed == 1  # only the new one
        assert builder.processed_count == 2

    @pytest.mark.asyncio
    async def test_start_from_override(self, mock_ner, mock_rel):
        """Explicit start_from overrides internal counter."""
        mock_ner.extract = AsyncMock(return_value=[_make_entity("X")])
        mock_rel.extract = AsyncMock(return_value=[])

        builder = ConversationGraphBuilder(
            entity_extractor=mock_ner, relation_extractor=mock_rel
        )

        msgs = [
            {"role": "user", "content": "First message about Alice."},
            {"role": "user", "content": "Second message about Bob."},
            {"role": "user", "content": "Third message about Carol."},
        ]

        # Process from index 1 (skip first)
        result = await builder.process_messages(msgs, start_from=1)
        assert result.messages_processed == 2
        assert builder.processed_count == 3


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestEntityDedup:
    def test_dedup_case_insensitive(self):
        """Same entity with different casing is deduplicated."""
        entities = [
            _make_entity("Alice", confidence=0.9),
            _make_entity("alice", confidence=0.8),
            _make_entity("ALICE", confidence=0.7),
        ]
        unique = ConversationGraphBuilder._deduplicate_entities(entities)
        assert len(unique) == 1
        assert unique[0].confidence == 0.9  # highest kept

    def test_dedup_preserves_different_entities(self):
        """Different entities remain after dedup."""
        entities = [
            _make_entity("Alice"),
            _make_entity("Bob"),
            _make_entity("Acme Corp", "ORG"),
        ]
        unique = ConversationGraphBuilder._deduplicate_entities(entities)
        assert len(unique) == 3

    def test_dedup_keeps_higher_confidence(self):
        """When merging duplicates, the higher confidence wins."""
        entities = [
            _make_entity("Alice", confidence=0.6),
            _make_entity("Alice", confidence=0.95),
        ]
        unique = ConversationGraphBuilder._deduplicate_entities(entities)
        assert len(unique) == 1
        assert unique[0].confidence == 0.95

    def test_dedup_empty_list(self):
        """Empty list returns empty."""
        assert ConversationGraphBuilder._deduplicate_entities([]) == []


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    @pytest.mark.asyncio
    async def test_reset_reprocesses_all(self, mock_ner, mock_rel):
        """After reset, all messages are reprocessed."""
        mock_ner.extract = AsyncMock(return_value=[_make_entity("X")])
        mock_rel.extract = AsyncMock(return_value=[])

        builder = ConversationGraphBuilder(
            entity_extractor=mock_ner, relation_extractor=mock_rel
        )

        msgs = [{"role": "user", "content": "Alice works at Acme Corp."}]
        await builder.process_messages(msgs)
        assert builder.processed_count == 1

        builder.reset()
        assert builder.processed_count == 0

        result = await builder.process_messages(msgs)
        assert result.messages_processed == 1


# ---------------------------------------------------------------------------
# Graph store integration
# ---------------------------------------------------------------------------


class TestGraphStoreIntegration:
    @pytest.mark.asyncio
    async def test_entities_and_relations_stored(self, mock_ner, mock_rel):
        """Extracted entities and relations are persisted to graph store."""
        mock_ner.extract = AsyncMock(
            return_value=[
                _make_entity("Alice"),
                _make_entity("Acme", "ORG"),
            ]
        )
        mock_rel.extract = AsyncMock(
            return_value=[_make_relation("Alice", "works_at", "Acme")]
        )

        graph = AsyncMock()
        graph.store = AsyncMock()
        graph.add_relation = AsyncMock()

        builder = ConversationGraphBuilder(
            entity_extractor=mock_ner,
            relation_extractor=mock_rel,
            graph_store=graph,
        )

        await builder.process_messages(
            [{"role": "user", "content": "Alice works at Acme as a developer."}]
        )

        assert graph.store.call_count == 2
        graph.add_relation.assert_called_once_with("Alice", "works_at", "Acme")

    @pytest.mark.asyncio
    async def test_no_graph_store_no_error(self, mock_ner, mock_rel):
        """Without a graph store, extraction still works."""
        mock_ner.extract = AsyncMock(return_value=[_make_entity("X")])
        mock_rel.extract = AsyncMock(return_value=[])

        builder = ConversationGraphBuilder(
            entity_extractor=mock_ner,
            relation_extractor=mock_rel,
            graph_store=None,
        )

        result = await builder.process_messages(
            [{"role": "user", "content": "Something about entities."}]
        )
        assert result.entities_found == 1

    @pytest.mark.asyncio
    async def test_graph_store_without_store_method(self, mock_ner, mock_rel):
        """Graph store missing 'store' method still works for relations."""
        mock_ner.extract = AsyncMock(return_value=[_make_entity("Alice")])
        mock_rel.extract = AsyncMock(
            return_value=[_make_relation("Alice", "knows", "Bob")]
        )

        # Object with add_relation but no store
        graph = AsyncMock(spec=[])
        graph.add_relation = AsyncMock()

        builder = ConversationGraphBuilder(
            entity_extractor=mock_ner,
            relation_extractor=mock_rel,
            graph_store=graph,
        )

        await builder.process_messages(
            [{"role": "user", "content": "Alice knows Bob from university."}]
        )
        graph.add_relation.assert_called_once()
