# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Ingestion pipeline — parse, chunk, embed, and store documents."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sagewai.intelligence.embeddings.protocol import Embedder
    from sagewai.intelligence.multimodal.protocol import Transcriber, VisionDescriber

# Build a tuple of exception types for embedding/LLM fallback catches.
# Includes optional litellm/openai errors when available.
_FALLBACK_ERRORS: tuple[type[BaseException], ...] = (
    ImportError, OSError, RuntimeError, ValueError, ConnectionError, TypeError,
)
try:
    import openai

    _FALLBACK_ERRORS = (*_FALLBACK_ERRORS, openai.OpenAIError)
except ImportError:
    pass

try:
    from pymilvus.exceptions import MilvusException

    _FALLBACK_ERRORS = (*_FALLBACK_ERRORS, MilvusException)
except ImportError:
    pass

from sagewai.context.chunking import ChunkManager
from sagewai.context.dedup import Deduplicator
from sagewai.context.models import (
    ChunkingConfig,
    ChunkText,
    ContextChunk,
    ContextDocument,
    ContextScope,
    ContextSource,
    ParsedCode,
)
from sagewai.context.parsers import (
    detect_mime_type,
    is_code_file,
    parse_code,
    parse_directory,
    parse_document,
)
from sagewai.context.stores import ContextMetadataStore, ContextVectorStore

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Orchestrates the full ingestion flow: parse → chunk → dedup → embed → store.

    Usage::

        pipeline = IngestionPipeline(
            metadata_store=pg_store,
            vector_store=milvus_store,
            embedding_model="text-embedding-3-small",
        )
        doc = await pipeline.ingest_file(file_bytes, "report.pdf",
                                          scope=ContextScope.PROJECT, scope_id="acme")
    """

    def __init__(
        self,
        *,
        metadata_store: ContextMetadataStore,
        vector_store: ContextVectorStore,
        embedding_model: str = "text-embedding-3-small",
        chunking_config: ChunkingConfig | None = None,
        max_concurrent_ingestions: int = 2,
        embedder: Embedder | None = None,
        transcriber: Transcriber | None = None,
        vision_describer: VisionDescriber | None = None,
    ) -> None:
        self.metadata_store = metadata_store
        self.vector_store = vector_store
        self.embedding_model = embedding_model
        self.chunk_manager = ChunkManager(chunking_config)
        self._semaphore = asyncio.Semaphore(max_concurrent_ingestions)
        self._embedder = embedder
        self._transcriber = transcriber
        self._vision_describer = vision_describer

    # ------------------------------------------------------------------
    # File ingestion
    # ------------------------------------------------------------------

    async def ingest_file(
        self,
        file_bytes: bytes,
        filename: str,
        scope: ContextScope,
        scope_id: str,
        project_id: str = "default",
        enable_graph: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ContextDocument:
        """Ingest a single file: parse -> chunk -> embed -> store (concurrency-limited)."""
        async with self._semaphore:
            return await self._do_ingest_file(
                file_bytes, filename, scope, scope_id, project_id, enable_graph, metadata
            )

    async def _do_ingest_file(
        self,
        file_bytes: bytes,
        filename: str,
        scope: ContextScope,
        scope_id: str,
        project_id: str = "default",
        enable_graph: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ContextDocument:
        """Ingest a single file: parse -> chunk -> embed -> store."""
        mime = detect_mime_type(filename)
        doc_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        doc = ContextDocument(
            id=doc_id,
            scope=scope,
            scope_id=scope_id,
            project_id=project_id,
            title=filename,
            source=ContextSource.UPLOAD,
            source_uri=filename,
            mime_type=mime,
            file_size_bytes=len(file_bytes),
            status="processing",
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
            freshness_at=now,
        )
        await self.metadata_store.save_document(doc)

        try:
            # Parse — handle multimodal files when backends are available
            if mime.startswith("audio/") and self._transcriber:
                import tempfile
                suffix = os.path.splitext(filename)[1] or ".wav"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(file_bytes)
                    tmp_path = tmp.name
                try:
                    raw_text = await self._transcriber.transcribe(tmp_path)
                finally:
                    os.unlink(tmp_path)
                doc.metadata["parser"] = "transcriber"
                doc.metadata["original_mime"] = mime
            elif mime.startswith("image/") and self._vision_describer:
                import tempfile
                suffix = os.path.splitext(filename)[1] or ".png"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(file_bytes)
                    tmp_path = tmp.name
                try:
                    raw_text = await self._vision_describer.describe(tmp_path)
                finally:
                    os.unlink(tmp_path)
                doc.metadata["parser"] = "vision"
                doc.metadata["original_mime"] = mime
            elif is_code_file(mime):
                text = file_bytes.decode("utf-8", errors="replace")
                ts_lang = _mime_to_language(mime)
                parsed = await parse_code(text, ts_lang, filename=filename)
                raw_text = parsed.text
                doc.metadata["language"] = ts_lang
                doc.metadata["entities"] = [e.model_dump() for e in parsed.entities[:50]]
            else:
                parsed_doc = await parse_document(file_bytes, mime, filename=filename)
                raw_text = parsed_doc.text
                doc.metadata.update(parsed_doc.metadata)

            # Chunk (offload synchronous work to thread)
            chunk_meta = {"filename": filename, "mime_type": mime}
            chunks = await asyncio.to_thread(
                self.chunk_manager.chunk, raw_text, chunk_meta
            )

            # Dedup
            existing_hashes = await self.metadata_store.get_existing_hashes(project_id)
            dedup = Deduplicator(existing_hashes=existing_hashes)
            chunks = dedup.filter(chunks)

            if not chunks:
                doc.status = "ready"
                doc.chunk_count = 0
                await self.metadata_store.update_document(doc)
                return doc

            # Embed
            vectors = await self._embed_chunks(chunks)

            # Store chunks + vectors (batched inserts)
            context_chunks = self._build_chunks(
                chunks, vectors, doc_id, scope, scope_id, project_id
            )
            await self.metadata_store.save_chunks(context_chunks)

            insert_sem = asyncio.Semaphore(50)

            async def _insert(chunk: ContextChunk, vec: list[float]) -> None:
                async with insert_sem:
                    await self.vector_store.insert(
                        chunk_id=chunk.id,
                        vector=vec,
                        metadata={
                            "project_id": project_id,
                            "scope": scope.value,
                            "scope_id": scope_id,
                            "document_id": doc_id,
                        },
                    )

            await asyncio.gather(
                *(_insert(c, v) for c, v in zip(context_chunks, vectors))
            )

            doc.status = "ready"
            doc.chunk_count = len(context_chunks)
            await self.metadata_store.update_document(doc)

            logger.info(
                "Ingested %s: %d chunks (%d tokens total)",
                filename,
                len(context_chunks),
                sum(c.token_count for c in context_chunks),
            )
            return doc

        except _FALLBACK_ERRORS as exc:
            doc.status = "failed"
            await self.metadata_store.update_document(doc)
            logger.error("Ingestion failed for %s: %s", filename, exc)
            raise

    # ------------------------------------------------------------------
    # Text ingestion
    # ------------------------------------------------------------------

    async def ingest_text(
        self,
        text: str,
        title: str,
        scope: ContextScope,
        scope_id: str,
        project_id: str = "default",
        source: ContextSource = ContextSource.MANUAL,
        metadata: dict[str, Any] | None = None,
    ) -> ContextDocument:
        """Ingest raw text content (concurrency-limited)."""
        async with self._semaphore:
            return await self._do_ingest_text(
                text, title, scope, scope_id, project_id, source, metadata
            )

    async def _do_ingest_text(
        self,
        text: str,
        title: str,
        scope: ContextScope,
        scope_id: str,
        project_id: str = "default",
        source: ContextSource = ContextSource.MANUAL,
        metadata: dict[str, Any] | None = None,
    ) -> ContextDocument:
        """Ingest raw text content."""
        doc_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        doc = ContextDocument(
            id=doc_id,
            scope=scope,
            scope_id=scope_id,
            project_id=project_id,
            title=title,
            source=source,
            mime_type="text/plain",
            file_size_bytes=len(text.encode("utf-8")),
            status="processing",
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
            freshness_at=now,
        )
        await self.metadata_store.save_document(doc)

        try:
            chunks = await asyncio.to_thread(
                self.chunk_manager.chunk, text, {"title": title}
            )

            existing_hashes = await self.metadata_store.get_existing_hashes(project_id)
            dedup = Deduplicator(existing_hashes=existing_hashes)
            chunks = dedup.filter(chunks)

            if not chunks:
                doc.status = "ready"
                doc.chunk_count = 0
                await self.metadata_store.update_document(doc)
                return doc

            vectors = await self._embed_chunks(chunks)
            context_chunks = self._build_chunks(
                chunks, vectors, doc_id, scope, scope_id, project_id
            )
            await self.metadata_store.save_chunks(context_chunks)

            insert_sem = asyncio.Semaphore(50)

            async def _insert(chunk: ContextChunk, vec: list[float]) -> None:
                async with insert_sem:
                    await self.vector_store.insert(
                        chunk_id=chunk.id,
                        vector=vec,
                        metadata={
                            "project_id": project_id,
                            "scope": scope.value,
                            "scope_id": scope_id,
                            "document_id": doc_id,
                        },
                    )

            await asyncio.gather(
                *(_insert(c, v) for c, v in zip(context_chunks, vectors))
            )

            doc.status = "ready"
            doc.chunk_count = len(context_chunks)
            await self.metadata_store.update_document(doc)
            return doc

        except _FALLBACK_ERRORS as exc:
            doc.status = "failed"
            await self.metadata_store.update_document(doc)
            logger.error("Text ingestion failed for %s: %s", title, exc)
            raise

    # ------------------------------------------------------------------
    # Directory ingestion
    # ------------------------------------------------------------------

    async def ingest_directory(
        self,
        path: str,
        scope: ContextScope,
        scope_id: str,
        project_id: str = "default",
        patterns: list[str] | None = None,
        ignore: list[str] | None = None,
        enable_graph: bool = False,
    ) -> list[ContextDocument]:
        """Ingest all files in a directory tree."""
        parsed_files = await parse_directory(path, patterns=patterns, ignore=ignore)
        documents: list[ContextDocument] = []

        for parsed in parsed_files:
            rel_path = parsed.metadata.get("relative_path", "unknown")
            try:
                if isinstance(parsed, ParsedCode):
                    text = parsed.text
                    file_meta = {**parsed.metadata, "language": parsed.language}
                    if parsed.entities:
                        file_meta["entities"] = [
                            e.model_dump() for e in parsed.entities[:50]
                        ]
                else:
                    text = parsed.text
                    file_meta = parsed.metadata

                doc = await self.ingest_text(
                    text=text,
                    title=rel_path,
                    scope=scope,
                    scope_id=scope_id,
                    project_id=project_id,
                    source=ContextSource.DIRECTORY,
                    metadata=file_meta,
                )
                documents.append(doc)

            except _FALLBACK_ERRORS:
                logger.warning("Failed to ingest %s", rel_path, exc_info=True)
                continue

        logger.info("Directory ingestion: %d/%d files succeeded", len(documents), len(parsed_files))
        return documents

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    async def _embed_chunks(self, chunks: list[ChunkText]) -> list[list[float]]:
        """Embed chunk texts using the configured embedder.

        When an ``Embedder`` instance is set, delegates to it directly.
        Otherwise falls back to the legacy ``litellm.aembedding`` path
        with hash-based fallback for backward compatibility.
        """
        if self._embedder is not None:
            texts = [c.content for c in chunks]
            return await self._embedder.embed(texts)

        # Legacy path — direct litellm call with hash fallback
        try:
            import litellm

            texts = [c.content for c in chunks]
            vectors: list[list[float]] = []

            batch_size = 100
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                response = await litellm.aembedding(
                    model=self.embedding_model, input=batch
                )
                for item in response.data:
                    vectors.append(item["embedding"])

            return vectors

        except _FALLBACK_ERRORS:
            logger.info("Embedding unavailable, using hash-based vectors")
            return [self._hash_vector(c.content) for c in chunks]

    @staticmethod
    def _hash_vector(text: str, dim: int = 1536) -> list[float]:
        """Generate a deterministic pseudo-vector from text hash.

        Useful for testing and offline mode. Same text always produces the same vector.
        """
        import hashlib

        h = hashlib.sha256(text.encode("utf-8")).digest()
        # Extend hash bytes to fill dim floats
        extended = h * ((dim * 4 // len(h)) + 1)
        values = []
        for i in range(dim):
            byte_val = extended[i % len(extended)]
            values.append((byte_val / 255.0) * 2 - 1)  # normalize to [-1, 1]
        return values

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_chunks(
        self,
        chunk_texts: list[ChunkText],
        vectors: list[list[float]],
        document_id: str,
        scope: ContextScope,
        scope_id: str,
        project_id: str,
    ) -> list[ContextChunk]:
        """Convert ChunkText + vectors into ContextChunk objects."""
        now = datetime.now(timezone.utc)
        return [
            ContextChunk(
                id=str(uuid.uuid4()),
                document_id=document_id,
                scope=scope,
                scope_id=scope_id,
                project_id=project_id,
                content=ct.content,
                chunk_index=ct.chunk_index,
                token_count=ct.token_count,
                embedding_model=self.embedding_model,
                content_hash=ct.content_hash,
                metadata=ct.metadata,
                created_at=now,
            )
            for ct in chunk_texts
        ]


def _mime_to_language(mime: str) -> str:
    """Map MIME type to tree-sitter language name."""
    mapping = {
        "text/x-python": "python",
        "text/javascript": "javascript",
        "text/typescript": "typescript",
        "text/x-java": "java",
        "text/x-go": "go",
        "text/x-rust": "rust",
        "text/x-ruby": "ruby",
        "text/x-c": "c",
        "text/x-c++": "cpp",
    }
    return mapping.get(mime, "python")
