# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""ContextEngine — unified context creation and management for AI agents.

Implements the ``MemoryProvider`` protocol so it can be passed directly to
``BaseAgent(memory=engine)`` for zero-change agent integration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sagewai.intelligence.embeddings.protocol import Embedder

from sagewai.context.ingestion import IngestionPipeline, _FALLBACK_ERRORS
from sagewai.context.models import (
    ChunkingConfig,
    ContextDocument,
    ContextScope,
    ContextSearchResult,
    ContextSource,
)
from sagewai.context.stores import ContextMetadataStore, ContextVectorStore
from sagewai.core.context import resolve_project_id

logger = logging.getLogger(__name__)


class ContextEngine:
    """Unified context creation and management for AI agents.

    Implements ``MemoryProvider`` — pass directly to ``BaseAgent(memory=engine)``.

    Handles:
    - Universal data ingestion (files, directories, URLs, text)
    - Scoped retrieval with inheritance (org → project → agent → user)
    - Automatic deduplication across scopes
    - Composite scoring (similarity + recency + importance)

    Usage::

        engine = ContextEngine(
            metadata_store=InMemoryMetadataStore(),
            vector_store=InMemoryVectorStore(),
        )
        doc = await engine.ingest_file(data, "report.pdf",
                                        scope=ContextScope.PROJECT, scope_id="acme")
        results = await engine.retrieve("quarterly revenue")
    """

    def __init__(
        self,
        *,
        metadata_store: ContextMetadataStore,
        vector_store: ContextVectorStore,
        graph_store: Any | None = None,
        embedding_model: str = "text-embedding-3-small",
        chunking_config: ChunkingConfig | None = None,
        project_id: str | None = None,
        # Scope context for scoped retrieval
        org_id: str | None = None,
        # Retrieval options
        reranker: Any | None = None,
        enable_bm25: bool = True,
        event_callback: Any | None = None,
        lifecycle_config: Any | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.metadata_store = metadata_store
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.embedding_model = embedding_model
        self.chunking_config = chunking_config or ChunkingConfig()
        self._project_id = project_id
        self._org_id = org_id
        self._reranker = reranker
        self._enable_bm25 = enable_bm25
        self._event_callback = event_callback
        self._embedder = embedder

        # BM25 index — lazily populated
        self._bm25_index: Any | None = None

        # Lifecycle config for auto-trigger
        self._lifecycle_config = lifecycle_config
        self._lifecycle_running = False

        # Circuit breaker for embedding API failures (legacy path only)
        self._embed_failures: int = 0
        self._embed_circuit_open_until: float = 0.0
        self._embed_failure_threshold: int = 3
        self._embed_circuit_cooldown: float = 60.0  # seconds

        self._pipeline = IngestionPipeline(
            metadata_store=metadata_store,
            vector_store=vector_store,
            embedding_model=embedding_model,
            chunking_config=self.chunking_config,
            embedder=embedder,
        )

    @property
    def project_id(self) -> str:
        return resolve_project_id(self._project_id)

    # ------------------------------------------------------------------
    # MemoryProvider protocol
    # ------------------------------------------------------------------

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        """Retrieve relevant context strings (MemoryProvider protocol).

        Searches all applicable scopes, merges, deduplicates, and returns
        the top_k most relevant chunks as plain strings.
        """
        results = await self.search(query, top_k=top_k)
        return [r.content for r in results]

    async def store(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        """Store content in context (MemoryProvider protocol).

        Defaults to project scope.
        """
        await self._pipeline.ingest_text(
            text=content,
            title=metadata.get("title", "untitled") if metadata else "untitled",
            scope=ContextScope.PROJECT,
            scope_id=self.project_id,
            project_id=self.project_id,
            source=ContextSource.MANUAL,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Rich search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        top_k: int = 5,
        scopes: list[ContextScope] | None = None,
        sources: list[ContextSource] | None = None,
        tags: list[str] | None = None,
    ) -> list[ContextSearchResult]:
        """Multi-strategy search: vector + BM25 + graph, merged via RRF, re-ranked.

        Pipeline:
        1. Run vector search + BM25 keyword search (+ graph if available) in parallel
        2. Merge results via Reciprocal Rank Fusion
        3. Re-rank top candidates (if reranker configured)
        4. Apply composite scoring (recency + importance)
        5. Deduplicate by content hash (most specific scope wins)
        """
        from sagewai.context.reranker import reciprocal_rank_fusion

        t0 = time.time()
        self._emit_event("CONTEXT_SEARCH_STARTED", {"query": query[:200], "top_k": top_k})

        scope_filters = self._build_scope_filters(scopes)
        fetch_k = top_k * 3  # over-fetch for RRF merging

        # --- Pre-filter by tags at document level ---
        # Tags are a document-level attribute, not stored in chunk vectors.
        # When tags are specified, find matching document IDs first, then
        # constrain chunk results to those documents post-retrieval.
        tag_doc_ids: set[str] | None = None
        if tags:
            tagged_docs = await self.metadata_store.list_documents(
                project_id=self.project_id, tags=tags,
            )
            tag_doc_ids = {d.id for d in tagged_docs}
            if not tag_doc_ids:
                return []  # no documents match the tags

        # --- Strategy 1: Vector search ---
        query_vector = await self._embed_query(query)
        vector_tasks = []
        for scope, scope_id in scope_filters:
            filters: dict[str, Any] = {
                "project_id": self.project_id,
                "scope": scope.value,
                "scope_id": scope_id,
            }
            vector_tasks.append(
                self.vector_store.search(query_vector, top_k=fetch_k, filters=filters)
            )

        # --- Strategy 2: BM25 keyword search ---
        bm25_task = self._bm25_search(query, fetch_k) if self._enable_bm25 else None

        # --- Strategy 3: Graph search ---
        graph_task = self._graph_search(query, fetch_k) if self.graph_store else None

        # --- Run all in parallel ---
        all_tasks: list[Any] = vector_tasks[:]
        if bm25_task is not None:
            all_tasks.append(bm25_task)
        if graph_task is not None:
            all_tasks.append(graph_task)

        raw_results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # Split results back
        n_vector = len(vector_tasks)
        vector_results = raw_results[:n_vector]
        idx = n_vector
        bm25_results = raw_results[idx] if bm25_task is not None else []
        if bm25_task is not None:
            idx += 1
        graph_results = raw_results[idx] if graph_task is not None else []

        # Collect vector hits
        vector_hits: list[tuple[str, float]] = []
        for result in vector_results:
            if isinstance(result, (Exception, BaseException)):
                continue
            vector_hits.extend(result)

        # Collect BM25 hits
        bm25_hits: list[tuple[str, float]] = []
        if not isinstance(bm25_results, (Exception, BaseException)):
            bm25_hits = bm25_results if isinstance(bm25_results, list) else []

        # Collect graph hits
        graph_hits: list[tuple[str, float]] = []
        if not isinstance(graph_results, (Exception, BaseException)):
            graph_hits = graph_results if isinstance(graph_results, list) else []

        # --- Merge via RRF ---
        result_sets = [vector_hits]
        if bm25_hits:
            result_sets.append(bm25_hits)
        if graph_hits:
            result_sets.append(graph_hits)

        merged = reciprocal_rank_fusion(result_sets)

        # --- Pre-fetch all chunks into a local cache to avoid N+1 ---
        chunk_cache: dict[str, Any] = {}
        for chunk_id, _ in merged:
            if chunk_id not in chunk_cache:
                chunk_cache[chunk_id] = await self.metadata_store.get_chunk(chunk_id)

        # --- Filter by tag-matching document IDs ---
        if tag_doc_ids is not None:
            chunk_cache = {
                cid: c for cid, c in chunk_cache.items()
                if c and c.document_id in tag_doc_ids
            }
            merged = [(cid, s) for cid, s in merged if cid in chunk_cache]

        # --- Deduplicate by content hash (most specific scope wins) ---
        scope_priority = {
            ContextScope.PROJECT: 0,
            ContextScope.ORG: 1,
        }

        seen_hashes: dict[str, str] = {}  # content_hash → chunk_id
        deduped: list[tuple[str, float]] = []

        for chunk_id, rrf_score in merged:
            chunk = chunk_cache.get(chunk_id)
            if not chunk:
                continue
            h = chunk.content_hash
            if h in seen_hashes:
                existing = chunk_cache.get(seen_hashes[h])
                if existing and scope_priority.get(chunk.scope, 99) < scope_priority.get(
                    existing.scope, 99
                ):
                    deduped = [(cid, s) if cid != seen_hashes[h] else (chunk_id, rrf_score)
                               for cid, s in deduped]
                    seen_hashes[h] = chunk_id
                continue
            seen_hashes[h] = chunk_id
            deduped.append((chunk_id, rrf_score))

        # --- Re-rank top candidates ---
        candidates = deduped[: top_k * 3]
        if self._reranker and len(candidates) > 1:
            try:
                contents = [
                    chunk_cache[cid].content if chunk_cache.get(cid) else ""
                    for cid, _ in candidates
                ]
                reranked = await self._reranker.rerank(query, contents, top_k=top_k)
                candidates = [(candidates[idx][0], score) for idx, score in reranked]
            except _FALLBACK_ERRORS:
                logger.debug("Re-ranking failed, using RRF ordering")

        # --- Build results with composite scoring ---
        results: list[ContextSearchResult] = []
        now_ts = time.time()
        doc_cache: dict[str, Any] = {}

        for chunk_id, base_score in candidates[:top_k]:
            chunk = chunk_cache.get(chunk_id)
            if not chunk:
                continue

            if chunk.document_id not in doc_cache:
                doc_cache[chunk.document_id] = await self.metadata_store.get_document(
                    chunk.document_id
                )
            doc = doc_cache[chunk.document_id]
            doc_title = doc.title if doc else "unknown"
            doc_source = doc.source if doc else ContextSource.MANUAL

            recency = 1.0
            if chunk.created_at:
                age_days = (now_ts - chunk.created_at.timestamp()) / 86400
                recency = max(0.0, 1.0 - age_days / 365)

            composite = base_score * 0.6 + recency * 0.2 + chunk.importance * 0.2

            results.append(
                ContextSearchResult(
                    chunk_id=chunk_id,
                    document_id=chunk.document_id,
                    content=chunk.content,
                    score=composite,
                    scope=chunk.scope,
                    scope_id=chunk.scope_id,
                    document_title=doc_title,
                    source=doc_source,
                    metadata=chunk.metadata,
                )
            )
            await self.metadata_store.update_chunk_access(chunk_id)

        results.sort(key=lambda r: r.score, reverse=True)
        final = results[:top_k]

        self._emit_event("CONTEXT_SEARCH_COMPLETED", {
            "query": query[:200],
            "result_count": len(final),
            "duration_ms": round((time.time() - t0) * 1000, 1),
            "strategies": {
                "vector": len(vector_hits),
                "bm25": len(bm25_hits),
                "graph": len(graph_hits),
            },
        })
        return final

    async def _bm25_search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """Run BM25 keyword search over indexed chunks."""
        if self._bm25_index is None:
            self._bm25_index = await self._build_bm25_index()
        if self._bm25_index is None or len(self._bm25_index) == 0:
            return []
        return self._bm25_index.search(query, top_k=top_k)

    async def _graph_search(
        self, query: str, top_k: int
    ) -> list[tuple[str, float]]:
        """Search graph store for entities related to query.

        Extracts key terms from the query, looks them up in the graph,
        retrieves neighbor context, and maps results back to chunk IDs.
        """
        if not self.graph_store:
            return []

        try:
            # Use graph store's retrieve() which does entity lookup
            context_lines = await self.graph_store.retrieve(query, top_k=top_k)
            if not context_lines:
                return []

            # Map graph results back to chunks by searching for matching content
            hits: list[tuple[str, float]] = []
            for i, line in enumerate(context_lines):
                # Search metadata store for chunks containing these entities
                docs = await self.metadata_store.list_documents(
                    project_id=self.project_id, status="ready"
                )
                for doc in docs[:10]:  # limit scan
                    chunks = await self.metadata_store.get_chunks(doc.id)
                    for chunk in chunks:
                        # Check if any entity from graph result appears in chunk
                        entities = line.split()
                        if any(
                            ent.lower() in chunk.content.lower()
                            for ent in entities
                            if len(ent) > 2
                        ):
                            score = 1.0 - (i * 0.1)  # rank-based score
                            hits.append((chunk.id, max(score, 0.1)))
                            break
                    if len(hits) >= top_k:
                        break

            logger.debug("Graph search returned %d hits for '%s'", len(hits), query[:40])
            return hits[:top_k]

        except (ConnectionError, OSError, RuntimeError, ValueError):
            logger.debug("Graph search failed", exc_info=True)
            return []

    async def _build_bm25_index(self) -> Any:
        """Build or rebuild the BM25 index from stored chunks."""
        try:
            from sagewai.context.bm25 import BM25Index

            idx = BM25Index()
            docs = await self.metadata_store.list_documents(project_id=self.project_id)
            for doc in docs:
                if doc.status != "ready":
                    continue
                chunks = await self.metadata_store.get_chunks(doc.id)
                for chunk in chunks:
                    idx.add(chunk.id, chunk.content)
            logger.debug("BM25 index built with %d documents", len(idx))
            return idx
        except ImportError:
            return None

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def ingest_file(
        self,
        file_bytes: bytes,
        filename: str,
        scope: ContextScope,
        scope_id: str,
        enable_graph: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ContextDocument:
        """Ingest a file through the full pipeline."""
        doc = await self._pipeline.ingest_file(
            file_bytes=file_bytes,
            filename=filename,
            scope=scope,
            scope_id=scope_id,
            project_id=self.project_id,
            enable_graph=enable_graph,
            metadata=metadata,
        )

        if enable_graph and self.graph_store:
            await self._extract_graph(doc)

        self._invalidate_bm25()
        return doc

    async def ingest_directory(
        self,
        path: str,
        scope: ContextScope,
        scope_id: str,
        patterns: list[str] | None = None,
        ignore: list[str] | None = None,
        enable_graph: bool = False,
    ) -> list[ContextDocument]:
        """Ingest all files in a directory tree."""
        docs = await self._pipeline.ingest_directory(
            path=path,
            scope=scope,
            scope_id=scope_id,
            project_id=self.project_id,
            patterns=patterns,
            ignore=ignore,
            enable_graph=enable_graph,
        )
        self._invalidate_bm25()
        return docs

    async def ingest_text(
        self,
        text: str,
        title: str,
        scope: ContextScope,
        scope_id: str,
        source: ContextSource = ContextSource.MANUAL,
        metadata: dict[str, Any] | None = None,
    ) -> ContextDocument:
        """Ingest raw text content."""
        t0 = time.time()
        self._emit_event("CONTEXT_INGESTION_STARTED", {
            "title": title, "scope": scope.value, "source": source.value,
        })
        doc = await self._pipeline.ingest_text(
            text=text,
            title=title,
            scope=scope,
            scope_id=scope_id,
            project_id=self.project_id,
            source=source,
            metadata=metadata,
        )
        self._emit_event("CONTEXT_INGESTION_COMPLETED", {
            "document_id": doc.id,
            "title": title,
            "chunk_count": doc.chunk_count,
            "duration_ms": round((time.time() - t0) * 1000, 1),
        })
        self._invalidate_bm25()
        await self._maybe_auto_lifecycle()
        return doc

    async def ingest_url(
        self,
        url: str,
        scope: ContextScope,
        scope_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> ContextDocument:
        """Fetch a URL and ingest its content.

        If a document with the same URL already exists, the old document's
        chunks are superseded (importance set to 0) before ingesting the
        new version. This prevents duplicate/conflicting content.
        """
        from sagewai.context.url_parser import fetch_and_parse

        # Auto-supersede: check if we already have this URL
        existing_docs = await self.metadata_store.list_documents(
            project_id=self.project_id, scope=scope, scope_id=scope_id,
        )
        for old_doc in existing_docs:
            if old_doc.metadata.get("url") == url and old_doc.status == "ready":
                await self._supersede_document(old_doc.id)
                logger.info("Auto-superseded old document %s for URL %s", old_doc.id, url[:80])

        parsed = await fetch_and_parse(url)
        title = parsed.metadata.get("title", url[:80])
        meta = {**(metadata or {}), **parsed.metadata}

        doc = await self._pipeline.ingest_text(
            text=parsed.text,
            title=title,
            scope=scope,
            scope_id=scope_id,
            project_id=self.project_id,
            source=ContextSource.URL,
            metadata=meta,
        )
        self._invalidate_bm25()
        return doc

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        scope: ContextScope | None = None,
        scope_id: str | None = None,
        source: ContextSource | None = None,
        status: str | None = None,
        search: str | None = None,
        tags: list[str] | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ContextDocument]:
        """List documents with optional filtering, search, sorting, and pagination."""
        return await self.metadata_store.list_documents(
            project_id=self.project_id,
            scope=scope,
            scope_id=scope_id,
            source=source,
            status=status,
            search=search,
            tags=tags,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )

    async def count_documents(
        self,
        scope: ContextScope | None = None,
        scope_id: str | None = None,
        source: ContextSource | None = None,
        status: str | None = None,
        search: str | None = None,
        tags: list[str] | None = None,
    ) -> int:
        """Count documents matching filters."""
        return await self.metadata_store.count_documents(
            project_id=self.project_id,
            scope=scope,
            scope_id=scope_id,
            source=source,
            status=status,
            search=search,
            tags=tags,
        )

    async def get_document(self, document_id: str) -> ContextDocument | None:
        """Get a document by ID."""
        return await self.metadata_store.get_document(document_id)

    async def delete_document(self, document_id: str) -> bool:
        """Delete a document and all its chunks/vectors."""
        doc = await self.metadata_store.get_document(document_id)
        if not doc:
            return False

        # Delete vectors
        chunks = await self.metadata_store.get_chunks(document_id)
        if chunks:
            await self.vector_store.delete([c.id for c in chunks])

        # Delete metadata
        self._invalidate_bm25()
        return await self.metadata_store.delete_document(document_id)

    async def reprocess_document(self, document_id: str) -> ContextDocument:
        """Re-chunk and re-embed a document from its stored content.

        Collects the original text from existing chunks, deletes old data,
        then re-runs the ingestion pipeline with current chunking config.
        """
        doc = await self.metadata_store.get_document(document_id)
        if not doc:
            from sagewai.errors import ContextDocumentNotFoundError

            raise ContextDocumentNotFoundError(document_id)

        # Collect original text from existing chunks before deleting
        chunks = await self.metadata_store.get_chunks(document_id)
        original_text = "\n\n".join(c.content for c in chunks) if chunks else ""

        if not original_text:
            doc.status = "ready"
            doc.chunk_count = 0
            await self.metadata_store.update_document(doc)
            return doc

        # Delete existing chunks and vectors
        if chunks:
            await self.vector_store.delete([c.id for c in chunks])
            await self.metadata_store.delete_chunks(document_id)

        # Re-ingest the collected text through the pipeline
        doc.status = "processing"
        await self.metadata_store.update_document(doc)

        try:
            from sagewai.context.chunking import ChunkManager
            from sagewai.context.dedup import Deduplicator

            chunk_mgr = ChunkManager(self.chunking_config)
            new_chunks = chunk_mgr.chunk(original_text, metadata={"reprocessed": True})

            if not new_chunks:
                doc.status = "ready"
                doc.chunk_count = 0
                await self.metadata_store.update_document(doc)
                return doc

            vectors = await self._pipeline._embed_chunks(new_chunks)
            context_chunks = self._pipeline._build_chunks(
                new_chunks, vectors, document_id,
                doc.scope, doc.scope_id, doc.project_id,
            )
            await self.metadata_store.save_chunks(context_chunks)

            for chunk, vec in zip(context_chunks, vectors):
                await self.vector_store.insert(
                    chunk_id=chunk.id, vector=vec,
                    metadata={
                        "project_id": doc.project_id,
                        "scope": doc.scope.value,
                        "scope_id": doc.scope_id,
                        "document_id": document_id,
                    },
                )

            doc.status = "ready"
            doc.chunk_count = len(context_chunks)
            await self.metadata_store.update_document(doc)
            logger.info("Reprocessed doc %s: %d chunks", document_id, len(context_chunks))
            self._invalidate_bm25()
            return doc

        except _FALLBACK_ERRORS as exc:
            doc.status = "failed"
            await self.metadata_store.update_document(doc)
            logger.error("Reprocessing failed for doc %s: %s", document_id, exc)
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _supersede_document(self, document_id: str) -> None:
        """Mark all chunks in a document as superseded (importance → 0)."""
        chunks = await self.metadata_store.get_chunks(document_id)
        for chunk in chunks:
            chunk.importance = 0.0
            await self.metadata_store.update_chunk(chunk)

        doc = await self.metadata_store.get_document(document_id)
        if doc:
            doc.metadata["superseded"] = True
            await self.metadata_store.update_document(doc)

        # Supersede in graph store if available
        if self.graph_store and hasattr(self.graph_store, "supersede_by_document"):
            try:
                await self.graph_store.supersede_by_document(document_id)
            except (ConnectionError, OSError, RuntimeError, ValueError):
                logger.debug("Graph supersede failed for %s", document_id)

    def _emit_event(self, event: str, data: dict[str, Any]) -> None:
        """Emit an observability event if callback is configured."""
        if self._event_callback:
            try:
                self._event_callback(event, data)
            except Exception:
                logger.debug("Event callback error for %s", event, exc_info=True)

    def _invalidate_bm25(self) -> None:
        """Mark the BM25 index as stale so it is rebuilt on next search."""
        self._bm25_index = None

    async def _maybe_auto_lifecycle(self) -> None:
        """Check if chunk count exceeds threshold and trigger maintenance."""
        if not self._lifecycle_config or self._lifecycle_running:
            return

        try:
            from sagewai.context.lifecycle import LifecycleConfig

            config = self._lifecycle_config
            if not isinstance(config, LifecycleConfig):
                return

            docs = await self.metadata_store.list_documents(
                project_id=self.project_id, status="ready"
            )
            total_chunks = sum(d.chunk_count for d in docs)

            if total_chunks >= config.auto_trigger_threshold:
                self._lifecycle_running = True
                logger.info(
                    "Auto-lifecycle triggered: %d chunks >= %d threshold",
                    total_chunks,
                    config.auto_trigger_threshold,
                )
                try:
                    from sagewai.context.lifecycle import LifecycleManager

                    mgr = LifecycleManager(
                        metadata_store=self.metadata_store,
                        vector_store=self.vector_store,
                        config=config,
                    )
                    await mgr.run_maintenance(self.project_id)
                finally:
                    self._lifecycle_running = False
        except (ImportError, OSError, RuntimeError, ValueError):
            logger.debug("Auto-lifecycle failed", exc_info=True)

    def _build_scope_filters(
        self, scopes: list[ContextScope] | None = None
    ) -> list[tuple[ContextScope, str]]:
        """Build scope filters for retrieval based on engine context."""
        if scopes:
            filters = []
            for scope in scopes:
                scope_id = self._resolve_scope_id(scope)
                if scope_id:
                    filters.append((scope, scope_id))
            return filters

        # Default: search all applicable scopes
        filters = []
        if self._org_id:
            filters.append((ContextScope.ORG, self._org_id))
        filters.append((ContextScope.PROJECT, self.project_id))
        return filters

    def _resolve_scope_id(self, scope: ContextScope) -> str | None:
        """Resolve the scope_id for a given scope level."""
        if scope == ContextScope.ORG:
            return self._org_id
        elif scope == ContextScope.PROJECT:
            return self.project_id
        return None

    async def _embed_query(self, query: str) -> list[float]:
        """Embed a query string with circuit breaker protection.

        When an ``Embedder`` is configured, delegates directly to it.
        Otherwise uses the legacy ``litellm.aembedding`` path with circuit
        breaker: after 3 consecutive failures within 60 s, falls back to
        hash-based vectors until cooldown expires.
        """
        if self._embedder is not None:
            return await self._embedder.embed_query(query)

        # Legacy path — direct litellm call with circuit breaker
        if self._embed_failures >= self._embed_failure_threshold and time.time() < self._embed_circuit_open_until:
            logger.warning(
                "Embedding circuit breaker OPEN (%d failures), using hash fallback",
                self._embed_failures,
            )
            return IngestionPipeline._hash_vector(query)

        try:
            import litellm

            response = await litellm.aembedding(
                model=self.embedding_model, input=[query]
            )
            # Success — reset circuit breaker
            self._embed_failures = 0
            return response.data[0]["embedding"]
        except _FALLBACK_ERRORS as exc:
            self._embed_failures += 1
            self._embed_circuit_open_until = time.time() + self._embed_circuit_cooldown
            logger.warning(
                "Embedding failed (attempt %d, model=%s, error=%s), using hash fallback",
                self._embed_failures,
                self.embedding_model,
                type(exc).__name__,
            )
            return IngestionPipeline._hash_vector(query)

    def get_tools(self) -> list:
        """Return self-editing memory tools bound to this engine.

        Returns 4 tools: memory_store, memory_search, memory_forget, memory_update.
        Pass these to an agent's tools list to enable self-editing memory.
        """
        from sagewai.context.tools import create_memory_tools

        return create_memory_tools(self)

    async def _extract_graph(self, doc: ContextDocument) -> None:
        """Extract entities and relations into graph store.

        For code files: uses tree-sitter entities from chunk metadata (no LLM).
        For documents: delegates to graph_store.store() which uses LLM extraction.
        """
        if not self.graph_store:
            return
        try:
            chunks = await self.metadata_store.get_chunks(doc.id)
            graph_meta = {"document_id": doc.id, "scope": doc.scope.value}

            for chunk in chunks:
                # Code entities — use pre-extracted AST data (no LLM needed)
                entities = chunk.metadata.get("entities", [])
                if entities and hasattr(self.graph_store, "add_relation"):
                    for ent in entities[:30]:
                        if isinstance(ent, dict) and ent.get("name"):
                            await self.graph_store.store(
                                f"{ent.get('kind', 'entity')}: {ent['name']}",
                                metadata={**graph_meta, "entity_kind": ent.get("kind")},
                            )
                            if ent.get("parent"):
                                await self.graph_store.add_relation(
                                    source=ent["parent"],
                                    relation=f"contains_{ent.get('kind', 'member')}",
                                    target=ent["name"],
                                )
                else:
                    # Document text — use LLM-based relation extraction via graph store
                    await self.graph_store.store(chunk.content, metadata=graph_meta)

            logger.info("Graph extraction complete for doc %s: %d chunks", doc.id, len(chunks))
        except (ConnectionError, OSError, RuntimeError, ValueError):
            logger.warning("Graph extraction failed for doc %s", doc.id, exc_info=True)
