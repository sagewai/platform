# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for auto-learn wiring (#404)."""

import pytest

from sagewai.context.engine import ContextEngine
from sagewai.context.memory_bridge import MemoryBridge
from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore
from sagewai.models.agent import AgentConfig


class TestAgentConfigAutoLearn:
    def test_default_disabled(self):
        config = AgentConfig(name="test")
        assert config.auto_learn is False
        assert config.learn_every_n_turns == 5

    def test_enable_auto_learn(self):
        config = AgentConfig(name="test", auto_learn=True, learn_every_n_turns=3)
        assert config.auto_learn is True
        assert config.learn_every_n_turns == 3


class TestMemoryBridgeShouldExtract:
    def test_extracts_at_interval(self):
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
        )
        bridge = MemoryBridge(context_engine=engine, extract_every_n_turns=3)
        assert bridge.should_extract(0) is False
        assert bridge.should_extract(1) is False
        assert bridge.should_extract(2) is False
        assert bridge.should_extract(3) is True
        assert bridge.should_extract(4) is False
        assert bridge.should_extract(6) is True

    def test_extracts_on_compaction(self):
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
        )
        bridge = MemoryBridge(context_engine=engine)
        assert bridge.should_extract(1, compaction_happened=True) is True


class TestMemoryBridgeExtraction:
    @pytest.mark.asyncio
    async def test_empty_messages(self):
        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
        )
        bridge = MemoryBridge(context_engine=engine)
        from sagewai.context.models import ContextScope

        result = await bridge.extract_from_conversation(
            messages=[], scope=ContextScope.PROJECT, scope_id="test"
        )
        assert result == []
