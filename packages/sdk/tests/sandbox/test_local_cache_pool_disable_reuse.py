# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""pool_disable_warm_reuse=True bypasses pooling entirely."""
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

    async def probe_runner(self, h): return True
    async def reap(self, *, older_than): return 0


@pytest.mark.asyncio
async def test_disable_reuse_skips_warm_bench(tmp_path):
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    backend = _Backend()
    pool = LocalCacheSandboxPool(
        backend=backend,
        config=SandboxConfig(
            mode=SandboxMode.PER_RUN,
            pool_disable_warm_reuse=True,
        ),
        worker_id="w-1",
        scratch_root=tmp_path,
        sealed_secret_provider=None,
        audit_writer=AsyncMock(emit=AsyncMock()),
    )
    await pool.start()
    for run_id in ("r-1", "r-2", "r-3"):
        async with pool.acquire(
            project_id="p", run_id=run_id,
            execution_mode=ExecutionMode.SANDBOXED,
            image="img", image_digest="sha256:x",
            image_variant=SandboxImageVariant.BASE,
        ):
            pass

    assert backend.started == 3
    assert pool._global_warm_count == 0
    await pool.stop()
