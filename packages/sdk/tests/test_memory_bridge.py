"""Tests for MemoryBridge — conversation/workflow/research → context."""

import pytest

from sagewai.context.engine import ContextEngine
from sagewai.context.memory_bridge import MemoryBridge
from sagewai.context.models import ContextScope, ContextSource
from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore
from sagewai.models.message import ChatMessage


@pytest.fixture
def engine():
    return ContextEngine(
        metadata_store=InMemoryMetadataStore(),
        vector_store=InMemoryVectorStore(),
        project_id="test-project",
    )


@pytest.fixture
def bridge(engine):
    return MemoryBridge(context_engine=engine, model="gpt-4o-mini")


class TestShouldExtract:
    def test_extracts_on_cadence(self, bridge):
        assert bridge.should_extract(5) is True
        assert bridge.should_extract(10) is True

    def test_skips_off_cadence(self, bridge):
        assert bridge.should_extract(3) is False
        assert bridge.should_extract(7) is False

    def test_extracts_on_compaction(self, bridge):
        assert bridge.should_extract(1, compaction_happened=True) is True

    def test_skips_zero_turns(self, bridge):
        assert bridge.should_extract(0) is False


class TestStoreWorkflowOutput:
    @pytest.mark.asyncio
    async def test_stores_workflow_output(self, bridge, engine):
        doc = await bridge.store_workflow_output(
            workflow_id="wf-123",
            output="The analysis found 3 key patterns in the data.",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        assert doc.source == ContextSource.WORKFLOW
        assert doc.status == "ready"
        assert doc.metadata["workflow_id"] == "wf-123"

    @pytest.mark.asyncio
    async def test_custom_title(self, bridge, engine):
        doc = await bridge.store_workflow_output(
            workflow_id="wf-456",
            output="Results here.",
            scope=ContextScope.PROJECT,
            scope_id="test-project",
            title="Q4 Analysis Results",
        )
        assert doc.title == "Q4 Analysis Results"


class TestStoreResearch:
    @pytest.mark.asyncio
    async def test_stores_research_results(self, bridge, engine):
        doc = await bridge.store_research(
            query="best practices for Python async",
            results=[
                "Use asyncio.gather for concurrent operations",
                "Prefer httpx.AsyncClient over requests",
            ],
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        assert doc.source == ContextSource.RESEARCH
        assert doc.status == "ready"
        assert doc.metadata["query"] == "best practices for Python async"
        assert doc.metadata["result_count"] == 2

    @pytest.mark.asyncio
    async def test_research_title_truncated(self, bridge, engine):
        long_query = "A" * 100
        doc = await bridge.store_research(
            query=long_query,
            results=["result 1"],
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        assert len(doc.title) <= 70  # "Research: " + 60 chars


class TestExtractFromConversation:
    @pytest.mark.asyncio
    async def test_empty_messages(self, bridge):
        docs = await bridge.extract_from_conversation(
            messages=[],
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        assert docs == []

    @pytest.mark.asyncio
    async def test_extraction_handles_llm_failure(self, bridge):
        # With no API key, LLM call will fail but bridge handles it gracefully
        messages = [
            ChatMessage.user("My favorite color is blue"),
            ChatMessage.assistant("Noted, you prefer blue!"),
        ]
        docs = await bridge.extract_from_conversation(
            messages=messages,
            scope=ContextScope.PROJECT,
            scope_id="test-project",
        )
        # Should return empty (LLM failed) rather than raising
        assert isinstance(docs, list)
