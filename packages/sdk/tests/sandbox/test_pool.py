"""Tests for SandboxPool lifecycle (uses NullBackend to avoid Docker)."""
from pathlib import Path

import pytest

from sagewai.sandbox.models import (
    SandboxConfig,
    SandboxMode,
    ToolCall,
)
from sagewai.sandbox.null_backend import NullBackend
from sagewai.sandbox.pool import SandboxPool


@pytest.mark.asyncio
async def test_pool_per_run_returns_same_handle_within_run(tmp_path: Path):
    pool = SandboxPool(
        backend=NullBackend(),
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="w1",
        scratch_root=tmp_path,
    )
    async with pool.acquire(project_id="p1", run_id="r1", image="null") as sbx1:
        async with pool.acquire(project_id="p1", run_id="r1", image="null") as sbx2:
            assert sbx1.sandbox_id == sbx2.sandbox_id


@pytest.mark.asyncio
async def test_pool_per_run_isolated_between_runs(tmp_path: Path):
    pool = SandboxPool(
        backend=NullBackend(),
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="w1",
        scratch_root=tmp_path,
    )
    ids: list[str] = []
    async with pool.acquire(project_id="p1", run_id="r1", image="null") as s:
        ids.append(s.sandbox_id)
    async with pool.acquire(project_id="p1", run_id="r2", image="null") as s:
        ids.append(s.sandbox_id)
    assert ids[0] != ids[1]


@pytest.mark.asyncio
async def test_pool_none_mode_uses_null_backend(tmp_path: Path):
    pool = SandboxPool(
        backend=NullBackend(),
        config=SandboxConfig(mode=SandboxMode.NONE),
        worker_id="w1",
        scratch_root=tmp_path,
    )
    async with pool.acquire(project_id="p1", run_id="r1", image="ignored") as sbx:
        r = await sbx.exec(ToolCall(tool="bash", args={"command": "echo ok"}, call_id="c1"))
        assert r.stdout.strip() == "ok"


@pytest.mark.asyncio
async def test_pool_scratch_dir_created(tmp_path: Path):
    pool = SandboxPool(
        backend=NullBackend(),
        config=SandboxConfig(mode=SandboxMode.PER_RUN),
        worker_id="w1",
        scratch_root=tmp_path,
    )
    async with pool.acquire(project_id="p1", run_id="r1", image="null"):
        expected = tmp_path / "w1" / "runs" / "r1"
        assert expected.is_dir()
