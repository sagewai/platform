# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the intelligence extraction layer (I3).

Covers:
- Model creation and serialization
- Protocol compliance (isinstance checks)
- GLiNEREntityExtractor (skip if gliner not installed)
- HeuristicRelationExtractor with mock entity extractor
- LLMEntityExtractor / LLMRelationExtractor with mocked litellm
- ProviderRegistry.get_entity_extractor() / get_relation_extractor() fallback
- NebulaGraphMemory integration: verify extractor is used when provided
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.intelligence.config import IntelligenceConfig
from sagewai.intelligence.extractors.gliner_extractor import (
    GLiNEREntityExtractor,
    HeuristicRelationExtractor,
)
from sagewai.intelligence.extractors.llm_extractor import (
    LLMEntityExtractor,
    LLMRelationExtractor,
)
from sagewai.intelligence.extractors.protocol import (
    EntityExtractor,
    RelationExtractor,
)
from sagewai.intelligence.models import (
    ExtractedFact,
    ExtractionResult,
    RelationTriple,
)
from sagewai.intelligence.registry import ProviderRegistry


# ── Model creation and serialization ──────────────────────────────────


class TestExtractionResult:
    def test_create(self) -> None:
        r = ExtractionResult(
            text="Python", label="TECHNOLOGY", start=0, end=6, confidence=0.95
        )
        assert r.text == "Python"
        assert r.label == "TECHNOLOGY"
        assert r.start == 0
        assert r.end == 6
        assert r.confidence == 0.95

    def test_serialization_roundtrip(self) -> None:
        r = ExtractionResult(
            text="Acme", label="ORG", start=10, end=14, confidence=0.8
        )
        data = r.model_dump()
        r2 = ExtractionResult(**data)
        assert r == r2

    def test_json_roundtrip(self) -> None:
        r = ExtractionResult(
            text="Berlin", label="LOCATION", start=5, end=11, confidence=0.7
        )
        j = r.model_dump_json()
        r2 = ExtractionResult.model_validate_json(j)
        assert r == r2


class TestRelationTriple:
    def test_create_defaults(self) -> None:
        t = RelationTriple(subject="A", predicate="knows", object="B")
        assert t.confidence == 1.0
        assert t.source_text == ""

    def test_create_full(self) -> None:
        t = RelationTriple(
            subject="Python",
            predicate="is_a",
            object="Language",
            confidence=0.9,
            source_text="Python is a language.",
        )
        assert t.subject == "Python"
        assert t.predicate == "is_a"
        assert t.object == "Language"

    def test_serialization(self) -> None:
        t = RelationTriple(subject="A", predicate="rel", object="B")
        data = t.model_dump()
        assert data["subject"] == "A"
        t2 = RelationTriple(**data)
        assert t == t2


class TestExtractedFact:
    def test_create_defaults(self) -> None:
        f = ExtractedFact(content="The sky is blue")
        assert f.fact_type == "general"
        assert f.confidence == 1.0
        assert f.entities == []

    def test_create_full(self) -> None:
        f = ExtractedFact(
            content="Team decided to use Rust",
            fact_type="decision",
            confidence=0.9,
            entities=["Team", "Rust"],
        )
        assert f.fact_type == "decision"
        assert len(f.entities) == 2


# ── Protocol compliance ───────────────────────────────────────────────


class TestProtocolCompliance:
    def test_llm_entity_extractor_is_protocol(self) -> None:
        ext = LLMEntityExtractor(model="gpt-4o-mini")
        assert isinstance(ext, EntityExtractor)

    def test_llm_relation_extractor_is_protocol(self) -> None:
        ext = LLMRelationExtractor(model="gpt-4o-mini")
        assert isinstance(ext, RelationExtractor)

    def test_heuristic_relation_extractor_is_protocol(self) -> None:
        mock_ner = AsyncMock()
        mock_ner.extract = AsyncMock(return_value=[])
        ext = HeuristicRelationExtractor(entity_extractor=mock_ner)
        assert isinstance(ext, RelationExtractor)


# ── GLiNEREntityExtractor ────────────────────────────────────────────


def _gliner_available() -> bool:
    try:
        import gliner  # noqa: F401

        return True
    except ImportError:
        return False


class TestGLiNEREntityExtractor:
    def test_import_error_when_gliner_missing(self) -> None:
        """Raises ImportError when gliner is not installed."""
        with patch.dict("sys.modules", {"gliner": None}):
            with pytest.raises(ImportError, match="gliner is required"):
                GLiNEREntityExtractor()

    @pytest.mark.skipif(
        not _gliner_available(),
        reason="gliner not installed",
    )
    async def test_extract_with_gliner(self) -> None:
        ext = GLiNEREntityExtractor()
        results = await ext.extract("John works at Google in London.")
        assert len(results) > 0
        for r in results:
            assert isinstance(r, ExtractionResult)
            assert r.confidence > 0.0

    async def test_extract_empty_text(self) -> None:
        """Should return empty list for blank input."""
        with patch.dict("sys.modules", {"gliner": MagicMock()}):
            # Create with mocked import
            with patch(
                "sagewai.intelligence.extractors.gliner_extractor.GLiNEREntityExtractor.__init__",
                return_value=None,
            ):
                ext = GLiNEREntityExtractor.__new__(GLiNEREntityExtractor)
                ext._model_name = "test"
                ext._threshold = 0.5
                ext._model = None
                results = await ext.extract("")
                assert results == []
                results = await ext.extract("   ")
                assert results == []


# ── HeuristicRelationExtractor ────────────────────────────────────────


class TestHeuristicRelationExtractor:
    async def test_extract_with_mock_ner(self) -> None:
        """Creates triples from entities in the same sentence."""
        mock_ner = AsyncMock()
        mock_ner.extract = AsyncMock(
            return_value=[
                ExtractionResult(
                    text="John", label="PERSON", start=0, end=4, confidence=0.9
                ),
                ExtractionResult(
                    text="Google", label="ORG", start=14, end=20, confidence=0.85
                ),
            ]
        )

        ext = HeuristicRelationExtractor(entity_extractor=mock_ner)
        triples = await ext.extract("John works at Google.")
        assert len(triples) == 1
        assert triples[0].subject == "John"
        assert triples[0].object == "Google"
        assert triples[0].confidence == 0.85  # min of the two
        assert isinstance(triples[0], RelationTriple)

    async def test_extract_empty_text(self) -> None:
        mock_ner = AsyncMock()
        ext = HeuristicRelationExtractor(entity_extractor=mock_ner)
        triples = await ext.extract("")
        assert triples == []

    async def test_single_entity_no_triple(self) -> None:
        """No triples when sentence has only one entity."""
        mock_ner = AsyncMock()
        mock_ner.extract = AsyncMock(
            return_value=[
                ExtractionResult(
                    text="John", label="PERSON", start=0, end=4, confidence=0.9
                ),
            ]
        )
        ext = HeuristicRelationExtractor(entity_extractor=mock_ner)
        triples = await ext.extract("John is here.")
        assert triples == []

    async def test_multiple_sentences(self) -> None:
        """Each sentence is processed independently."""
        call_count = 0

        async def mock_extract(text: str, **kwargs) -> list[ExtractionResult]:
            nonlocal call_count
            call_count += 1
            if "Alice" in text:
                return [
                    ExtractionResult(
                        text="Alice", label="PERSON", start=0, end=5, confidence=0.9
                    ),
                    ExtractionResult(
                        text="Bob", label="PERSON", start=11, end=14, confidence=0.8
                    ),
                ]
            return []

        mock_ner = AsyncMock()
        mock_ner.extract = mock_extract

        ext = HeuristicRelationExtractor(entity_extractor=mock_ner)
        triples = await ext.extract("Alice knows Bob. The weather is nice.")
        assert len(triples) == 1
        assert call_count == 2  # Two sentences

    def test_predicate_extraction(self) -> None:
        """Test the static predicate extraction helper."""
        ent_a = ExtractionResult(
            text="John", label="PERSON", start=0, end=4, confidence=0.9
        )
        ent_b = ExtractionResult(
            text="Google", label="ORG", start=14, end=20, confidence=0.8
        )
        pred = HeuristicRelationExtractor._extract_predicate(
            "John works at Google.", ent_a, ent_b
        )
        assert pred == "works_at"

    def test_predicate_fallback(self) -> None:
        """Falls back to 'related_to' when entities are adjacent."""
        ent_a = ExtractionResult(
            text="John", label="PERSON", start=0, end=4, confidence=0.9
        )
        ent_b = ExtractionResult(
            text="Smith", label="PERSON", start=4, end=9, confidence=0.8
        )
        pred = HeuristicRelationExtractor._extract_predicate(
            "JohnSmith", ent_a, ent_b
        )
        assert pred == "related_to"


# ── LLM Extractors (mocked) ──────────────────────────────────────────


class TestLLMEntityExtractor:
    async def test_extract_success(self) -> None:
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps([
                        {
                            "text": "Python",
                            "label": "TECHNOLOGY",
                            "start": 0,
                            "end": 6,
                            "confidence": 0.9,
                        }
                    ])
                )
            )
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            ext = LLMEntityExtractor(model="gpt-4o-mini")
            results = await ext.extract("Python is great.")
            assert len(results) == 1
            assert results[0].text == "Python"
            assert results[0].label == "TECHNOLOGY"

    async def test_extract_empty_text(self) -> None:
        ext = LLMEntityExtractor()
        results = await ext.extract("")
        assert results == []

    async def test_extract_parse_failure(self) -> None:
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="not valid json"))
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            ext = LLMEntityExtractor()
            results = await ext.extract("Hello world")
            assert results == []

    async def test_extract_with_code_fence(self) -> None:
        """Handles markdown code-fenced JSON responses."""
        fenced = '```json\n[{"text":"X","label":"ORG","start":0,"end":1,"confidence":0.8}]\n```'
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=fenced))
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            ext = LLMEntityExtractor()
            results = await ext.extract("X corp")
            assert len(results) == 1
            assert results[0].text == "X"


class TestLLMRelationExtractor:
    async def test_extract_success(self) -> None:
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps([["Python", "is_a", "Language"]])
                )
            )
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            ext = LLMRelationExtractor(model="gpt-4o-mini")
            triples = await ext.extract("Python is a language.")
            assert len(triples) == 1
            assert triples[0].subject == "Python"
            assert triples[0].predicate == "is_a"
            assert triples[0].object == "Language"

    async def test_extract_empty_text(self) -> None:
        ext = LLMRelationExtractor()
        triples = await ext.extract("")
        assert triples == []

    async def test_extract_parse_failure(self) -> None:
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="garbage"))
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            ext = LLMRelationExtractor()
            triples = await ext.extract("Some text")
            assert triples == []


# ── ProviderRegistry ──────────────────────────────────────────────────


class TestProviderRegistryExtractors:
    def test_get_entity_extractor_llm_mode(self) -> None:
        config = IntelligenceConfig(extraction_provider="llm")
        ext = ProviderRegistry.get_entity_extractor(config)
        assert isinstance(ext, LLMEntityExtractor)

    def test_get_relation_extractor_llm_mode(self) -> None:
        config = IntelligenceConfig(extraction_provider="llm")
        ext = ProviderRegistry.get_relation_extractor(config)
        assert isinstance(ext, LLMRelationExtractor)

    def test_get_entity_extractor_auto_fallback_to_llm(self) -> None:
        """Auto mode falls back to LLM when gliner not installed."""
        with patch(
            "sagewai.intelligence.registry._try_gliner_entity",
            side_effect=ImportError("no gliner"),
        ):
            config = IntelligenceConfig(extraction_provider="auto")
            ext = ProviderRegistry.get_entity_extractor(config)
            assert isinstance(ext, LLMEntityExtractor)

    def test_get_relation_extractor_auto_fallback_to_llm(self) -> None:
        """Auto mode falls back to LLM when gliner not installed."""
        with patch(
            "sagewai.intelligence.registry._try_gliner_entity",
            side_effect=ImportError("no gliner"),
        ):
            config = IntelligenceConfig(extraction_provider="auto")
            ext = ProviderRegistry.get_relation_extractor(config)
            assert isinstance(ext, LLMRelationExtractor)

    def test_get_entity_extractor_local_with_gliner(self) -> None:
        """Local mode works when gliner is available."""
        mock_ext = MagicMock()
        with patch(
            "sagewai.intelligence.registry._try_gliner_entity",
            return_value=mock_ext,
        ):
            config = IntelligenceConfig(extraction_provider="local")
            ext = ProviderRegistry.get_entity_extractor(config)
            assert ext is mock_ext

    def test_get_relation_extractor_with_provided_ner(self) -> None:
        """Uses the provided entity_extractor in heuristic mode."""
        mock_ner = MagicMock(spec=EntityExtractor)
        with patch(
            "sagewai.intelligence.extractors.gliner_extractor.HeuristicRelationExtractor"
        ) as MockHeuristic:
            config = IntelligenceConfig(extraction_provider="local")
            # Ensure _try_gliner_entity is not called when ner is provided
            with patch(
                "sagewai.intelligence.registry._try_gliner_entity"
            ) as mock_try:
                ProviderRegistry.get_relation_extractor(
                    config, entity_extractor=mock_ner
                )
                mock_try.assert_not_called()


# ── NebulaGraphMemory integration ─────────────────────────────────────


class TestNebulaGraphMemoryExtractorIntegration:
    async def test_store_uses_relation_extractor(self) -> None:
        """When relation_extractor is set, store() uses it instead of LLM."""
        mock_rel_ext = AsyncMock()
        mock_rel_ext.extract = AsyncMock(
            return_value=[
                RelationTriple(
                    subject="Python",
                    predicate="is_a",
                    object="Language",
                    confidence=0.9,
                )
            ]
        )

        # Patch ConnectionPool so we don't need NebulaGraph running
        with patch(
            "sagewai.memory.nebula.ConnectionPool", MagicMock()
        ), patch(
            "sagewai.memory.nebula.NebulaConfig", MagicMock()
        ):
            from sagewai.memory.nebula import NebulaGraphMemory

            mem = NebulaGraphMemory(relation_extractor=mock_rel_ext)

            # Mock add_relation so we can verify it was called
            mem.add_relation = AsyncMock()
            await mem.store("Python is a language.")

            mock_rel_ext.extract.assert_awaited_once_with(
                "Python is a language."
            )
            mem.add_relation.assert_awaited_once_with(
                "Python", "is_a", "Language"
            )

    async def test_store_falls_back_to_llm(self) -> None:
        """When no extractors configured, falls back to LLM."""
        with patch(
            "sagewai.memory.nebula.ConnectionPool", MagicMock()
        ), patch(
            "sagewai.memory.nebula.NebulaConfig", MagicMock()
        ), patch(
            "sagewai.memory.nebula._extract_relations_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = [("A", "rel", "B")]

            from sagewai.memory.nebula import NebulaGraphMemory

            mem = NebulaGraphMemory()
            mem.add_relation = AsyncMock()
            await mem.store("A rel B")

            mock_llm.assert_awaited_once()
            mem.add_relation.assert_awaited_once_with("A", "rel", "B")

    async def test_extract_relations_with_extractor(self) -> None:
        """The _extract_relations method delegates to relation_extractor."""
        mock_rel_ext = AsyncMock()
        mock_rel_ext.extract = AsyncMock(
            return_value=[
                RelationTriple(
                    subject="X", predicate="has", object="Y", confidence=0.8
                ),
                RelationTriple(
                    subject="Y", predicate="is", object="Z", confidence=0.7
                ),
            ]
        )

        with patch(
            "sagewai.memory.nebula.ConnectionPool", MagicMock()
        ), patch(
            "sagewai.memory.nebula.NebulaConfig", MagicMock()
        ):
            from sagewai.memory.nebula import NebulaGraphMemory

            mem = NebulaGraphMemory(relation_extractor=mock_rel_ext)
            result = await mem._extract_relations("X has Y. Y is Z.")

            assert result == [("X", "has", "Y"), ("Y", "is", "Z")]
