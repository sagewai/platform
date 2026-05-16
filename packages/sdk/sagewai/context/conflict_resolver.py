# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Conflict detection and resolution for context facts.

Detects when new information contradicts stored context and provides
mechanisms for automatic or user-driven resolution.
"""

from __future__ import annotations

import logging
from typing import Any

from sagewai.context.models import ContextChunk, ContextScope
from sagewai.context.stores import ContextMetadataStore, ContextVectorStore

logger = logging.getLogger(__name__)


class ConflictDetector:
    """Detect conflicting facts in the context store.

    A conflict occurs when two chunks have high semantic similarity
    but textually different content, suggesting one may supersede the other.

    Usage::

        detector = ConflictDetector(
            metadata_store=pg_store,
            vector_store=milvus_store,
        )
        conflicts = await detector.check_new_chunk(chunk, existing_chunks)
    """

    def __init__(
        self,
        *,
        metadata_store: ContextMetadataStore,
        vector_store: ContextVectorStore,
        similarity_threshold: float = 0.9,
    ) -> None:
        self.metadata_store = metadata_store
        self.vector_store = vector_store
        self.similarity_threshold = similarity_threshold

    async def check_new_chunk(
        self,
        new_chunk_vector: list[float],
        new_chunk_content: str,
        scope: ContextScope,
        scope_id: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """Check if a new chunk conflicts with existing chunks.

        Returns a list of potential conflicts with metadata.
        """
        # Search for similar existing chunks
        filters = {
            "project_id": project_id,
            "scope": scope.value,
            "scope_id": scope_id,
        }
        similar = await self.vector_store.search(
            query_vector=new_chunk_vector,
            top_k=5,
            filters=filters,
        )

        conflicts: list[dict[str, Any]] = []
        for chunk_id, similarity in similar:
            if similarity < self.similarity_threshold:
                continue

            existing = await self.metadata_store.get_chunk(chunk_id)
            if not existing:
                continue

            # If content is identical, it's a duplicate not a conflict
            if existing.content.strip() == new_chunk_content.strip():
                continue

            conflicts.append({
                "existing_chunk_id": chunk_id,
                "existing_content": existing.content,
                "new_content": new_chunk_content,
                "similarity": similarity,
                "scope": scope.value,
                "scope_id": scope_id,
            })

        if conflicts:
            logger.info(
                "Detected %d potential conflicts in scope %s/%s",
                len(conflicts),
                scope.value,
                scope_id,
            )

        return conflicts

    async def auto_resolve(
        self,
        conflicts: list[dict[str, Any]],
        strategy: str = "keep_newer",
    ) -> int:
        """Automatically resolve conflicts.

        Strategies:
        - ``keep_newer``: mark old chunks with reduced importance (default)
        - ``keep_both``: do nothing, let user decide
        """
        if strategy == "keep_both":
            return 0

        resolved = 0
        for conflict in conflicts:
            chunk_id = conflict["existing_chunk_id"]
            existing = await self.metadata_store.get_chunk(chunk_id)
            if existing and existing.importance > 0.1:
                existing.importance = max(0.0, existing.importance * 0.3)
                await self.metadata_store.update_chunk(existing)
                resolved += 1
                logger.debug(
                    "Auto-resolved conflict: reduced importance of chunk %s",
                    chunk_id,
                )

        return resolved
