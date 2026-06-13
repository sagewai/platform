# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pluggable rate limiter: in-memory (single-org default) + Postgres (distributed).

The in-memory limiter reproduces the single-process sliding window of the two
legacy throttles. The Postgres limiter is a fixed-window counter on the shared
engine, so two limiter instances pointed at one engine enforce a *single* shared
limit — the property that makes it correct across worker processes.
"""

import os

import pytest
import pytest_asyncio

from sagewai.db.engine import create_engine
from sagewai.db.models import Base
from sagewai.db.rate_limit import (
    InMemoryRateLimiter,
    PostgresRateLimiter,
    build_rate_limiter,
)

# Dual-dialect engine fixture (mirrors tests/db/conftest.py, which is scoped to
# tests/db/): SQLite always; Postgres when SAGEWAI_TEST_DATABASE_URL is set, so
# the distributed assertions run against real Postgres in CI when configured.
_PG_URL = os.environ.get("SAGEWAI_TEST_DATABASE_URL")
_DIALECT_PARAMS = ["sqlite"] + (["postgres"] if _PG_URL else [])


@pytest_asyncio.fixture(params=_DIALECT_PARAMS)
async def dialect_engine(request, tmp_path):
    """Async engine with the full schema. SQLite always; Postgres when configured."""
    if request.param == "sqlite":
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'rl.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        engine = create_engine(_PG_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


class _Clock:
    """Monotonic-style injectable clock; advance with ``tick``."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


# --------------------------------------------------------------- InMemory ----


@pytest.mark.asyncio
async def test_in_memory_allows_up_to_limit_then_denies():
    clock = _Clock()
    rl = InMemoryRateLimiter(now=clock)
    # limit=3 over a 60s window: first three within the limit, fourth over it.
    assert await rl.hit("k", limit=3, window=60) is True
    assert await rl.hit("k", limit=3, window=60) is True
    assert await rl.hit("k", limit=3, window=60) is True
    assert await rl.hit("k", limit=3, window=60) is False


@pytest.mark.asyncio
async def test_in_memory_recovers_after_window():
    clock = _Clock()
    rl = InMemoryRateLimiter(now=clock)
    assert await rl.hit("k", limit=1, window=60) is True
    assert await rl.hit("k", limit=1, window=60) is False
    clock.tick(61)  # slide past the window — the old hit ages out
    assert await rl.hit("k", limit=1, window=60) is True


@pytest.mark.asyncio
async def test_in_memory_keys_are_independent():
    clock = _Clock()
    rl = InMemoryRateLimiter(now=clock)
    assert await rl.hit("a", limit=1, window=60) is True
    assert await rl.hit("a", limit=1, window=60) is False
    assert await rl.hit("b", limit=1, window=60) is True  # different key unaffected


@pytest.mark.asyncio
async def test_in_memory_count_is_read_only():
    clock = _Clock()
    rl = InMemoryRateLimiter(now=clock)
    assert await rl.count("k", window=60) == 0
    await rl.hit("k", limit=10, window=60)
    await rl.hit("k", limit=10, window=60)
    assert await rl.count("k", window=60) == 2  # count() records nothing itself
    assert await rl.count("k", window=60) == 2


# --------------------------------------------------------------- Postgres ----


@pytest.mark.asyncio
async def test_postgres_allows_up_to_limit_then_denies(dialect_engine):
    clock = _Clock()
    rl = PostgresRateLimiter(dialect_engine, now=clock)
    await rl.init()
    assert await rl.hit("k", limit=3, window=60) is True
    assert await rl.hit("k", limit=3, window=60) is True
    assert await rl.hit("k", limit=3, window=60) is True
    assert await rl.hit("k", limit=3, window=60) is False


@pytest.mark.asyncio
async def test_postgres_recovers_after_window(dialect_engine):
    clock = _Clock()
    rl = PostgresRateLimiter(dialect_engine, now=clock)
    await rl.init()
    assert await rl.hit("k", limit=1, window=60) is True
    assert await rl.hit("k", limit=1, window=60) is False
    clock.tick(61)  # next fixed window — fresh counter
    assert await rl.hit("k", limit=1, window=60) is True


@pytest.mark.asyncio
async def test_postgres_is_distributed_across_instances(dialect_engine):
    """Two separate limiter instances on one engine share a single limit.

    This is the cross-process property: each worker process builds its own
    PostgresRateLimiter, but they all increment the same row, so the limit is
    enforced fleet-wide rather than per-process.
    """
    clock = _Clock()
    rl_a = PostgresRateLimiter(dialect_engine, now=clock)
    rl_b = PostgresRateLimiter(dialect_engine, now=clock)
    await rl_a.init()
    # limit=2 shared: instance A uses one, instance B uses the other, A's next is denied.
    assert await rl_a.hit("shared", limit=2, window=60) is True
    assert await rl_b.hit("shared", limit=2, window=60) is True
    assert await rl_a.hit("shared", limit=2, window=60) is False
    assert await rl_b.hit("shared", limit=2, window=60) is False


@pytest.mark.asyncio
async def test_postgres_count_reflects_shared_window(dialect_engine):
    clock = _Clock()
    rl_a = PostgresRateLimiter(dialect_engine, now=clock)
    rl_b = PostgresRateLimiter(dialect_engine, now=clock)
    await rl_a.init()
    await rl_a.hit("k", limit=10, window=60)
    await rl_b.hit("k", limit=10, window=60)
    assert await rl_a.count("k", window=60) == 2  # both instances' hits are visible
    assert await rl_b.count("k", window=60) == 2


@pytest.mark.asyncio
async def test_postgres_prunes_stale_windows(dialect_engine):
    """Old windows are opportunistically pruned so the table doesn't grow forever."""
    from sqlalchemy import func, select

    from sagewai.db.models import RateLimitModel

    clock = _Clock()
    rl = PostgresRateLimiter(dialect_engine, now=clock)
    await rl.init()
    await rl.hit("k", limit=10, window=60)  # window starting at 1000
    clock.tick(60 * 10)  # ten windows later
    await rl.hit("k", limit=10, window=60)  # prunes rows older than a couple of windows
    async with dialect_engine.connect() as conn:
        rows = (
            await conn.execute(select(func.count()).select_from(RateLimitModel.__table__))
        ).scalar_one()
    assert rows == 1  # only the current window's row survives


# ------------------------------------------------------------ build_rate_limiter


def test_build_rate_limiter_single_org_is_in_memory():
    # Single-org (multi_tenant=False, the default) never takes a DB dependency,
    # even with an engine available.
    assert isinstance(build_rate_limiter(object(), multi_tenant=False), InMemoryRateLimiter)
    assert isinstance(build_rate_limiter(object()), InMemoryRateLimiter)
    assert isinstance(build_rate_limiter(None, multi_tenant=False), InMemoryRateLimiter)


def test_build_rate_limiter_multi_with_engine_is_postgres(dialect_engine):
    assert isinstance(
        build_rate_limiter(dialect_engine, multi_tenant=True), PostgresRateLimiter
    )


def test_build_rate_limiter_multi_without_engine_falls_back():
    # Multi-tenant but no engine wired yet → safe in-memory fallback.
    assert isinstance(build_rate_limiter(None, multi_tenant=True), InMemoryRateLimiter)
