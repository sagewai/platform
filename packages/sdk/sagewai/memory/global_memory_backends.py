# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Storage backends for :class:`GlobalMemory`.

The default backend is in-process — fast, zero-config, but limited to
a single Python process. Production deployments with multiple workers
should switch to a shared backend (Postgres or Redis) so that all
workers see the same shared knowledge surface.

Backend protocol::

    class GlobalMemoryBackend(Protocol):
        async def add(self, scope: str, content: str, metadata: dict | None = None) -> None: ...
        async def retrieve(self, scope: str, query: str, top_k: int) -> list[str]: ...
        async def clear(self, scope: str) -> None: ...
        async def count(self, scope: str) -> int: ...

Built-in backends:

- :class:`InMemoryBackend` — process-local. Default. Tests + single-host dev.
- :class:`PostgresBackend` — shared across workers via the existing
  Sagewai Postgres database. Multi-worker production. Optional pgvector.
- :class:`RedisBackend` — shared across workers via Redis. High-throughput
  cache. Eventually consistent across replicas.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class GlobalMemoryBackend(Protocol):
    """Storage interface for :class:`GlobalMemory`.

    All backends must support concurrent ``add`` / ``retrieve`` calls
    safely — the :class:`GlobalMemory` wrapper does NOT serialise
    backend calls. The backend is responsible for its own concurrency.
    """

    async def add(
        self,
        scope: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append *content* to the *scope*'s memory."""
        ...

    async def retrieve(self, scope: str, query: str, top_k: int) -> list[str]:
        """Return the top-k matches for *query* within *scope*."""
        ...

    async def clear(self, scope: str) -> None:
        """Drop all content for *scope*."""
        ...

    async def count(self, scope: str) -> int:
        """Return the number of items in *scope*. Used for stats."""
        ...


# ── 1. InMemoryBackend (default, process-local) ───────────────────


class InMemoryBackend:
    """Process-local backend backed by :class:`RAGEngine` per scope.

    Storage: an in-process dict of scope → :class:`RAGEngine`.
    Multi-worker: NOT supported — each worker has its own state.

    Use this for tests, single-host dev, or single-process deployments.
    For production multi-worker, switch to :class:`PostgresBackend`.
    """

    def __init__(self) -> None:
        from sagewai.memory.rag import RAGEngine

        self._RAGEngine = RAGEngine
        self._engines: dict[str, Any] = {}
        self._counts: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def _engine_for(self, scope: str):
        async with self._lock:
            if scope not in self._engines:
                self._engines[scope] = self._RAGEngine()
                self._counts[scope] = 0
            return self._engines[scope]

    async def add(
        self, scope: str, content: str, metadata: dict[str, Any] | None = None,
    ) -> None:
        engine = await self._engine_for(scope)
        await engine.store(content, metadata=metadata)
        self._counts[scope] = self._counts.get(scope, 0) + 1

    async def retrieve(self, scope: str, query: str, top_k: int) -> list[str]:
        if scope not in self._engines:
            return []
        return await self._engines[scope].retrieve(query, top_k=top_k)

    async def clear(self, scope: str) -> None:
        if scope in self._engines:
            await self._engines[scope].clear()
            self._counts[scope] = 0

    async def count(self, scope: str) -> int:
        return self._counts.get(scope, 0)


# ── 2. PostgresBackend (shared across workers) ────────────────────


class PostgresBackend:
    """Shared multi-worker backend using the existing Sagewai Postgres.

    Storage: ``global_memory_facts`` table — one row per fact, indexed
    on (scope, content) with a Postgres full-text search column for
    keyword retrieval. pgvector embeddings are an optional v1.1+ upgrade.

    Schema (created on first use via :meth:`ensure_schema`)::

        CREATE TABLE global_memory_facts (
            id BIGSERIAL PRIMARY KEY,
            scope TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            content_tsv tsvector
              GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
        );
        CREATE INDEX idx_gm_scope ON global_memory_facts (scope);
        CREATE INDEX idx_gm_content_tsv ON global_memory_facts USING GIN (content_tsv);

    Multi-worker: ✅ — every worker sees every other worker's writes
    after the transaction commits.

    Args:
        connection_pool: An ``asyncpg.Pool`` instance. Reuse the
            existing Sagewai pool from :class:`PostgresStore` rather
            than creating a new one.
    """

    SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS global_memory_facts (
        id BIGSERIAL PRIMARY KEY,
        scope TEXT NOT NULL,
        content TEXT NOT NULL,
        metadata JSONB DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        content_tsv tsvector
          GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
    );
    CREATE INDEX IF NOT EXISTS idx_gm_scope ON global_memory_facts (scope);
    CREATE INDEX IF NOT EXISTS idx_gm_content_tsv
      ON global_memory_facts USING GIN (content_tsv);
    """

    def __init__(self, *, connection_pool: Any) -> None:
        # Don't import asyncpg here — it's an optional dep
        self._pool = connection_pool

    async def ensure_schema(self) -> None:
        """Create the ``global_memory_facts`` table + indexes if missing."""
        async with self._pool.acquire() as conn:
            await conn.execute(self.SCHEMA_SQL)

    async def add(
        self, scope: str, content: str, metadata: dict[str, Any] | None = None,
    ) -> None:
        import json

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO global_memory_facts (scope, content, metadata)
                VALUES ($1, $2, $3::jsonb)
                """,
                scope, content, json.dumps(metadata or {}),
            )

    async def retrieve(self, scope: str, query: str, top_k: int) -> list[str]:
        async with self._pool.acquire() as conn:
            # Postgres full-text search with rank-ordered top-k
            rows = await conn.fetch(
                """
                SELECT content, ts_rank(content_tsv, plainto_tsquery('english', $2)) AS rank
                FROM global_memory_facts
                WHERE scope = $1
                  AND content_tsv @@ plainto_tsquery('english', $2)
                ORDER BY rank DESC, created_at DESC
                LIMIT $3
                """,
                scope, query, top_k,
            )
            return [r["content"] for r in rows]

    async def clear(self, scope: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM global_memory_facts WHERE scope = $1", scope,
            )

    async def count(self, scope: str) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM global_memory_facts WHERE scope = $1",
                scope,
            )
            return int(row["n"]) if row else 0


# ── 3. RedisBackend (shared cache, high-throughput) ──────────────


class RedisBackend:
    """Shared multi-worker backend using Redis as a fast cache.

    Storage: per-scope Redis lists ``sagewai:gm:{scope}`` of stringified
    facts. Retrieval is keyword filtering over the list (small corpora)
    or via Redis Search if the ``RediSearch`` module is available.

    Multi-worker: ✅ — Redis is the canonical multi-process cache.

    Best for: high-write, low-latency scenarios where eventual
    consistency is acceptable. For durability, use :class:`PostgresBackend`.

    Args:
        redis_client: An ``redis.asyncio.Redis`` instance.
        key_prefix: Prefix for the Redis keys. Defaults to ``sagewai:gm``.
    """

    def __init__(
        self,
        *,
        redis_client: Any,
        key_prefix: str = "sagewai:gm",
    ) -> None:
        self._redis = redis_client
        self._prefix = key_prefix

    def _key(self, scope: str) -> str:
        return f"{self._prefix}:{scope}"

    async def add(
        self, scope: str, content: str, metadata: dict[str, Any] | None = None,
    ) -> None:
        # For simplicity store JSON: {"c": content, "m": metadata}
        import json

        payload = json.dumps({"c": content, "m": metadata or {}})
        await self._redis.rpush(self._key(scope), payload)

    async def retrieve(self, scope: str, query: str, top_k: int) -> list[str]:
        import json

        # Pull all facts for this scope; filter client-side.
        # For large corpora, swap to RediSearch with FT.SEARCH.
        all_facts = await self._redis.lrange(self._key(scope), 0, -1)
        query_terms = [t.lower().strip(".,!?") for t in query.split() if len(t) > 2]
        if not query_terms:
            # No useful query — return most recent top_k
            return [
                json.loads(f.decode() if isinstance(f, bytes) else f)["c"]
                for f in all_facts[-top_k:]
            ]
        scored: list[tuple[int, str]] = []
        for f in all_facts:
            data = json.loads(f.decode() if isinstance(f, bytes) else f)
            content = data["c"]
            score = sum(
                1 for t in query_terms if t in content.lower()
            )
            if score > 0:
                scored.append((score, content))
        scored.sort(reverse=True)
        return [c for _, c in scored[:top_k]]

    async def clear(self, scope: str) -> None:
        await self._redis.delete(self._key(scope))

    async def count(self, scope: str) -> int:
        return int(await self._redis.llen(self._key(scope)))
