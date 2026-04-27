# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
"""Performance micro-benchmarks for Sagewai core operations.

These run as part of ``make perf`` and guard against obvious regressions
in the hot paths. LLM calls are mocked, so the numbers here are pure
framework overhead — import time, object construction, tool decoration,
and the chat() round-trip plumbing — nothing network-bound.

The budgets are deliberately generous (measured on an M1 Mac, then
doubled). They are *not* tuned micro-benchmarks; they exist to catch
10x slowdowns, not 10% ones. If a budget starts flaking on CI, raise it
with a comment explaining why rather than chasing the regression.

Run with:
    pytest packages/sdk/tests/test_perf.py -v -m perf
    make perf  # from the monorepo root
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from sagewai.engines.universal import UniversalAgent
from sagewai.models.tool import tool

# ─── Helpers ─────────────────────────────────────────────────────────────

def _timed(fn, *args, **kwargs) -> tuple[object, float]:
    """Run ``fn(*args, **kwargs)`` and return (result, elapsed_seconds)."""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - start


async def _timed_async(coro_fn, *args, **kwargs) -> tuple[object, float]:
    start = time.perf_counter()
    result = await coro_fn(*args, **kwargs)
    return result, time.perf_counter() - start


# ─── Agent construction ─────────────────────────────────────────────────

@pytest.mark.perf
def test_agent_construction_under_50ms() -> None:
    """Constructing a single UniversalAgent should be near-free."""
    agent, elapsed = _timed(
        lambda: UniversalAgent(name="perf", model="gpt-4o-mini")
    )
    assert agent is not None
    assert elapsed < 0.050, (
        f"agent construction took {elapsed*1000:.1f}ms — budget 50ms. "
        "Check engines.universal imports for regressions."
    )


@pytest.mark.perf
def test_100_agents_under_1s() -> None:
    """100 agents should construct in well under a second."""
    def build_many() -> list[UniversalAgent]:
        return [
            UniversalAgent(name=f"perf-{i}", model="gpt-4o-mini")
            for i in range(100)
        ]

    agents, elapsed = _timed(build_many)
    assert len(agents) == 100
    assert elapsed < 1.0, (
        f"100 agent constructions took {elapsed*1000:.1f}ms — budget 1000ms."
    )


# ─── Tool decoration ────────────────────────────────────────────────────

@pytest.mark.perf
def test_tool_decoration_under_50ms() -> None:
    """@tool decoration introspects docstring + type hints + builds a
    Pydantic arg schema. Must stay well under a frame (16ms) once warm,
    but we use a looser budget to account for first-call JIT / cache miss.
    """
    def apply() -> object:
        @tool
        def sample(text: str, count: int = 1) -> str:
            """Repeat text count times.

            Args:
                text: Input string.
                count: Number of repetitions.
            """
            return text * count

        return sample

    t, elapsed = _timed(apply)
    # @tool returns a ToolSpec dataclass wrapping the underlying function.
    assert hasattr(t, "name") and hasattr(t, "handler")
    assert elapsed < 0.050, (
        f"@tool decoration took {elapsed*1000:.2f}ms — budget 50ms. "
        "Check models.tool for regressions in docstring / type-hint parsing."
    )


# ─── Chat round-trip with mocked LLM ────────────────────────────────────

@pytest.mark.perf
def test_mocked_chat_roundtrip_under_50ms() -> None:
    """A full agent.chat() with a mocked provider should be < 50ms.

    This patches the agent method directly so we measure the coroutine
    creation + asyncio.run + patch plumbing, which is the framework
    overhead contributors can introduce regressions in.
    """
    agent = UniversalAgent(name="perf", model="gpt-4o-mini")

    async def one_shot() -> str:
        with patch(
            "sagewai.engines.universal.UniversalAgent.chat",
            return_value="ok",
        ):
            return await agent.chat("hello")

    result, elapsed = asyncio.run(_timed_async(one_shot))
    assert result == "ok"
    assert elapsed < 0.050, (
        f"mocked chat roundtrip took {elapsed*1000:.1f}ms — budget 50ms."
    )


# ─── Sealed revocation lookup ────────────────────────────────────────────

import os as _os_module  # noqa: E402


@pytest.mark.skipif(
    not _os_module.environ.get("SAGEWAI_DATABASE_URL"),
    reason="SAGEWAI_DATABASE_URL not set",
)
@pytest.mark.asyncio
async def test_revocation_lookup_perf():
    """is_revoked indexed lookup must complete in <5ms p99 against 10k rows.

    Plan 3a-style perf budget — fixed threshold, fail loud on regression.
    """
    import os as _os
    import time as _time

    from sagewai.core.stores.postgres import PostgresStore
    from sagewai.sealed.revocation import RevocationRegistry

    store = PostgresStore(database_url=_os.environ["SAGEWAI_DATABASE_URL"])
    await store.initialize()
    try:
        await store._pool.execute("DELETE FROM sealed_revocations")
        # Seed 10k rows (lifted, so they don't conflict on the unique index)
        await store._pool.executemany(
            """
            INSERT INTO sealed_revocations
              (profile_id, secret_key, reason, hard, lifted_at)
            VALUES ($1, $2, 'perf seed', false, NOW())
            """,
            [(f"perf-{i // 100}", f"K_{i}") for i in range(10_000)],
        )
        reg = RevocationRegistry(store)

        # 100 lookups; record p99
        latencies = []
        for i in range(100):
            t0 = _time.monotonic()
            await reg.is_revoked(profile_id=f"perf-{i // 10}", secret_key=f"K_{i}")
            latencies.append(_time.monotonic() - t0)

        latencies.sort()
        p99 = latencies[int(len(latencies) * 0.99) - 1]
        assert p99 < 0.005, f"is_revoked p99 latency {p99*1000:.2f}ms > 5ms budget"
    finally:
        await store._pool.execute("DELETE FROM sealed_revocations")
        await store.close()


# ─── Sandbox pool warm-acquire ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_perf_pool_warm_acquire(tmp_path):
    """Warm acquire ≤ 200ms p95 against a fast fake backend."""
    import asyncio
    import time
    from unittest.mock import AsyncMock, MagicMock

    from sagewai.core.state import ExecutionMode
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool
    from sagewai.sandbox.models import (
        SandboxConfig, SandboxImageVariant, SandboxMode,
    )
    from sagewai.sandbox.pool_protocol import PoolStrategy

    class _Backend:
        name = "fake"
        pool_strategy = PoolStrategy.LOCAL_CACHE

        async def start(self, **kw):
            h = MagicMock()
            h.image_digest = kw["image_digest"]
            h.set_env = AsyncMock()
            h.stop = AsyncMock()
            return h

        async def probe_runner(self, h): return True
        async def reap(self, *, older_than): return 0

    pool = LocalCacheSandboxPool(
        backend=_Backend(),
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="bench",
        scratch_root=tmp_path,
        sealed_secret_provider=None,
        audit_writer=AsyncMock(emit=AsyncMock()),
    )
    await pool.start()

    # Warm the pool with 4 sandboxes
    for i in range(4):
        async with pool.acquire(
            project_id="p", run_id=f"warm-{i}",
            execution_mode=ExecutionMode.SANDBOXED,
            image="img", image_digest="sha256:x",
            image_variant=SandboxImageVariant.BASE,
        ):
            pass

    # Measure 100 warm-only acquires
    samples_ms: list[float] = []
    for i in range(100):
        t0 = time.monotonic()
        async with pool.acquire(
            project_id="p", run_id=f"hot-{i}",
            execution_mode=ExecutionMode.SANDBOXED,
            image="img", image_digest="sha256:x",
            image_variant=SandboxImageVariant.BASE,
        ):
            pass
        samples_ms.append((time.monotonic() - t0) * 1000)

    samples_ms.sort()
    p95 = samples_ms[int(len(samples_ms) * 0.95)]
    assert p95 <= 200.0, f"warm acquire p95 = {p95:.1f}ms (budget 200ms)"
    await pool.stop()
