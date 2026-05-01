# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for self-editing memory tools (#403)."""

import pytest

from sagewai.context.engine import ContextEngine
from sagewai.context.models import ContextScope
from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore
from sagewai.context.tools import create_memory_tools


@pytest.fixture
def engine():
    return ContextEngine(
        metadata_store=InMemoryMetadataStore(),
        vector_store=InMemoryVectorStore(),
        project_id="test-project",
    )


@pytest.fixture
def tools(engine):
    return create_memory_tools(engine)


def _get(tools, name):
    return next(t for t in tools if t.name == name)


class TestCreateMemoryTools:
    def test_returns_four_tools(self, tools):
        assert len(tools) == 4

    def test_tool_names(self, tools):
        names = {t.name for t in tools}
        assert "memory_store" in names
        assert "memory_search" in names
        assert "memory_forget" in names
        assert "memory_update" in names


class TestMemoryStore:
    @pytest.mark.asyncio
    async def test_stores_fact(self, engine, tools):
        t = _get(tools, "memory_store")
        result = await t.handler("The user prefers dark mode", title="preference")
        assert "Stored in memory" in result

        docs = await engine.list_documents(scope=ContextScope.PROJECT)
        assert len(docs) == 1
        assert docs[0].title == "preference"

    @pytest.mark.asyncio
    async def test_default_title_from_content(self, engine, tools):
        t = _get(tools, "memory_store")
        await t.handler("Short fact")

        docs = await engine.list_documents(scope=ContextScope.PROJECT)
        assert len(docs) == 1
        assert docs[0].title == "Short fact"


class TestMemorySearch:
    @pytest.mark.asyncio
    async def test_search_empty(self, tools):
        t = _get(tools, "memory_search")
        result = await t.handler("anything")
        assert "No relevant memories" in result

    @pytest.mark.asyncio
    async def test_search_finds_stored(self, engine, tools):
        store = _get(tools, "memory_store")
        search = _get(tools, "memory_search")

        await store.handler("The capital of France is Paris")
        result = await search.handler("capital of France")
        assert "Paris" in result


class TestMemoryForget:
    @pytest.mark.asyncio
    async def test_forget_reduces_importance(self, engine, tools):
        store = _get(tools, "memory_store")
        forget = _get(tools, "memory_forget")

        await store.handler("Outdated fact")
        result = await forget.handler("Outdated fact")
        assert "Forgotten" in result

    @pytest.mark.asyncio
    async def test_forget_nonexistent(self, tools):
        t = _get(tools, "memory_forget")
        result = await t.handler("nothing here")
        assert "No matching memory" in result


class TestMemoryUpdate:
    @pytest.mark.asyncio
    async def test_update_replaces_fact(self, engine, tools):
        store = _get(tools, "memory_store")
        update = _get(tools, "memory_update")

        await store.handler("User lives in Berlin")
        result = await update.handler("User lives in Berlin", "User lives in Munich")
        assert "Updated memory" in result

        docs = await engine.list_documents(scope=ContextScope.PROJECT)
        assert len(docs) >= 2


class TestEngineGetTools:
    def test_engine_get_tools(self, engine):
        tools = engine.get_tools()
        assert len(tools) == 4
        names = {t.name for t in tools}
        assert "memory_store" in names
