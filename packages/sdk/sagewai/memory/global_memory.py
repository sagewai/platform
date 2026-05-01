# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""GlobalMemory — shared cross-agent memory with pluggable storage backends.

The complement to per-mission :class:`MemoryBranch`: where ``MemoryBranch``
isolates concurrent missions, ``GlobalMemory`` does the opposite — it
gives every agent in a deployment a single shared knowledge surface
they all read from and write to.

Use cases:

- A team of agents accumulating shared learnings (an on-call team
  that learns from prior incidents, a code-review squad whose notes
  accumulate over time)
- A research org where every agent contributes findings to a common pool
- A multi-tenant SaaS where each tenant gets its own scope

Multi-tenancy: ``GlobalMemory.get(scope=...)`` returns a per-scope
singleton. Two scopes are the **hard isolation boundary** — same as
the ``project_id`` axis used elsewhere in Sagewai.

**Multi-worker / multi-process deployments — read this:**

The default storage is in-process — fast, zero-config, but limited to
a single Python process. Multiple workers run as separate processes;
each gets its own ``InMemoryBackend`` instance and they DO NOT share.

For production multi-worker, configure a shared backend:

- :class:`~sagewai.memory.global_memory_backends.PostgresBackend` uses
  the existing Sagewai Postgres database. Multi-worker, durable, with
  Postgres full-text search.
- :class:`~sagewai.memory.global_memory_backends.RedisBackend` uses
  Redis as a fast cache. Multi-worker, eventually consistent.

Configure via :meth:`GlobalMemory.configure_backend`::

    from sagewai.memory.global_memory_backends import PostgresBackend

    GlobalMemory.configure_backend(
        PostgresBackend(connection_pool=postgres_store.pool)
    )
    await GlobalMemory.ensure_backend_ready()

Now every worker that calls ``GlobalMemory.get(scope='team-x')`` reads
the same shared memory via Postgres.

For per-mission isolated memory, use :class:`MemoryBranch` instead.

Example (single-process, default in-memory backend)::

    from sagewai.memory import GlobalMemory

    team = GlobalMemory.get(scope="acme-eng")
    await team.add("Customer ACME-CORP escalated twice this month.")
    notes = await team.retrieve("ACME-CORP", top_k=3)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sagewai.memory.global_memory_backends import (
    GlobalMemoryBackend,
    InMemoryBackend,
)

logger = logging.getLogger(__name__)


class GlobalMemory:
    """Shared cross-agent memory with pluggable storage.

    Each scope is an independent storage namespace. Agents call
    :meth:`add` to contribute and :meth:`retrieve` to read.

    The default backend is :class:`InMemoryBackend` (process-local).
    For multi-worker deployments, configure :class:`PostgresBackend`
    or :class:`RedisBackend` via :meth:`configure_backend`.
    """

    # Class-level registry of scope → instance. Singleton-per-scope.
    _instances: dict[str, "GlobalMemory"] = {}

    # Class-level shared backend. Defaults to InMemoryBackend (process-local).
    # Configure via GlobalMemory.configure_backend(...) to switch to a
    # shared backend for multi-worker deployments.
    _backend: GlobalMemoryBackend = InMemoryBackend()

    def __init__(self, scope: str) -> None:
        if not scope or not isinstance(scope, str):
            raise ValueError("scope must be a non-empty string")
        self.scope = scope
        self._created_at = time.time()
        self._add_count = 0
        self._retrieve_count = 0

    # ── factory / configuration ────────────────────────────────────

    @classmethod
    def get(cls, scope: str = "default") -> "GlobalMemory":
        """Return the singleton :class:`GlobalMemory` for *scope*.

        Same scope → same instance (shares memory via the configured
        backend). Different scopes → independent (no cross-leak).

        Args:
            scope: Scope identifier. Use a project_id, org_id, or
                tenant slug. Defaults to ``"default"`` for single-
                tenant use. **Two scopes never share memory.**

        Returns:
            The :class:`GlobalMemory` instance for this scope.
        """
        if scope not in cls._instances:
            cls._instances[scope] = cls(scope=scope)
            logger.info(
                "Created GlobalMemory scope=%r (backend=%s)",
                scope, type(cls._backend).__name__,
            )
        return cls._instances[scope]

    @classmethod
    def configure_backend(cls, backend: GlobalMemoryBackend) -> None:
        """Switch the shared storage backend.

        For multi-worker deployments, call this once at startup with
        a shared backend (Postgres / Redis). All workers must use
        the same backend type pointed at the same instance for them
        to actually share memory.

        Args:
            backend: An object satisfying :class:`GlobalMemoryBackend`.
        """
        cls._backend = backend
        logger.info(
            "GlobalMemory backend reconfigured to %s", type(backend).__name__,
        )

    @classmethod
    async def ensure_backend_ready(cls) -> None:
        """Initialise the backend (create tables/indexes if needed).

        Call once at app startup after :meth:`configure_backend`.
        No-op for backends that don't need preparation.
        """
        ensure = getattr(cls._backend, "ensure_schema", None)
        if ensure is not None and asyncio.iscoroutinefunction(ensure):
            await ensure()

    @classmethod
    def get_backend(cls) -> GlobalMemoryBackend:
        """Return the currently-configured shared backend (for inspection)."""
        return cls._backend

    @classmethod
    def list_scopes(cls) -> list[str]:
        """Return scope names that currently have :class:`GlobalMemory` instances.

        Note: returns scopes requested via ``get()`` in this process.
        For backends that persist across process lifetime (Postgres,
        Redis), scopes may exist in storage that haven't been
        requested here yet.
        """
        return sorted(cls._instances.keys())

    @classmethod
    def reset(cls, scope: str | None = None) -> None:
        """Drop one (or all) scope instances from this process.

        Useful in tests. Does NOT delete data from the backend —
        call :meth:`clear` on the instance for that.

        Args:
            scope: Specific scope to drop, or ``None`` to drop all.
        """
        if scope is None:
            n = len(cls._instances)
            cls._instances.clear()
            logger.info("Reset all GlobalMemory scopes (dropped %d)", n)
        elif scope in cls._instances:
            del cls._instances[scope]
            logger.info("Reset GlobalMemory scope=%r", scope)

    # ── public API ─────────────────────────────────────────────────

    async def add(
        self,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add one fact / observation / document to the shared memory.

        Concurrency: backends are responsible for safe concurrent adds.
        :class:`InMemoryBackend` uses an asyncio lock per scope;
        :class:`PostgresBackend` relies on row-level Postgres semantics;
        :class:`RedisBackend` uses atomic RPUSH.

        Args:
            content: The text to store.
            metadata: Optional metadata (agent_id, timestamp, source).
        """
        if not content or not content.strip():
            return
        await type(self)._backend.add(self.scope, content, metadata=metadata)
        self._add_count += 1

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        """Retrieve the top-k matches for *query*.

        Args:
            query: Query text.
            top_k: Number of results to return.

        Returns:
            List of matching content strings, ordered by backend-
            specific relevance scoring.
        """
        self._retrieve_count += 1
        return await type(self)._backend.retrieve(self.scope, query, top_k)

    async def clear(self) -> None:
        """Drop all content for this scope from the backend."""
        await type(self)._backend.clear(self.scope)
        self._add_count = 0
        logger.info("Cleared GlobalMemory scope=%r", self.scope)

    # ── observability ──────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return per-instance counters for the Observatory dashboard.

        Counters are this-process-local. Use :meth:`backend_count`
        for the authoritative cross-worker count.
        """
        return {
            "scope": self.scope,
            "age_seconds": round(time.time() - self._created_at, 1),
            "add_count": self._add_count,
            "retrieve_count": self._retrieve_count,
            "backend": type(type(self)._backend).__name__,
        }

    async def backend_count(self) -> int:
        """Return the count of items in the backend for this scope.

        This is the authoritative number — counts items committed by
        ALL workers using the shared backend, not just this process.
        """
        return await type(self)._backend.count(self.scope)
