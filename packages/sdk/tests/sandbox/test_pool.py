# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for LocalCacheSandboxPool lifecycle (uses NullBackend to avoid Docker).

NOTE: The legacy SandboxPool class was retired in Plan 1.5 Task 18.
pool.py is now a back-compat shim re-exporting the Protocol +
LocalCacheSandboxPool. Tests have been migrated to use
LocalCacheSandboxPool directly.
"""
from pathlib import Path

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.models import (
    SandboxConfig,
    SandboxImageVariant,
    SandboxMode,
    ToolCall,
)
from sagewai.sandbox.null_backend import NullBackend
from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool


def _make_pool(tmp_path: Path, mode: SandboxMode = SandboxMode.PER_RUN) -> LocalCacheSandboxPool:
    return LocalCacheSandboxPool(
        backend=NullBackend(),
        config=SandboxConfig(mode=mode),
        worker_id="w1",
        scratch_root=tmp_path,
    )


def _acquire_kwargs(**overrides):
    defaults = dict(
        project_id="p1",
        run_id="r1",
        execution_mode=ExecutionMode.SANDBOXED,
        image="null",
        image_digest="sha256:" + "a" * 64,
        image_variant=SandboxImageVariant.BASE,
    )
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_pool_returns_handle(tmp_path: Path):
    """acquire() yields a usable sandbox handle."""
    pool = _make_pool(tmp_path)
    async with pool.acquire(**_acquire_kwargs()) as sbx:
        assert sbx is not None


@pytest.mark.asyncio
async def test_pool_sequential_runs_each_get_a_handle(tmp_path: Path):
    """Sequential runs each acquire a usable sandbox handle.

    NOTE: LocalCacheSandboxPool benches handles after release (warm-pool).
    A second acquire after the first release may reuse the same sandboxed
    container — that is intentional. This test verifies both runs succeed
    and obtain a handle (whether new or pooled).
    """
    pool = _make_pool(tmp_path)
    ids: list[str] = []
    async with pool.acquire(**_acquire_kwargs(run_id="r1")) as s:
        ids.append(s.sandbox_id)
    async with pool.acquire(**_acquire_kwargs(run_id="r2")) as s:
        ids.append(s.sandbox_id)
    assert len(ids) == 2
    assert all(isinstance(i, str) and i for i in ids)


@pytest.mark.asyncio
async def test_pool_none_mode_uses_null_backend(tmp_path: Path):
    pool = _make_pool(tmp_path, mode=SandboxMode.NONE)
    async with pool.acquire(**_acquire_kwargs()) as sbx:
        r = await sbx.exec(ToolCall(tool="bash", args={"command": "echo ok"}, call_id="c1"))
        assert r.stdout.strip() == "ok"


@pytest.mark.asyncio
async def test_pool_scratch_dir_created(tmp_path: Path):
    pool = _make_pool(tmp_path)
    async with pool.acquire(**_acquire_kwargs(run_id="r1")):
        expected = tmp_path / "w1" / "runs" / "r1"
        assert expected.is_dir()


@pytest.mark.asyncio
async def test_advertised_labels(tmp_path):
    """advertised_labels() returns mode, backend, network_policy."""
    from sagewai.sandbox.models import SandboxConfig, SandboxMode
    from sagewai.sandbox.null_backend import NullBackend

    pool = LocalCacheSandboxPool(
        backend=NullBackend(),
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="w-test",
        scratch_root=tmp_path,
    )
    labels = pool.advertised_labels()
    assert labels["sandbox.mode"] == "per_run"
    assert labels["sandbox.backend"] == "null"
    assert labels["sandbox.network_policy"] == "none"


@pytest.mark.asyncio
async def test_pool_shim_exports_protocol_and_concrete(tmp_path):
    """pool.py shim exports SandboxPool (Protocol) and LocalCacheSandboxPool."""
    from sagewai.sandbox.pool import LocalCacheSandboxPool as ShimConcrete
    from sagewai.sandbox.pool import SandboxPool as ShimProtocol
    from sagewai.sandbox.pool_protocol import SandboxPool as ProtocolDirect
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool as ConcreteDirect

    # Shim re-exports should be the same objects
    assert ShimProtocol is ProtocolDirect
    assert ShimConcrete is ConcreteDirect

    # LocalCacheSandboxPool satisfies the Protocol
    pool = LocalCacheSandboxPool(
        backend=NullBackend(),
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="w-test",
        scratch_root=tmp_path,
    )
    assert isinstance(pool, ShimProtocol)
