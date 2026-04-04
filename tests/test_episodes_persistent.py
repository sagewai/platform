"""Tests for persistent episode store (#418)."""

import pytest

from sagewai.context.engine import ContextEngine
from sagewai.context.episodes import Episode, EpisodeStore, PersistentEpisodeStore
from sagewai.context.models import ContextScope, ContextSource
from sagewai.context.stores import InMemoryMetadataStore, InMemoryVectorStore


@pytest.fixture
def shared_stores():
    """Fresh stores per test to avoid cross-test pollution."""
    return InMemoryMetadataStore(), InMemoryVectorStore()


@pytest.fixture
def persistent_stores():
    """Shared stores that survive engine recreation (simulates persistence)."""
    return InMemoryMetadataStore(), InMemoryVectorStore()


class TestPersistentEpisodeStore:
    @pytest.mark.asyncio
    async def test_capture_persists_via_engine(self, shared_stores):
        meta, vec = shared_stores
        engine = ContextEngine(
            metadata_store=meta, vector_store=vec, project_id="test"
        )
        store = PersistentEpisodeStore(context_engine=engine)

        ep = Episode(
            goal="Audit quarterly financials",
            agent_name="auditor",
            outcome="Found 3 discrepancies",
            success=True,
        )
        await store.capture(ep, extract_lessons=False)

        # Verify stored in context engine
        docs = await engine.list_documents(scope=ContextScope.PROJECT)
        assert any(d.source == ContextSource.EPISODE for d in docs)

    @pytest.mark.asyncio
    async def test_retrieve_after_restart(self, persistent_stores):
        meta, vec = persistent_stores

        # Session 1: capture episode
        engine1 = ContextEngine(
            metadata_store=meta, vector_store=vec, project_id="test"
        )
        store1 = PersistentEpisodeStore(context_engine=engine1)
        await store1.capture(
            Episode(goal="Analyze revenue trends", agent_name="analyst", success=True),
            extract_lessons=False,
        )

        # Session 2: new engine instance (simulates restart)
        engine2 = ContextEngine(
            metadata_store=meta, vector_store=vec, project_id="test"
        )
        store2 = PersistentEpisodeStore(context_engine=engine2)

        results = await store2.retrieve("revenue analysis", top_k=3)
        assert len(results) >= 1
        assert results[0].goal == "Analyze revenue trends"

    @pytest.mark.asyncio
    async def test_retrieve_filters_agent_name(self, shared_stores):
        meta, vec = shared_stores
        engine = ContextEngine(
            metadata_store=meta, vector_store=vec, project_id="test"
        )
        store = PersistentEpisodeStore(context_engine=engine)

        await store.capture(
            Episode(goal="task A", agent_name="alpha"), extract_lessons=False
        )
        await store.capture(
            Episode(goal="task B", agent_name="beta"), extract_lessons=False
        )

        results = await store.retrieve("task", agent_name="alpha")
        assert all(r.agent_name == "alpha" for r in results)

    @pytest.mark.asyncio
    async def test_retrieve_filters_success(self, shared_stores):
        meta, vec = shared_stores
        engine = ContextEngine(
            metadata_store=meta, vector_store=vec, project_id="test"
        )
        store = PersistentEpisodeStore(context_engine=engine)

        await store.capture(
            Episode(goal="success task", success=True), extract_lessons=False
        )
        await store.capture(
            Episode(goal="failed task", success=False), extract_lessons=False
        )

        results = await store.retrieve("task", only_successful=True)
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_local_cache(self, shared_stores):
        meta, vec = shared_stores
        engine = ContextEngine(
            metadata_store=meta, vector_store=vec, project_id="test"
        )
        store = PersistentEpisodeStore(context_engine=engine)

        ep = Episode(goal="cached task")
        await store.capture(ep, extract_lessons=False)

        assert store.get(ep.id) is not None
        assert store.get(ep.id).goal == "cached task"

    @pytest.mark.asyncio
    async def test_format_for_prompt(self):
        episodes = [
            Episode(goal="audit", outcome="found issues", success=True, lessons=["check totals"]),
        ]
        text = PersistentEpisodeStore.format_for_prompt(episodes)
        assert "Past experiences" in text
        assert "audit" in text


class TestContextSourceEpisode:
    def test_episode_source_exists(self):
        assert ContextSource.EPISODE == "episode"
