# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Release path: cleanup_run runs; on success the sandbox enters the bench."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.models import (
    NetworkPolicy,
    SandboxConfig,
    SandboxImageVariant,
    SandboxMode,
)
from sagewai.sandbox.pool_protocol import PoolStrategy


class _FakeBackend:
    name = "fake"
    pool_strategy = PoolStrategy.LOCAL_CACHE

    def __init__(self):
        self._handles_started = 0
        self.start = AsyncMock(side_effect=self._start)
        self.probe_runner = AsyncMock(return_value=True)
        self.reap = AsyncMock(return_value=0)

    async def _start(self, **kwargs):
        self._handles_started += 1
        h = MagicMock()
        h.image_digest = kwargs["image_digest"]
        h.set_env = AsyncMock()
        h.stop = AsyncMock()
        return h


class _FakeProvider:
    """Mimics SealedSecretProvider.cleanup_run (Sealed-iii.A)."""

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls: list[dict] = []
        from sagewai.sealed.provider import CleanupResult
        self._CleanupResult = CleanupResult

    async def env_for(self, **kwargs):
        return {}

    async def cleanup_run(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("simulated cleanup failure")
        return self._CleanupResult(
            env_keys_to_unset=list(kwargs.get("effective_env_keys") or []),
            audit_emitted=True,
            had_active_revocations=[],
        )


@pytest.mark.asyncio
async def test_release_calls_cleanup_run_and_pools_on_success(tmp_path):
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    backend = _FakeBackend()
    provider = _FakeProvider()
    pool = LocalCacheSandboxPool(
        backend=backend,
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="w-1",
        scratch_root=tmp_path,
        sealed_secret_provider=provider,
        audit_writer=AsyncMock(emit=AsyncMock()),
    )
    await pool.start()
    async with pool.acquire(
        project_id="p-1",
        run_id="r-1",
        execution_mode=ExecutionMode.SANDBOXED,
        image="img",
        image_digest="sha256:x",
        image_variant=SandboxImageVariant.BASE,
    ):
        pass

    assert len(provider.calls) == 1
    assert backend._handles_started == 1
    # Bench has the released entry now.
    assert pool._global_warm_count == 1
    await pool.stop()


@pytest.mark.asyncio
async def test_release_discards_handle_when_cleanup_fails(tmp_path):
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    backend = _FakeBackend()
    provider = _FakeProvider(fail=True)
    audit = AsyncMock(emit=AsyncMock())
    pool = LocalCacheSandboxPool(
        backend=backend,
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="w-1",
        scratch_root=tmp_path,
        sealed_secret_provider=provider,
        audit_writer=audit,
    )
    await pool.start()
    async with pool.acquire(
        project_id="p-1",
        run_id="r-1",
        execution_mode=ExecutionMode.SANDBOXED,
        image="img",
        image_digest="sha256:x",
        image_variant=SandboxImageVariant.BASE,
    ):
        pass

    assert pool._global_warm_count == 0
    await pool.stop()


@pytest.mark.asyncio
async def test_passive_eviction_when_bench_full(tmp_path):
    """When the per-tuple bench is at max, the released sandbox is stopped, not pooled."""
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    backend = _FakeBackend()
    provider = _FakeProvider()
    config = SandboxConfig(
        mode=SandboxMode.PER_RUN,
        pool_max_warm_per_tuple=1,    # tight bench
        pool_max_warm_global=10,
    )
    pool = LocalCacheSandboxPool(
        backend=backend,
        config=config,
        worker_id="w-1",
        scratch_root=tmp_path,
        sealed_secret_provider=provider,
        audit_writer=AsyncMock(emit=AsyncMock()),
    )
    await pool.start()

    # Concurrent leases: open three contexts at once. The third release evicts.
    contexts = [
        pool.acquire(
            project_id="p", run_id=f"r-{i}",
            execution_mode=ExecutionMode.SANDBOXED,
            image="img", image_digest="sha256:x",
            image_variant=SandboxImageVariant.BASE,
        )
        for i in range(3)
    ]
    aentered = [await c.__aenter__() for c in contexts]
    for c in contexts:
        await c.__aexit__(None, None, None)

    # Bench cap is 1; the other two were stopped on release.
    assert pool._global_warm_count == 1
    await pool.stop()


@pytest.mark.asyncio
async def test_release_emits_pool_release_audit(tmp_path):
    """The pool emits pool.warm + pool.acquire + pool.release audit events
    over a single acquire+release cycle."""
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool
    from sagewai.sandbox.models import SandboxConfig, SandboxImageVariant, SandboxMode
    from sagewai.core.state import ExecutionMode

    audit = AsyncMock()
    audit.emit = AsyncMock()
    pool = LocalCacheSandboxPool(
        backend=_FakeBackend(),
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="w-1",
        scratch_root=tmp_path,
        sealed_secret_provider=_FakeProvider(),
        audit_writer=audit,
    )
    await pool.start()
    async with pool.acquire(
        project_id="p", run_id="r",
        execution_mode=ExecutionMode.SANDBOXED,
        image="img", image_digest="sha256:x",
        image_variant=SandboxImageVariant.BASE,
    ):
        pass
    events = [c.kwargs["event_type"] for c in audit.emit.call_args_list]
    assert "pool.warm" in events
    assert "pool.acquire" in events
    assert "pool.release" in events
    await pool.stop()
