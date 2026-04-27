# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for SandboxedToolDispatcher — thin wrapper around SandboxHandle.exec."""
from pathlib import Path

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.models import SandboxConfig, SandboxImageVariant, SandboxMode
from sagewai.sandbox.null_backend import NullBackend
from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool
from sagewai.sandbox.tool_dispatcher import SandboxedToolDispatcher


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
async def test_dispatcher_runs_bash(tmp_path: Path):
    pool = LocalCacheSandboxPool(
        backend=NullBackend(),
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="w1",
        scratch_root=tmp_path,
    )
    async with pool.acquire(**_acquire_kwargs()) as handle:
        d = SandboxedToolDispatcher(handle)
        result = await d.run(tool="bash", args={"command": "echo hi"}, call_id="c1")
        assert result.ok
        assert result.stdout.strip() == "hi"


@pytest.mark.asyncio
async def test_dispatcher_passes_timeout(tmp_path: Path):
    pool = LocalCacheSandboxPool(
        backend=NullBackend(),
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="w1",
        scratch_root=tmp_path,
    )
    async with pool.acquire(**_acquire_kwargs()) as handle:
        d = SandboxedToolDispatcher(handle)
        result = await d.run(
            tool="bash",
            args={"command": "sleep 5"},
            call_id="c1",
            timeout_s=0.2,
        )
        assert not result.ok
        assert "timeout" in (result.error or "").lower()
