"""Tests for intelligence layer fact extraction (Phase I4).

Covers:
- RuleBasedFactExtractor: each pattern type + dedup
- LLMFactExtractor: mock litellm, verify prompt, parse fallback
- HybridFactExtractor: rules-only + hybrid enhancement
- MemoryBridge / MemoryWriter integration
- Protocol compliance
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.intelligence.extractors.hybrid_fact_extractor import (
    HybridFactExtractor,
)
from sagewai.intelligence.extractors.llm_fact_extractor import LLMFactExtractor
from sagewai.intelligence.extractors.protocol import FactExtractor
from sagewai.intelligence.extractors.rule_based import RuleBasedFactExtractor
from sagewai.intelligence.models import ExtractedFact

# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_rule_based_implements_protocol(self):
        assert isinstance(RuleBasedFactExtractor(), FactExtractor)

    def test_llm_implements_protocol(self):
        assert isinstance(LLMFactExtractor(), FactExtractor)

    def test_hybrid_implements_protocol(self):
        assert isinstance(HybridFactExtractor(), FactExtractor)


# ---------------------------------------------------------------------------
# RuleBasedFactExtractor
# ---------------------------------------------------------------------------


class TestRuleBasedFactExtractor:
    @pytest.fixture()
    def extractor(self):
        return RuleBasedFactExtractor()

    @pytest.mark.asyncio
    async def test_decision_we_decided(self, extractor):
        text = "We decided to use PostgreSQL for the main database"
        facts = await extractor.extract(text)
        assert any(f.fact_type == "decision" for f in facts)

    @pytest.mark.asyncio
    async def test_decision_the_plan_is(self, extractor):
        text = "The plan is to migrate to Kubernetes next quarter"
        facts = await extractor.extract(text)
        assert any(f.fact_type == "decision" for f in facts)

    @pytest.mark.asyncio
    async def test_decision_lets_go_with(self, extractor):
        text = "Let's go with React for the frontend framework"
        facts = await extractor.extract(text)
        assert any(f.fact_type == "decision" for f in facts)

    @pytest.mark.asyncio
    async def test_preference_i_prefer(self, extractor):
        text = "I prefer dark mode for the editor interface"
        facts = await extractor.extract(text)
        assert any(f.fact_type == "preference" for f in facts)

    @pytest.mark.asyncio
    async def test_preference_i_dont_like(self, extractor):
        text = "I don't like verbose error messages in production logs"
        facts = await extractor.extract(text)
        assert any(f.fact_type == "preference" for f in facts)

    @pytest.mark.asyncio
    async def test_event_scheduled_for(self, extractor):
        text = "Meeting scheduled for Friday at 2pm"
        facts = await extractor.extract(text)
        assert any(f.fact_type == "event" for f in facts)

    @pytest.mark.asyncio
    async def test_event_deadline(self, extractor):
        text = "Deadline is March 15th, 2026"
        facts = await extractor.extract(text)
        assert any(f.fact_type == "event" for f in facts)

    @pytest.mark.asyncio
    async def test_action_i_will(self, extractor):
        text = "I will update the deployment scripts tomorrow"
        facts = await extractor.extract(text)
        assert any(f.fact_type == "action" for f in facts)

    @pytest.mark.asyncio
    async def test_action_todo(self, extractor):
        text = "TODO: refactor the authentication middleware"
        facts = await extractor.extract(text)
        assert any(f.fact_type == "action" for f in facts)

    @pytest.mark.asyncio
    async def test_entity_email(self, extractor):
        text = "Send the report to alice@example.com when ready"
        facts = await extractor.extract(text)
        entity_facts = [f for f in facts if f.fact_type == "entity"]
        assert len(entity_facts) >= 1
        assert any("alice@example.com" in f.entities for f in entity_facts)

    @pytest.mark.asyncio
    async def test_entity_url(self, extractor):
        text = "Check the docs at https://docs.example.com/api for details"
        facts = await extractor.extract(text)
        entity_facts = [f for f in facts if f.fact_type == "entity"]
        assert len(entity_facts) >= 1
        assert any(
            "https://docs.example.com/api" in f.entities
            for f in entity_facts
        )

    @pytest.mark.asyncio
    async def test_entity_mention(self, extractor):
        text = "Ask @johndoe about the CI pipeline configuration"
        facts = await extractor.extract(text)
        entity_facts = [f for f in facts if f.fact_type == "entity"]
        assert len(entity_facts) >= 1
        assert any("@johndoe" in f.entities for f in entity_facts)

    @pytest.mark.asyncio
    async def test_deduplication(self, extractor):
        text = (
            "We decided to use PostgreSQL for the main database\n"
            "We decided to use PostgreSQL for the main database"
        )
        facts = await extractor.extract(text)
        decision_facts = [f for f in facts if f.fact_type == "decision"]
        assert len(decision_facts) == 1

    @pytest.mark.asyncio
    async def test_empty_input(self, extractor):
        facts = await extractor.extract("")
        assert facts == []

    @pytest.mark.asyncio
    async def test_no_patterns_match(self, extractor):
        text = "The weather is nice today."
        facts = await extractor.extract(text)
        # Should return empty or only entity-type facts
        non_entity = [f for f in facts if f.fact_type != "entity"]
        assert len(non_entity) == 0

    @pytest.mark.asyncio
    async def test_confidence_values(self, extractor):
        text = "We decided to use Python 3.12 for the backend"
        facts = await extractor.extract(text)
        for fact in facts:
            assert 0.0 <= fact.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_multiline_conversation(self, extractor):
        text = (
            "USER: I prefer using TypeScript over JavaScript\n"
            "ASSISTANT: Noted. Any other preferences?\n"
            "USER: We decided to deploy on GCP\n"
            "USER: TODO: set up the CI pipeline"
        )
        facts = await extractor.extract(text)
        types = {f.fact_type for f in facts}
        assert "preference" in types
        assert "decision" in types
        assert "action" in types


# ---------------------------------------------------------------------------
# LLMFactExtractor
# ---------------------------------------------------------------------------


class TestLLMFactExtractor:
    @pytest.mark.asyncio
    async def test_extract_with_structured_json(self):
        response_data = [
            {
                "content": "Team uses PostgreSQL",
                "fact_type": "decision",
                "confidence": 0.95,
            },
            {
                "content": "User prefers dark mode",
                "fact_type": "preference",
                "confidence": 0.8,
            },
        ]

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps(response_data)))
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            extractor = LLMFactExtractor(model="gpt-4o-mini")
            facts = await extractor.extract("some conversation")

        assert len(facts) == 2
        assert facts[0].content == "Team uses PostgreSQL"
        assert facts[0].fact_type == "decision"
        assert facts[1].fact_type == "preference"
        mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_with_string_array(self):
        response_data = ["Fact one", "Fact two"]

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps(response_data)))
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            extractor = LLMFactExtractor()
            facts = await extractor.extract("conversation text")

        assert len(facts) == 2
        assert facts[0].content == "Fact one"

    @pytest.mark.asyncio
    async def test_extract_with_newline_fallback(self):
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(content="- Fact alpha\n- Fact beta\n")
            )
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            extractor = LLMFactExtractor()
            facts = await extractor.extract("text")

        assert len(facts) == 2
        assert facts[0].content == "Fact alpha"

    @pytest.mark.asyncio
    async def test_extract_handles_llm_error(self):
        with patch(
            "litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API down"),
        ):
            extractor = LLMFactExtractor()
            facts = await extractor.extract("text")

        assert facts == []

    @pytest.mark.asyncio
    async def test_prompt_includes_conversation(self):
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="[]"))
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            extractor = LLMFactExtractor()
            await extractor.extract("Hello world conversation")

        call_args = mock_llm.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "Hello world conversation" in prompt


# ---------------------------------------------------------------------------
# HybridFactExtractor
# ---------------------------------------------------------------------------


class TestHybridFactExtractor:
    @pytest.mark.asyncio
    async def test_rules_only_when_no_llm(self):
        extractor = HybridFactExtractor(llm_extractor=None)
        text = "We decided to use Rust for the performance-critical module"
        facts = await extractor.extract(text)
        assert any(f.fact_type == "decision" for f in facts)

    @pytest.mark.asyncio
    async def test_llm_called_when_rules_find_few(self):
        llm_mock = AsyncMock()
        llm_mock.extract = AsyncMock(
            return_value=[
                ExtractedFact(content="LLM fact", fact_type="general")
            ]
        )

        extractor = HybridFactExtractor(
            llm_extractor=llm_mock,
            llm_threshold=5,
        )
        # Text with only 1 rule match
        text = "We decided to use MongoDB for the analytics store"
        facts = await extractor.extract(text)

        llm_mock.extract.assert_called_once()
        assert any(f.content == "LLM fact" for f in facts)

    @pytest.mark.asyncio
    async def test_llm_skipped_when_rules_find_enough(self):
        llm_mock = AsyncMock()
        llm_mock.extract = AsyncMock(return_value=[])

        extractor = HybridFactExtractor(
            llm_extractor=llm_mock,
            llm_threshold=1,
        )
        # Text with multiple rule matches
        text = (
            "We decided to use PostgreSQL for the main database\n"
            "I prefer async/await patterns over callbacks\n"
            "TODO: write integration tests for the API"
        )
        facts = await extractor.extract(text)

        # LLM should not be called since rules found >= threshold
        llm_mock.extract.assert_not_called()
        assert len(facts) >= 1

    @pytest.mark.asyncio
    async def test_deduplication_across_sources(self):
        llm_mock = AsyncMock()
        llm_mock.extract = AsyncMock(
            return_value=[
                ExtractedFact(
                    content="We decided to use PostgreSQL for the main database",
                    fact_type="decision",
                )
            ]
        )

        extractor = HybridFactExtractor(
            llm_extractor=llm_mock,
            llm_threshold=5,
        )
        text = "We decided to use PostgreSQL for the main database"
        facts = await extractor.extract(text)

        # Both sources find the same fact — should be deduped
        decision_facts = [f for f in facts if f.fact_type == "decision"]
        assert len(decision_facts) == 1


# ---------------------------------------------------------------------------
# ProviderRegistry.get_fact_extractor
# ---------------------------------------------------------------------------


class TestProviderRegistryFactExtractor:
    """Fact extractors are instantiated directly (get_fact_extractor is not
    exposed on ProviderRegistry after the migration squash).  These tests
    verify the concrete classes still satisfy the protocol."""

    def test_rules_provider(self):
        extractor = RuleBasedFactExtractor()
        assert isinstance(extractor, FactExtractor)

    def test_llm_provider(self):
        extractor = LLMFactExtractor()
        assert isinstance(extractor, FactExtractor)

    def test_hybrid_provider(self):
        extractor = HybridFactExtractor()
        assert isinstance(extractor, FactExtractor)

    def test_auto_provider_returns_extractor(self):
        """Default (auto) — hybrid if litellm available, else rules."""
        try:
            import litellm  # noqa: F401

            extractor = HybridFactExtractor()
        except ImportError:
            extractor = RuleBasedFactExtractor()
        assert isinstance(extractor, FactExtractor)

    def test_default_config(self):
        extractor = RuleBasedFactExtractor()
        assert isinstance(extractor, FactExtractor)


# ---------------------------------------------------------------------------
# MemoryBridge integration
# ---------------------------------------------------------------------------


class TestMemoryBridgeIntegration:
    @pytest.mark.asyncio
    async def test_fact_extractor_used_when_provided(self):
        from sagewai.context.memory_bridge import MemoryBridge
        from sagewai.models.message import ChatMessage, Role

        mock_extractor = AsyncMock()
        mock_extractor.extract = AsyncMock(
            return_value=[
                ExtractedFact(
                    content="User prefers Python",
                    fact_type="preference",
                ),
            ]
        )

        mock_engine = AsyncMock()
        mock_doc = MagicMock()
        mock_doc.id = "doc-123"
        mock_engine.ingest_text = AsyncMock(return_value=mock_doc)

        bridge = MemoryBridge(
            context_engine=mock_engine,
            fact_extractor=mock_extractor,
        )

        messages = [
            ChatMessage(role=Role.user, content="I prefer Python"),
        ]
        from sagewai.context.models import ContextScope

        docs = await bridge.extract_from_conversation(
            messages,
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )

        mock_extractor.extract.assert_called_once()
        mock_engine.ingest_text.assert_called_once()
        assert len(docs) == 1

    @pytest.mark.asyncio
    async def test_llm_fallback_when_no_extractor(self):
        """When no fact_extractor is provided, MemoryBridge uses LLM."""
        from sagewai.context.memory_bridge import MemoryBridge
        from sagewai.models.message import ChatMessage, Role

        mock_engine = AsyncMock()
        mock_doc = MagicMock()
        mock_doc.id = "doc-456"
        mock_engine.ingest_text = AsyncMock(return_value=mock_doc)

        bridge = MemoryBridge(context_engine=mock_engine)

        messages = [
            ChatMessage(role=Role.user, content="Hello world"),
        ]

        with patch(
            "sagewai.context.memory_bridge._call_extraction_llm",
            new_callable=AsyncMock,
            return_value=["fact from LLM"],
        ) as mock_llm:
            from sagewai.context.models import ContextScope

            docs = await bridge.extract_from_conversation(
                messages,
                scope=ContextScope.PROJECT,
                scope_id="test",
            )

        mock_llm.assert_called_once()
        assert len(docs) == 1


# ---------------------------------------------------------------------------
# MemoryWriter integration
# ---------------------------------------------------------------------------


class TestMemoryWriterIntegration:
    @pytest.mark.asyncio
    async def test_fact_extractor_used_when_provided(self):
        from sagewai.core.memory_writer import MemoryWriter
        from sagewai.models.message import ChatMessage, Role

        mock_extractor = AsyncMock()
        mock_extractor.extract = AsyncMock(
            return_value=[
                ExtractedFact(content="Key fact A", fact_type="general"),
                ExtractedFact(content="Key fact B", fact_type="decision"),
            ]
        )

        writer = MemoryWriter(fact_extractor=mock_extractor)
        messages = [
            ChatMessage(role=Role.user, content="Some conversation"),
        ]
        facts = await writer.extract(messages)

        mock_extractor.extract.assert_called_once()
        assert facts == ["Key fact A", "Key fact B"]

    @pytest.mark.asyncio
    async def test_llm_fallback_when_no_extractor(self):
        from sagewai.core.memory_writer import MemoryWriter
        from sagewai.models.message import ChatMessage, Role

        writer = MemoryWriter()
        messages = [
            ChatMessage(role=Role.user, content="Test message"),
        ]

        with patch(
            "sagewai.core.memory_writer._call_extraction_llm",
            new_callable=AsyncMock,
            return_value=["llm extracted fact"],
        ) as mock_llm:
            facts = await writer.extract(messages)

        mock_llm.assert_called_once()
        assert facts == ["llm extracted fact"]


# ---------------------------------------------------------------------------
# ExtractedFact model
# ---------------------------------------------------------------------------


class TestExtractedFactModel:
    def test_defaults(self):
        fact = ExtractedFact(content="test fact")
        assert fact.fact_type == "general"
        assert fact.confidence == 1.0
        assert fact.entities == []

    def test_custom_values(self):
        fact = ExtractedFact(
            content="We chose Redis",
            fact_type="decision",
            confidence=0.85,
            entities=["Redis"],
        )
        assert fact.fact_type == "decision"
        assert fact.confidence == 0.85
        assert fact.entities == ["Redis"]
