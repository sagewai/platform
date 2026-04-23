# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Integration test: WorkflowWorker starts a SandboxPool and tears it down cleanly."""
from pathlib import Path

import pytest

from sagewai.core.worker import WorkflowWorker
from sagewai.sandbox.models import SandboxConfig, SandboxMode
from sagewai.sandbox.null_backend import NullBackend


class _DummyStore:
    async def initialize(self): return None
    async def register_worker(self, *a, **kw): return None
    async def deregister_worker(self, *a, **kw): return None
    async def worker_heartbeat(self, *a, **kw): return None


@pytest.mark.asyncio
async def test_worker_exposes_sandbox_pool(tmp_path: Path):
    worker = WorkflowWorker(
        store=_DummyStore(),
        workflow_registry={},
        sandbox_backend=NullBackend(),
        sandbox_config=SandboxConfig(mode=SandboxMode.PER_RUN),
        sandbox_scratch_root=tmp_path,
    )
    await worker._start_sandbox_pool()
    try:
        assert worker._sandbox_pool is not None
        assert worker._sandbox_pool.mode is SandboxMode.PER_RUN
    finally:
        await worker._sandbox_pool.stop()


@pytest.mark.asyncio
async def test_worker_mode_none_no_backend_required(tmp_path: Path):
    worker = WorkflowWorker(
        store=_DummyStore(),
        workflow_registry={},
        sandbox_config=SandboxConfig(mode=SandboxMode.NONE),
        sandbox_scratch_root=tmp_path,
    )
    await worker._start_sandbox_pool()
    try:
        assert worker._sandbox_pool.mode is SandboxMode.NONE
    finally:
        await worker._sandbox_pool.stop()
