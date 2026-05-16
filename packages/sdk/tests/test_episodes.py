# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for episodic memory (#408)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.context.episodes import (
    Episode,
    EpisodeStore,
    _extract_lessons_static,
)
from sagewai.context.stores import InMemoryVectorStore


@pytest.fixture
def store():
    return EpisodeStore(vector_store=InMemoryVectorStore())


class TestEpisodeModel:
    def test_create_episode(self):
        ep = Episode(
            goal="Audit Q1 financial statements",
            agent_name="auditor",
            outcome="Found 3 discrepancies",
            success=True,
            actions_taken=["read_document", "compare_totals", "flag_issues"],
        )
        assert ep.goal == "Audit Q1 financial statements"
        assert ep.success is True
        assert len(ep.actions_taken) == 3
        assert ep.id  # auto-generated

    def test_defaults(self):
        ep = Episode(goal="test")
        assert ep.success is True
        assert ep.lessons == []
        assert ep.duration_seconds == 0
        assert ep.project_id == "default"


class TestEpisodeStore:
    @pytest.mark.asyncio
    async def test_capture_and_get(self, store):
        ep = Episode(goal="test task", agent_name="bot", outcome="done")
        captured = await store.capture(ep, extract_lessons=False)
        assert captured.id == ep.id

        fetched = store.get(ep.id)
        assert fetched is not None
        assert fetched.goal == "test task"

    @pytest.mark.asyncio
    async def test_capture_with_fallback_lessons(self, store):
        ep = Episode(
            goal="Analyze revenue data",
            outcome="Completed analysis",
            success=True,
        )
        captured = await store.capture(ep, extract_lessons=True)
        # Fallback lessons should be generated even without LLM
        assert len(captured.lessons) >= 1

    @pytest.mark.asyncio
    async def test_retrieve_by_similarity(self, store):
        await store.capture(
            Episode(goal="Audit Q1 financial statements", agent_name="auditor"),
            extract_lessons=False,
        )
        await store.capture(
            Episode(goal="Write marketing copy", agent_name="writer"),
            extract_lessons=False,
        )
        await store.capture(
            Episode(goal="Review Q2 financial audit", agent_name="auditor"),
            extract_lessons=False,
        )

        results = await store.retrieve("financial audit", top_k=2)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_retrieve_filter_agent(self, store):
        await store.capture(
            Episode(goal="task A", agent_name="alpha"),
            extract_lessons=False,
        )
        await store.capture(
            Episode(goal="task B", agent_name="beta"),
            extract_lessons=False,
        )

        results = await store.retrieve("task", agent_name="alpha")
        assert all(r.agent_name == "alpha" for r in results)

    @pytest.mark.asyncio
    async def test_retrieve_only_successful(self, store):
        await store.capture(
            Episode(goal="task ok", success=True),
            extract_lessons=False,
        )
        await store.capture(
            Episode(goal="task fail", success=False),
            extract_lessons=False,
        )

        results = await store.retrieve("task", only_successful=True)
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_list_all(self, store):
        await store.capture(Episode(goal="a"), extract_lessons=False)
        await store.capture(Episode(goal="b"), extract_lessons=False)
        assert len(store.list_all()) == 2

    @pytest.mark.asyncio
    async def test_list_filter_project(self, store):
        await store.capture(
            Episode(goal="a", project_id="p1"), extract_lessons=False
        )
        await store.capture(
            Episode(goal="b", project_id="p2"), extract_lessons=False
        )
        assert len(store.list_all(project_id="p1")) == 1


def _fenced_llm_response(content: str) -> MagicMock:
    """Build a litellm-style response carrying ``content`` verbatim."""
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    return resp


class TestLessonExtractionParsing:
    """Lesson extraction must survive SLM markdown-fenced JSON output."""

    @pytest.mark.asyncio
    async def test_capture_parses_fenced_json_lessons(self, store):
        """EpisodeStore._extract_lessons unwraps a ```json fenced array."""
        fenced = '```json\n["Check sub-totals carefully", "Verify period dates"]\n```'
        with patch(
            "litellm.acompletion",
            new_callable=AsyncMock,
            return_value=_fenced_llm_response(fenced),
        ):
            ep = Episode(goal="Audit revenue", outcome="done", success=True)
            captured = await store.capture(ep, extract_lessons=True)
        assert captured.lessons == [
            "Check sub-totals carefully",
            "Verify period dates",
        ]

    @pytest.mark.asyncio
    async def test_extract_lessons_static_parses_fenced_json(self):
        """_extract_lessons_static unwraps a bare ``` fenced array."""
        fenced = '```\n["lesson one", "lesson two"]\n```'
        with patch(
            "litellm.acompletion",
            new_callable=AsyncMock,
            return_value=_fenced_llm_response(fenced),
        ):
            lessons = await _extract_lessons_static(
                Episode(goal="ship release", success=True), "gpt-4o-mini"
            )
        assert lessons == ["lesson one", "lesson two"]


class TestFormatForPrompt:
    def test_empty(self):
        store = EpisodeStore()
        assert store.format_for_prompt([]) == ""

    def test_formats_episodes(self):
        store = EpisodeStore()
        eps = [
            Episode(
                goal="Audit revenue",
                outcome="Found issues",
                success=True,
                lessons=["Check sub-totals carefully"],
            ),
        ]
        text = store.format_for_prompt(eps)
        assert "Past experiences" in text
        assert "Audit revenue" in text
        assert "Check sub-totals carefully" in text
        assert "SUCCESS" in text
