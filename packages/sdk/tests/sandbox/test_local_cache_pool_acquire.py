# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LocalCacheSandboxPool acquire — cold path (Task 8) + warm path (Task 10)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.models import (
    NetworkPolicy,
    ResourceLimits,
    SandboxConfig,
    SandboxImageVariant,
    SandboxLifetime,
    SandboxMode,
)


class FakeBackend:
    name = "fake"
    pool_strategy = None  # set in fixture

    def __init__(self) -> None:
        self.start = AsyncMock(side_effect=self._start)
        self.probe_runner = AsyncMock(return_value=True)
        self.reap = AsyncMock(return_value=0)
        self.started: int = 0

    async def _start(self, **kwargs):
        self.started += 1
        h = MagicMock()
        h.image_digest = kwargs["image_digest"]
        h.set_env = AsyncMock()
        h.stop = AsyncMock()
        h.exec = AsyncMock()
        return h


@pytest.fixture
def fake_backend():
    from sagewai.sandbox.pool_protocol import PoolStrategy

    backend = FakeBackend()
    backend.pool_strategy = PoolStrategy.LOCAL_CACHE
    return backend


@pytest.mark.asyncio
async def test_cold_acquire_starts_a_new_sandbox(fake_backend, tmp_path):
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    config = SandboxConfig(mode=SandboxMode.PER_RUN, network_policy=NetworkPolicy.NONE)
    pool = LocalCacheSandboxPool(
        backend=fake_backend,
        config=config,
        worker_id="w-1",
        scratch_root=tmp_path,
        sealed_secret_provider=None,
        audit_writer=None,
    )
    await pool.start()

    async with pool.acquire(
        project_id="p-1",
        run_id="r-1",
        execution_mode=ExecutionMode.SANDBOXED,
        image="img",
        image_digest="sha256:x",
        image_variant=SandboxImageVariant.BASE,
    ) as handle:
        assert handle is not None

    assert fake_backend.started == 1
    await pool.stop()


@pytest.mark.asyncio
async def test_probe_failure_does_not_mark_digest_probed(fake_backend, tmp_path):
    """If probe_runner raises, the digest stays unprobed and a future acquire retries."""
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    fake_backend.probe_runner = AsyncMock(side_effect=RuntimeError("boom"))

    # pool_max_warm_per_tuple=0 disables bench so both acquires are cold starts,
    # which is the only way probe_runner can be re-attempted on the same digest.
    config = SandboxConfig(mode=SandboxMode.PER_RUN, pool_max_warm_per_tuple=0)
    pool = LocalCacheSandboxPool(
        backend=fake_backend,
        config=config,
        worker_id="w-1",
        scratch_root=tmp_path,
        sealed_secret_provider=None,
        audit_writer=None,
    )
    await pool.start()

    async with pool.acquire(
        project_id="p",
        run_id="r-1",
        execution_mode=ExecutionMode.SANDBOXED,
        image="img",
        image_digest="sha256:y",
        image_variant=SandboxImageVariant.BASE,
    ):
        pass

    # Digest NOT marked because probe failed.
    assert "sha256:y" not in pool._probed_digests
    # Future acquires will re-attempt probe.
    assert fake_backend.probe_runner.call_count == 1

    async with pool.acquire(
        project_id="p",
        run_id="r-2",
        execution_mode=ExecutionMode.SANDBOXED,
        image="img",
        image_digest="sha256:y",
        image_variant=SandboxImageVariant.BASE,
    ):
        pass

    assert fake_backend.probe_runner.call_count == 2  # tried again
    await pool.stop()


class _PassthroughProvider:
    async def env_for(self, **kwargs):
        return {}

    async def cleanup_run(self, **kwargs):
        from sagewai.sealed.provider import CleanupResult
        return CleanupResult(env_keys_to_unset=[], audit_emitted=True, had_active_revocations=[])


@pytest.mark.asyncio
async def test_warm_acquire_reuses_pooled_sandbox(fake_backend, tmp_path):
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    config = SandboxConfig(mode=SandboxMode.PER_RUN)
    pool = LocalCacheSandboxPool(
        backend=fake_backend,
        config=config,
        worker_id="w-1",
        scratch_root=tmp_path,
        sealed_secret_provider=_PassthroughProvider(),
        audit_writer=AsyncMock(emit=AsyncMock()),
    )
    await pool.start()

    # First acquire: cold start
    async with pool.acquire(
        project_id="p", run_id="r-1",
        execution_mode=ExecutionMode.SANDBOXED,
        image="img", image_digest="sha256:x",
        image_variant=SandboxImageVariant.BASE,
    ) as h1:
        pass
    assert fake_backend.started == 1
    assert pool._global_warm_count == 1

    # Second acquire on same key: warm reuse, no new start
    async with pool.acquire(
        project_id="p", run_id="r-2",
        execution_mode=ExecutionMode.SANDBOXED,
        image="img", image_digest="sha256:x",
        image_variant=SandboxImageVariant.BASE,
    ) as h2:
        pass
    assert fake_backend.started == 1   # same as before
    assert pool._global_warm_count == 1

    await pool.stop()
