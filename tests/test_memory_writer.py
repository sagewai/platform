"""Tests for MemoryWriter — auto-extraction of facts from conversations."""

from unittest.mock import AsyncMock, patch

import pytest

from sagewai.core.memory_writer import MemoryWriter
from sagewai.memory.vector import VectorMemory
from sagewai.models.message import ChatMessage


class TestMemoryWriter:
    @pytest.mark.asyncio
    async def test_extract_returns_facts(self):
        writer = MemoryWriter(model="gpt-4o-mini")
        messages = [
            ChatMessage.user("My preferred language is Python."),
            ChatMessage.assistant("Noted! I'll use Python for code examples."),
        ]

        with patch(
            "sagewai.core.memory_writer._call_extraction_llm", new_callable=AsyncMock
        ) as mock:
            mock.return_value = ["User prefers Python", "Agent should use Python examples"]
            facts = await writer.extract(messages)

        assert len(facts) == 2
        assert "Python" in facts[0]

    @pytest.mark.asyncio
    async def test_extract_and_store_writes_to_memory(self):
        writer = MemoryWriter(model="gpt-4o-mini")
        memory = VectorMemory()
        messages = [
            ChatMessage.user("Deploy to GCP Cloud Run."),
            ChatMessage.assistant("Understood, targeting Cloud Run."),
        ]

        with patch(
            "sagewai.core.memory_writer._call_extraction_llm", new_callable=AsyncMock
        ) as mock:
            mock.return_value = ["Deploy target: GCP Cloud Run"]
            await writer.extract_and_store(messages, memory)

        assert len(memory) == 1
        results = await memory.retrieve("Cloud Run")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_extract_empty_conversation(self):
        writer = MemoryWriter(model="gpt-4o-mini")

        with patch(
            "sagewai.core.memory_writer._call_extraction_llm", new_callable=AsyncMock
        ) as mock:
            mock.return_value = []
            facts = await writer.extract([])

        assert facts == []

    @pytest.mark.asyncio
    async def test_should_extract_based_on_turn_count(self):
        writer = MemoryWriter(model="gpt-4o-mini", extract_every_n_turns=5)
        assert not writer.should_extract(turn_count=3)
        assert writer.should_extract(turn_count=5)
        assert writer.should_extract(turn_count=10)
        assert not writer.should_extract(turn_count=7)

    @pytest.mark.asyncio
    async def test_should_extract_on_compaction(self):
        writer = MemoryWriter(model="gpt-4o-mini")
        assert writer.should_extract(turn_count=1, compaction_happened=True)
