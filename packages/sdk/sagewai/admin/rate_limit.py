# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pluggable rate limiter for the admin throttles.

Two backends behind one :class:`RateLimiter` interface:

* :class:`InMemoryRateLimiter` — the single-process sliding window the legacy
  ``_LoginThrottle`` / ``_ProjectRunThrottle`` used. This is the **default** and
  reproduces today's single-org behaviour exactly (deque of timestamps, trimmed
  on each hit). No database dependency.
* :class:`PostgresRateLimiter` — distributed across worker processes via the
  Postgres engine the multi-tenant stores already share. A **fixed-window
  counter** keyed by ``(bucket_key, window_start)`` with one atomic
  UPSERT-increment, so every worker increments the same row and the limit is
  enforced fleet-wide. Fixed windows can admit up to ~2x at a window boundary;
  that is acceptable for these coarse fairness guardrails (single-org keeps the
  tighter sliding window).

:func:`build_rate_limiter` picks the backend: Postgres when multi-tenant **and**
an engine is available, in-memory otherwise (single-org never takes a DB dep).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from collections.abc import Callable

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.admin.tenancy import is_multi_tenant


class RateLimiter(ABC):
    """Records events for a key and reports whether the key is within its limit.

    A single shared interface so the throttles compose a limiter without caring
    whether it is in-process or Postgres-backed.
    """

    @abstractmethod
    async def hit(self, key: str, *, limit: int, window: float) -> bool:
        """Record one event for ``key`` and return whether it is still allowed.

        Returns ``True`` if, *after* recording this event, ``key`` is within
        ``limit`` over the last ``window`` seconds; ``False`` if the limit is now
        exceeded. ``limit <= 0`` disables the limiter (always allowed).
        """

    @abstractmethod
    async def count(self, key: str, *, window: float) -> int:
        """Read-only: how many events ``key`` has in the current ``window``."""

    @abstractmethod
    async def reset(self, key: str) -> None:
        """Drop all recorded events for ``key`` (e.g. a successful login)."""


class InMemoryRateLimiter(RateLimiter):
    """Single-process sliding window — the legacy throttle logic, extracted.

    Keeps a deque of event timestamps per key, trimmed to ``window`` on each hit.
    The clock is injectable for deterministic tests (defaults to
    ``time.monotonic`` — the wall-clock-independent base the run throttle used).
    """

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, dq: deque[float], horizon: float) -> None:
        while dq and dq[0] < horizon:
            dq.popleft()

    def hit_sync(self, key: str, *, limit: int, window: float) -> bool:
        """Synchronous core (no I/O) — the legacy single-process path.

        Kept public so the throttles can use the in-memory limiter from
        synchronous call sites (single-org) without an event loop.
        """
        if limit <= 0:
            return True
        now = self._now()
        dq = self._hits[key]
        self._trim(dq, now - window)
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True

    def count_sync(self, key: str, *, window: float) -> int:
        dq = self._hits.get(key)
        if not dq:
            return 0
        self._trim(dq, self._now() - window)
        if not dq:
            del self._hits[key]
            return 0
        return len(dq)

    def reset_sync(self, key: str) -> None:
        """Drop all recorded events for ``key`` (legacy ``_LoginThrottle.reset``)."""
        self._hits.pop(key, None)

    async def hit(self, key: str, *, limit: int, window: float) -> bool:
        return self.hit_sync(key, limit=limit, window=window)

    async def count(self, key: str, *, window: float) -> int:
        return self.count_sync(key, window=window)

    async def reset(self, key: str) -> None:
        self.reset_sync(key)


class PostgresRateLimiter(RateLimiter):
    """Distributed fixed-window counter on the shared multi-tenant engine.

    Each hit floors ``now`` to the start of its ``window`` and runs one atomic
    ``INSERT ... ON CONFLICT (bucket_key, window_start) DO UPDATE SET count =
    count + 1 RETURNING count``. Because every worker process targets the same
    ``(bucket_key, window_start)`` row, the increment is serialized by the
    database and the limit is shared across processes. Allowed iff the returned
    count is ``<= limit``. Rows older than a couple of windows are pruned
    opportunistically on write so the table stays small.
    """

    # How many windows of history to retain before opportunistic pruning.
    _PRUNE_KEEP_WINDOWS = 2

    def __init__(self, engine: AsyncEngine, now: Callable[[], float] = time.time) -> None:
        # Postgres windows are anchored to wall-clock seconds so every worker
        # process agrees on the same window boundaries (monotonic clocks don't
        # share an epoch across processes).
        self._engine = engine
        self._now = now

    async def init(self) -> None:
        """Create the schema on SQLite (no-op on Postgres — Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            from sagewai.db.models import Base

            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    @staticmethod
    def _window_start(now: float, window: float) -> int:
        return int(now // window) * int(window)

    async def hit(self, key: str, *, limit: int, window: float) -> bool:
        if limit <= 0:
            return True
        from sagewai.db.dialect import upsert
        from sagewai.db.models import RateLimitModel

        tbl = RateLimitModel.__table__
        now = self._now()
        window_start = self._window_start(now, window)
        stmt = upsert(
            tbl,
            {"bucket_key": key, "window_start": window_start, "count": 1},
            index_elements=["bucket_key", "window_start"],
            set_={"count": tbl.c.count + 1},
            dialect=self._engine.dialect.name,
        ).returning(tbl.c.count)
        async with self._engine.begin() as conn:
            count = (await conn.execute(stmt)).scalar_one()
            # Opportunistically prune rows from windows that can no longer matter.
            await conn.execute(
                sa_delete(tbl).where(
                    tbl.c.window_start
                    < window_start - self._PRUNE_KEEP_WINDOWS * int(window)
                )
            )
        return count <= limit

    async def count(self, key: str, *, window: float) -> int:
        from sagewai.db.models import RateLimitModel

        tbl = RateLimitModel.__table__
        window_start = self._window_start(self._now(), window)
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(tbl.c.count).where(
                        tbl.c.bucket_key == key, tbl.c.window_start == window_start
                    )
                )
            ).first()
        return int(row[0]) if row is not None else 0

    async def reset(self, key: str) -> None:
        from sagewai.db.models import RateLimitModel

        tbl = RateLimitModel.__table__
        async with self._engine.begin() as conn:
            await conn.execute(sa_delete(tbl).where(tbl.c.bucket_key == key))


def build_rate_limiter(engine: AsyncEngine | None) -> RateLimiter:
    """Select the limiter backend.

    Returns a :class:`PostgresRateLimiter` when running multi-tenant **and** an
    engine is available (distributed, correct across processes); otherwise an
    :class:`InMemoryRateLimiter` (single-org default — no DB dependency, and a
    safe fallback if multi-tenant is configured before an engine is wired).
    """
    if is_multi_tenant() and engine is not None:
        return PostgresRateLimiter(engine)
    return InMemoryRateLimiter()
