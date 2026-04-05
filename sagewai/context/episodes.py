# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Episodic memory — agents learn from past task executions.

Captures the full context of a completed task: goal, context used, actions
taken, outcome, and lessons learned. On future similar tasks, relevant
episodes are retrieved to inform strategy.

This gives agents *experience*, not just *knowledge*.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sagewai.intelligence.embeddings.protocol import Embedder

from pydantic import BaseModel, Field

from sagewai.context.ingestion import _FALLBACK_ERRORS
from sagewai.context.stores import ContextVectorStore

logger = logging.getLogger(__name__)


class Episode(BaseModel):
    """A structured record of a completed agent task."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = "default"
    agent_name: str = ""
    scope: str = "project"

    # What happened
    goal: str = Field(description="What the agent was trying to accomplish")
    context_used: list[str] = Field(
        default_factory=list, description="Chunk IDs retrieved during task"
    )
    actions_taken: list[str] = Field(
        default_factory=list, description="Tool calls and key decisions"
    )
    outcome: str = Field(default="", description="Final result summary")
    success: bool = True

    # Learning
    lessons: list[str] = Field(
        default_factory=list, description="Key takeaways from this task"
    )

    # Metrics
    duration_seconds: float = 0
    token_count: int = 0
    cost_usd: float = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EpisodeStore:
    """In-memory episode store with vector-based retrieval.

    For persistent storage across restarts, use ``PersistentEpisodeStore``
    which delegates to a ``ContextEngine`` instance.

    Usage::

        store = EpisodeStore(vector_store=InMemoryVectorStore())
        await store.capture(episode)
        similar = await store.retrieve("audit financial statements", top_k=3)
    """

    def __init__(
        self,
        *,
        vector_store: ContextVectorStore | None = None,
        model: str = "gpt-4o-mini",
        embedder: Embedder | None = None,
    ) -> None:
        self._episodes: dict[str, Episode] = {}
        self._vector_store = vector_store
        self._model = model
        self._embedder = embedder

    async def capture(
        self,
        episode: Episode,
        extract_lessons: bool = True,
    ) -> Episode:
        """Capture a completed episode, optionally extracting lessons via LLM.

        Parameters
        ----------
        episode:
            The episode to store.
        extract_lessons:
            If True and episode.lessons is empty, run LLM to extract lessons.
        """
        if extract_lessons and not episode.lessons:
            episode.lessons = await self._extract_lessons(episode)

        self._episodes[episode.id] = episode

        # Embed the goal for similarity retrieval
        if self._vector_store:
            goal_vector = await self._embed(episode.goal)
            await self._vector_store.insert(
                chunk_id=episode.id,
                vector=goal_vector,
                metadata={
                    "project_id": episode.project_id,
                    "agent_name": episode.agent_name,
                    "success": str(episode.success),
                },
            )

        logger.info(
            "Captured episode %s: goal='%s', success=%s, lessons=%d",
            episode.id,
            episode.goal[:60],
            episode.success,
            len(episode.lessons),
        )
        return episode

    async def retrieve(
        self,
        goal: str,
        top_k: int = 3,
        agent_name: str | None = None,
        only_successful: bool = False,
    ) -> list[Episode]:
        """Find episodes with similar goals.

        Parameters
        ----------
        goal:
            The current task goal to match against past episodes.
        top_k:
            Maximum episodes to return.
        agent_name:
            Filter to a specific agent's episodes.
        only_successful:
            Only return episodes where the task succeeded.
        """
        if self._vector_store:
            goal_vector = await self._embed(goal)
            filters: dict[str, str] = {}
            if agent_name:
                filters["agent_name"] = agent_name
            if only_successful:
                filters["success"] = "True"

            hits = await self._vector_store.search(
                goal_vector, top_k=top_k, filters=filters
            )

            episodes = []
            for ep_id, _score in hits:
                ep = self._episodes.get(ep_id)
                if ep:
                    episodes.append(ep)
            return episodes

        # Fallback: return most recent episodes
        all_eps = sorted(
            self._episodes.values(), key=lambda e: e.created_at, reverse=True
        )
        if agent_name:
            all_eps = [e for e in all_eps if e.agent_name == agent_name]
        if only_successful:
            all_eps = [e for e in all_eps if e.success]
        return all_eps[:top_k]

    def get(self, episode_id: str) -> Episode | None:
        """Get an episode by ID."""
        return self._episodes.get(episode_id)

    def list_all(
        self,
        project_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[Episode]:
        """List all episodes with optional filtering."""
        eps = list(self._episodes.values())
        if project_id:
            eps = [e for e in eps if e.project_id == project_id]
        if agent_name:
            eps = [e for e in eps if e.agent_name == agent_name]
        return sorted(eps, key=lambda e: e.created_at, reverse=True)

    def format_for_prompt(self, episodes: list[Episode]) -> str:
        """Format episodes as context for injection into system prompt."""
        if not episodes:
            return ""

        lines = ["[Past experiences with similar tasks]"]
        for i, ep in enumerate(episodes, 1):
            status = "SUCCESS" if ep.success else "FAILED"
            lines.append(f"\nExperience {i} ({status}):")
            lines.append(f"  Goal: {ep.goal}")
            if ep.outcome:
                lines.append(f"  Outcome: {ep.outcome[:200]}")
            if ep.lessons:
                lines.append("  Lessons learned:")
                for lesson in ep.lessons[:5]:
                    lines.append(f"    - {lesson}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _extract_lessons(self, episode: Episode) -> list[str]:
        """Extract lessons from an episode using LLM."""
        try:
            import litellm

            prompt = (
                "Given this completed task, extract 2-5 key lessons learned. "
                "Return a JSON array of strings.\n\n"
                f"Goal: {episode.goal}\n"
                f"Actions: {', '.join(episode.actions_taken[:10])}\n"
                f"Outcome: {episode.outcome[:500]}\n"
                f"Success: {episode.success}\n\n"
                "Lessons (JSON array):"
            )

            response = await litellm.acompletion(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=300,
            )

            import json

            text = (response.choices[0].message.content or "[]").strip()
            # Strip markdown code fences that LLMs frequently add
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                lessons = json.loads(text)
                if isinstance(lessons, list):
                    return [str(l) for l in lessons[:5]]
            except json.JSONDecodeError:
                pass

        except _FALLBACK_ERRORS:
            logger.info("LLM lesson extraction unavailable")

        # Fallback: generate basic lessons from metadata
        lessons = []
        if episode.success:
            lessons.append(f"Successfully completed: {episode.goal[:100]}")
        else:
            lessons.append(f"Failed to complete: {episode.goal[:100]}. Review approach.")
        return lessons

    async def _embed(self, text: str) -> list[float]:
        """Embed text for similarity search.

        Uses the configured ``Embedder`` when available, otherwise falls
        back to the legacy ``litellm.aembedding`` path with hash fallback.
        """
        if self._embedder is not None:
            return await self._embedder.embed_query(text)

        # Legacy path
        try:
            import litellm

            response = await litellm.aembedding(
                model="text-embedding-3-small", input=[text]
            )
            return response.data[0]["embedding"]
        except _FALLBACK_ERRORS:
            from sagewai.context.ingestion import IngestionPipeline

            return IngestionPipeline._hash_vector(text)


class PersistentEpisodeStore:
    """Episode store backed by ContextEngine for durable persistence.

    Episodes are serialized to JSON and stored as context documents with
    ``source=ContextSource.EPISODE``. Retrieval uses the context engine's
    multi-strategy search (vector + BM25).

    Usage::

        engine = ContextEngine(metadata_store=..., vector_store=...)
        store = PersistentEpisodeStore(context_engine=engine)
        await store.capture(episode)

        # After restart — episodes survive:
        engine2 = ContextEngine(metadata_store=..., vector_store=...)
        store2 = PersistentEpisodeStore(context_engine=engine2)
        similar = await store2.retrieve("audit financial data")
    """

    def __init__(
        self,
        *,
        context_engine: Any,
        model: str = "gpt-4o-mini",
    ) -> None:
        self._engine = context_engine
        self._model = model
        # Local cache for fast get() within same process
        self._cache: dict[str, Episode] = {}

    async def capture(
        self,
        episode: Episode,
        extract_lessons: bool = True,
    ) -> Episode:
        """Capture an episode and persist it via ContextEngine."""
        if extract_lessons and not episode.lessons:
            episode.lessons = await _extract_lessons_static(episode, self._model)

        import json

        from sagewai.context.models import ContextScope, ContextSource

        episode_json = episode.model_dump_json()

        # Store the goal as text (for search) with full episode in metadata
        await self._engine.ingest_text(
            text=f"Episode: {episode.goal}\nOutcome: {episode.outcome}\n"
            f"Lessons: {'; '.join(episode.lessons)}",
            title=f"Episode: {episode.goal[:60]}",
            scope=ContextScope.PROJECT,
            scope_id=self._engine.project_id,
            source=ContextSource.EPISODE,
            metadata={
                "episode_id": episode.id,
                "episode_data": json.loads(episode_json),
                "success": episode.success,
                "agent_name": episode.agent_name,
            },
        )

        self._cache[episode.id] = episode
        logger.info(
            "Persisted episode %s: goal='%s', success=%s",
            episode.id,
            episode.goal[:60],
            episode.success,
        )
        return episode

    async def retrieve(
        self,
        goal: str,
        top_k: int = 3,
        agent_name: str | None = None,
        only_successful: bool = False,
    ) -> list[Episode]:
        """Find episodes with similar goals via ContextEngine search."""
        from sagewai.context.models import ContextSource

        results = await self._engine.search(
            query=f"Episode: {goal}",
            top_k=top_k * 2,  # over-fetch then filter
        )

        episodes: list[Episode] = []
        seen_docs: set[str] = set()

        for r in results:
            if r.document_id in seen_docs:
                continue
            seen_docs.add(r.document_id)

            # Fetch document to get episode_data from document metadata
            doc = await self._engine.metadata_store.get_document(r.document_id)
            if not doc or doc.source != ContextSource.EPISODE:
                continue

            ep_data = doc.metadata.get("episode_data")
            if not ep_data:
                continue

            try:
                ep = Episode(**ep_data)
            except (TypeError, ValueError):
                continue

            if agent_name and ep.agent_name != agent_name:
                continue
            if only_successful and not ep.success:
                continue

            episodes.append(ep)
            if len(episodes) >= top_k:
                break

        return episodes

    def get(self, episode_id: str) -> Episode | None:
        """Get an episode by ID (from local cache)."""
        return self._cache.get(episode_id)

    def list_all(
        self,
        project_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[Episode]:
        """List cached episodes (local process only)."""
        eps = list(self._cache.values())
        if project_id:
            eps = [e for e in eps if e.project_id == project_id]
        if agent_name:
            eps = [e for e in eps if e.agent_name == agent_name]
        return sorted(eps, key=lambda e: e.created_at, reverse=True)

    @staticmethod
    def format_for_prompt(episodes: list[Episode]) -> str:
        """Format episodes for system prompt injection."""
        if not episodes:
            return ""
        lines = ["[Past experiences with similar tasks]"]
        for i, ep in enumerate(episodes, 1):
            status = "SUCCESS" if ep.success else "FAILED"
            lines.append(f"\nExperience {i} ({status}):")
            lines.append(f"  Goal: {ep.goal}")
            if ep.outcome:
                lines.append(f"  Outcome: {ep.outcome[:200]}")
            if ep.lessons:
                lines.append("  Lessons learned:")
                for lesson in ep.lessons[:5]:
                    lines.append(f"    - {lesson}")
        return "\n".join(lines)


async def _extract_lessons_static(episode: Episode, model: str) -> list[str]:
    """Shared lesson extraction logic for both store implementations."""
    try:
        import json

        import litellm

        prompt = (
            "Given this completed task, extract 2-5 key lessons learned. "
            "Return a JSON array of strings.\n\n"
            f"Goal: {episode.goal}\n"
            f"Actions: {', '.join(episode.actions_taken[:10])}\n"
            f"Outcome: {episode.outcome[:500]}\n"
            f"Success: {episode.success}\n\n"
            "Lessons (JSON array):"
        )

        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300,
        )

        text = (response.choices[0].message.content or "[]").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            lessons = json.loads(text)
            if isinstance(lessons, list):
                return [str(l) for l in lessons[:5]]
        except json.JSONDecodeError:
            pass

    except _FALLBACK_ERRORS:
        logger.info("LLM lesson extraction unavailable")

    lessons = []
    if episode.success:
        lessons.append(f"Successfully completed: {episode.goal[:100]}")
    else:
        lessons.append(f"Failed to complete: {episode.goal[:100]}. Review approach.")
    return lessons
