# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Adaptive memory lifecycle management — compress, archive, discard."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from sagewai.context.ingestion import _FALLBACK_ERRORS
from sagewai.context.models import ContextChunk, ContextDocument, ContextScope
from sagewai.context.stores import ContextMetadataStore, ContextVectorStore

logger = logging.getLogger(__name__)


class LifecycleReport(BaseModel):
    """Summary of a maintenance cycle run."""

    project_id: str
    chunks_compressed: int = 0
    documents_archived: int = 0
    chunks_discarded: int = 0
    importance_refreshed: int = 0
    conflicts_detected: int = 0
    duration_ms: float = 0.0


class ConflictPair(BaseModel):
    """Two chunks with contradicting information."""

    chunk_a_id: str
    chunk_b_id: str
    chunk_a_content: str
    chunk_b_content: str
    similarity: float
    scope: ContextScope
    scope_id: str


class LifecycleConfig(BaseModel):
    """Configuration for lifecycle management behavior."""

    decay_rate: float = Field(default=0.05, description="Importance decay per week of inactivity")
    compress_age_days: int = Field(default=90, description="Compress chunks older than this")
    compress_min_importance: float = Field(default=0.1, description="Compress below this importance")
    archive_importance: float = Field(default=0.05, description="Archive docs below this importance")
    discard_age_days: int = Field(default=365, description="Discard chunks older than this")
    auto_trigger_threshold: int = Field(
        default=10000, description="Auto-trigger maintenance above this chunk count"
    )


class LifecycleManager:
    """Adaptive memory management — compress, archive, discard.

    Manages the lifecycle of context chunks based on access patterns,
    age, and importance scoring. Can be run on a schedule or triggered
    by storage thresholds.

    Usage::

        mgr = LifecycleManager(
            metadata_store=pg_store,
            vector_store=milvus_store,
            config=LifecycleConfig(decay_rate=0.03),
        )
        report = await mgr.run_maintenance("project-123")
    """

    def __init__(
        self,
        *,
        metadata_store: ContextMetadataStore,
        vector_store: ContextVectorStore,
        config: LifecycleConfig | None = None,
    ) -> None:
        self.metadata_store = metadata_store
        self.vector_store = vector_store
        self.config = config or LifecycleConfig()

    async def run_maintenance(self, project_id: str) -> LifecycleReport:
        """Full maintenance cycle for a project's context store."""
        start = time.monotonic()
        report = LifecycleReport(project_id=project_id)

        try:
            report.importance_refreshed = await self.refresh_importance(project_id)
            report.chunks_compressed = await self.compress_stale(project_id)
            report.documents_archived = await self.archive_low_importance(project_id)
            report.chunks_discarded = await self.discard_old(project_id)
        except (OSError, RuntimeError, ValueError, ConnectionError) as exc:
            logger.error("Maintenance failed for project %s: %s", project_id, exc)

        report.duration_ms = (time.monotonic() - start) * 1000
        logger.info(
            "Maintenance for %s: compressed=%d, archived=%d, discarded=%d (%.0fms)",
            project_id,
            report.chunks_compressed,
            report.documents_archived,
            report.chunks_discarded,
            report.duration_ms,
        )
        return report

    async def refresh_importance(self, project_id: str) -> int:
        """Decay importance scores for chunks not recently accessed.

        Decay rate is configurable via ``LifecycleConfig.decay_rate``
        (default 5% per week of inactivity).
        """
        decay_factor = 1.0 - self.config.decay_rate
        docs = await self.metadata_store.list_documents(project_id=project_id, status="ready")
        count = 0
        now = datetime.now(timezone.utc)

        for doc in docs:
            chunks = await self.metadata_store.get_chunks(doc.id)
            for chunk in chunks:
                if chunk.last_accessed_at:
                    days_since = (now - chunk.last_accessed_at).total_seconds() / 86400
                else:
                    days_since = (now - chunk.created_at).total_seconds() / 86400

                weeks = days_since / 7
                if weeks >= 1:
                    new_importance = max(0.0, chunk.importance * (decay_factor ** weeks))
                    if abs(new_importance - chunk.importance) > 0.01:
                        chunk.importance = round(new_importance, 4)
                        await self.metadata_store.update_chunk(chunk)
                        count += 1

        return count

    async def compress_stale(
        self,
        project_id: str,
        max_age_days: int | None = None,
        min_importance: float | None = None,
        model: str = "gpt-4o-mini",
    ) -> int:
        """Compress old low-importance chunks by summarizing them via LLM.

        Finds chunks older than ``max_age_days`` with importance below
        ``min_importance``, groups them by document, summarizes each group
        into a single condensed chunk, and replaces the originals.
        """
        age_threshold = max_age_days if max_age_days is not None else self.config.compress_age_days
        imp_threshold = min_importance if min_importance is not None else self.config.compress_min_importance
        docs = await self.metadata_store.list_documents(project_id=project_id, status="ready")
        compressed = 0
        now = datetime.now(timezone.utc)

        for doc in docs:
            chunks = await self.metadata_store.get_chunks(doc.id)
            stale = [
                c for c in chunks
                if (now - c.created_at).total_seconds() / 86400 > age_threshold
                and c.importance < imp_threshold
            ]
            if len(stale) < 2:
                continue

            # Summarize stale chunks via LLM
            combined = "\n\n".join(c.content for c in stale)
            summary = await self._summarize(combined, model)
            if not summary:
                continue

            stale_ids = [c.id for c in stale]

            # Save summary FIRST, then delete originals — prevents data loss
            # if deletion succeeds but summary save fails
            from sagewai.context.chunking import _content_hash, _count_tokens

            import uuid

            from sagewai.context.models import ContextChunk

            summary_chunk = ContextChunk(
                id=str(uuid.uuid4()),
                document_id=doc.id,
                scope=doc.scope,
                scope_id=doc.scope_id,
                project_id=doc.project_id,
                content=summary,
                chunk_index=0,
                token_count=_count_tokens(summary),
                embedding_model="text-embedding-3-small",
                content_hash=_content_hash(summary),
                importance=0.2,
                metadata={"compressed_from": len(stale), "original_chunks": stale_ids},
                created_at=now,
            )
            try:
                await self.metadata_store.save_chunks([summary_chunk])
            except (OSError, RuntimeError, ValueError) as exc:
                logger.error("Failed to save summary chunk for doc %s: %s", doc.id, exc)
                continue  # Don't delete originals if summary save failed

            # Now safe to delete originals
            await self.vector_store.delete(stale_ids)
            for sid in stale_ids:
                await self.metadata_store.delete_chunk(sid)
            compressed += len(stale)

            logger.info(
                "Compressed %d stale chunks → 1 summary for doc %s",
                len(stale),
                doc.id,
            )

        return compressed

    @staticmethod
    async def _summarize(text: str, model: str) -> str:
        """Summarize text using LLM, with truncation fallback."""
        try:
            import litellm

            response = await litellm.acompletion(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Summarize the following content into a concise paragraph "
                            "that preserves all key facts, decisions, and entities. "
                            "Be thorough but brief.\n\n"
                            f"{text[:8000]}"
                        ),
                    }
                ],
                temperature=0.2,
                max_tokens=500,
            )
            return response.choices[0].message.content or text[:500]
        except _FALLBACK_ERRORS:
            logger.info("LLM summarization unavailable, using truncation fallback")
            # Fallback: first 500 chars of combined text
            return text[:500] if text else ""

    async def archive_low_importance(
        self,
        project_id: str,
        max_importance: float = 0.05,
    ) -> int:
        """Archive documents where all chunks have very low importance."""
        docs = await self.metadata_store.list_documents(project_id=project_id, status="ready")
        archived = 0

        for doc in docs:
            chunks = await self.metadata_store.get_chunks(doc.id)
            if not chunks:
                continue

            if all(c.importance < max_importance for c in chunks):
                doc.status = "archived"
                await self.metadata_store.update_document(doc)
                # Remove vectors from search index to save memory
                await self.vector_store.delete([c.id for c in chunks])
                archived += 1
                logger.debug("Archived document %s (%s)", doc.id, doc.title)

        return archived

    async def discard_old(
        self,
        project_id: str,
        max_age_days: int = 365,
        max_access_count: int = 0,
    ) -> int:
        """Permanently remove archived documents older than retention period."""
        docs = await self.metadata_store.list_documents(
            project_id=project_id, status="archived"
        )
        discarded = 0
        now = datetime.now(timezone.utc)

        for doc in docs:
            age_days = (now - doc.created_at).total_seconds() / 86400
            if age_days > max_age_days:
                await self.metadata_store.delete_document(doc.id)
                discarded += 1
                logger.debug("Discarded document %s (%s, age=%dd)", doc.id, doc.title, age_days)

        return discarded

    async def detect_conflicts(
        self,
        project_id: str,
        scope: ContextScope | None = None,
        scope_id: str | None = None,
    ) -> list[ConflictPair]:
        """Find chunks with potentially contradicting information.

        Looks for chunks with high content similarity but different text,
        which may indicate outdated or conflicting facts.

        This is a lightweight check based on content hashes — full semantic
        conflict detection requires embedding comparison and is more expensive.
        """
        # For now, detect exact-scope duplicates with different content
        docs = await self.metadata_store.list_documents(
            project_id=project_id, scope=scope, scope_id=scope_id, status="ready"
        )
        conflicts: list[ConflictPair] = []

        # Group chunks by scope to find intra-scope conflicts
        scope_chunks: dict[tuple[str, str], list[ContextChunk]] = {}
        for doc in docs:
            chunks = await self.metadata_store.get_chunks(doc.id)
            for chunk in chunks:
                key = (chunk.scope.value, chunk.scope_id)
                scope_chunks.setdefault(key, []).append(chunk)

        # Check for near-duplicate chunks with different content
        for (sc, sid), chunks in scope_chunks.items():
            seen_hashes: dict[str, ContextChunk] = {}
            for chunk in chunks:
                for existing_hash, existing_chunk in seen_hashes.items():
                    # Different content hash but check for textual overlap
                    if chunk.content_hash == existing_chunk.content_hash:
                        continue  # exact duplicate, not a conflict

                    # Simple heuristic: if chunks share >60% of words, they may conflict
                    words_a = set(chunk.content.lower().split())
                    words_b = set(existing_chunk.content.lower().split())
                    if not words_a or not words_b:
                        continue
                    overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
                    if overlap > 0.6:
                        conflicts.append(ConflictPair(
                            chunk_a_id=existing_chunk.id,
                            chunk_b_id=chunk.id,
                            chunk_a_content=existing_chunk.content[:200],
                            chunk_b_content=chunk.content[:200],
                            similarity=round(overlap, 3),
                            scope=ContextScope(sc),
                            scope_id=sid,
                        ))

                seen_hashes[chunk.content_hash] = chunk

        if conflicts:
            logger.info(
                "Detected %d conflicts in project %s", len(conflicts), project_id
            )
        return conflicts

    async def resolve_conflict(
        self, keep_chunk_id: str, discard_chunk_id: str
    ) -> None:
        """User-driven conflict resolution: keep one chunk, discard the other."""
        discard = await self.metadata_store.get_chunk(discard_chunk_id)
        if discard:
            await self.vector_store.delete([discard_chunk_id])
            discard.importance = 0.0
            await self.metadata_store.update_chunk(discard)
            logger.info(
                "Conflict resolved: kept %s, discarded %s",
                keep_chunk_id,
                discard_chunk_id,
            )
