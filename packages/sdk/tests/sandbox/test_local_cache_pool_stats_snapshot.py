# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
# Copyright 2026 Ali Arda Diri, Berlin, Germany
# (Licensed under AGPL-3.0-or-later — see LICENSE)
"""LocalCacheSandboxPool.stats_snapshot reflects live per-tuple stats."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.models import (
    SandboxConfig,
    SandboxImageVariant,
    SandboxMode,
)
from sagewai.sandbox.pool_protocol import PoolStrategy


class _Backend:
    name = "fake"
    pool_strategy = PoolStrategy.LOCAL_CACHE

    def __init__(self):
        self.started = 0

    async def start(self, **kw):
        self.started += 1
        h = MagicMock()
        h.image_digest = kw["image_digest"]
        h.set_env = AsyncMock()
        h.stop = AsyncMock()
        return h

    async def probe_runner(self, h):
        return True

    async def reap(self, *, older_than):
        return 0

    async def health_check(self):
        from sagewai.sandbox.models import BackendHealth
        return BackendHealth(ok=True, backend="fake", detail="test")


class _Provider:
    async def env_for(self, **kw):
        return {}

    async def cleanup_run(self, **kw):
        from sagewai.sealed.provider import CleanupResult
        return CleanupResult(env_keys_to_unset=[], audit_emitted=True, had_active_revocations=[])


@pytest.mark.asyncio
async def test_snapshot_includes_per_tuple_after_acquire(tmp_path):
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    pool = LocalCacheSandboxPool(
        backend=_Backend(),
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="w-1",
        scratch_root=tmp_path,
        sealed_secret_provider=_Provider(),
        audit_writer=AsyncMock(emit=AsyncMock()),
    )
    await pool.start()

    async with pool.acquire(
        project_id="p", run_id="r-1",
        execution_mode=ExecutionMode.SANDBOXED,
        image="img", image_digest="sha256:x",
        image_variant=SandboxImageVariant.BASE,
    ):
        pass

    snap = await pool.stats_snapshot()
    assert snap.worker_id == "w-1"
    assert len(snap.per_tuple) == 1
    t = snap.per_tuple[0]
    assert t.image_variant == "base"
    assert t.execution_mode == "sandboxed"
    assert t.warm_count == 1   # one sandbox on the bench after release
    assert snap.aggregate.warm_count == 1
    await pool.stop()


@pytest.mark.asyncio
async def test_snapshot_empty_when_no_runs(tmp_path):
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    pool = LocalCacheSandboxPool(
        backend=_Backend(),
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="w-1",
        scratch_root=tmp_path,
        sealed_secret_provider=None,
        audit_writer=None,
    )
    await pool.start()
    snap = await pool.stats_snapshot()
    assert snap.per_tuple == []
    assert snap.aggregate.warm_count == 0
    assert snap.aggregate.hit_rate_1h is None
    await pool.stop()
