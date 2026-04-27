# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Idle reaper + global-LRU eviction."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.models import (
    NetworkPolicy,
    SandboxConfig,
    SandboxImageVariant,
    SandboxMode,
)
from sagewai.sandbox.pool_protocol import BenchEntry, PoolKey, PoolStrategy


class _Backend:
    name = "fake"
    pool_strategy = PoolStrategy.LOCAL_CACHE
    start = AsyncMock()
    probe_runner = AsyncMock(return_value=True)
    reap = AsyncMock(return_value=0)


def _stub_handle(name: str):
    h = MagicMock(name=name)
    h.set_env = AsyncMock()
    h.stop = AsyncMock()
    return h


@pytest.mark.asyncio
async def test_reap_evicts_idle_entries_past_timeout(tmp_path):
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    pool = LocalCacheSandboxPool(
        backend=_Backend(),
        config=SandboxConfig(
            mode=SandboxMode.PER_RUN,
            pool_idle_timeout_s=1,
            pool_reap_interval_s=1,
        ),
        worker_id="w-1",
        scratch_root=tmp_path,
    )
    key = PoolKey(
        image_digest="sha256:x",
        sandbox_mode=SandboxMode.PER_RUN,
        execution_mode=ExecutionMode.SANDBOXED,
        network_policy=NetworkPolicy.NONE,
        image_variant=SandboxImageVariant.BASE,
    )
    h = _stub_handle("idle")
    bench_now = datetime.now(timezone.utc) - timedelta(seconds=120)
    pool._benches[key] = deque([BenchEntry(handle=h, pooled_at=bench_now, last_run_id=None)])
    pool._global_warm_count = 1

    await pool._reap_once(now=datetime.now(timezone.utc))

    assert pool._global_warm_count == 0
    h.stop.assert_awaited()


@pytest.mark.asyncio
async def test_global_lru_evicts_oldest_when_over_cap(tmp_path):
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    pool = LocalCacheSandboxPool(
        backend=_Backend(),
        config=SandboxConfig(
            mode=SandboxMode.PER_RUN,
            pool_max_warm_global=2,
            pool_idle_timeout_s=99999,   # disable idle path for this test
            pool_reap_interval_s=1,
        ),
        worker_id="w-1",
        scratch_root=tmp_path,
    )
    key = PoolKey(
        image_digest="sha256:x",
        sandbox_mode=SandboxMode.PER_RUN,
        execution_mode=ExecutionMode.SANDBOXED,
        network_policy=NetworkPolicy.NONE,
        image_variant=SandboxImageVariant.BASE,
    )
    base = datetime.now(timezone.utc)
    handles = [_stub_handle(f"h-{i}") for i in range(4)]
    pool._benches[key] = deque(
        [
            BenchEntry(handle=handles[i], pooled_at=base - timedelta(minutes=10 - i), last_run_id=None)
            for i in range(4)
        ]
    )
    pool._global_warm_count = 4

    await pool._reap_once(now=base)

    # Two oldest got stopped; warm count is now 2.
    assert pool._global_warm_count == 2
    handles[0].stop.assert_awaited()
    handles[1].stop.assert_awaited()
    handles[2].stop.assert_not_awaited()
    handles[3].stop.assert_not_awaited()
