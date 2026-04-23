# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for NullBackend — mode=none in-process execution."""
from datetime import timedelta

import pytest

from sagewai.sandbox.models import (
    NetworkPolicy,
    ResourceLimits,
    SandboxLifetime,
    SandboxMode,
    ToolCall,
)
from sagewai.sandbox.null_backend import NullBackend


@pytest.mark.asyncio
async def test_null_backend_health_always_ok():
    h = await NullBackend().health_check()
    assert h.ok
    assert h.backend == "null"


@pytest.mark.asyncio
async def test_null_backend_start_returns_handle(tmp_path):
    b = NullBackend()
    h = await b.start(
        project_id="p1",
        run_id="r1",
        image="ignored",
        image_digest="",
        env={"K": "V"},
        network_policy=NetworkPolicy.FULL,
        resource_limits=ResourceLimits(),
        workdir_mount=tmp_path,
        lifetime=SandboxLifetime.PER_RUN,
    )
    assert h.mode is SandboxMode.NONE
    assert h.sandbox_id.startswith("null-")
    await h.stop()


@pytest.mark.asyncio
async def test_null_backend_bash_exec(tmp_path):
    b = NullBackend()
    h = await b.start(
        project_id="p1",
        run_id="r1",
        image="ignored",
        image_digest="",
        env={},
        network_policy=NetworkPolicy.FULL,
        resource_limits=ResourceLimits(),
        workdir_mount=tmp_path,
        lifetime=SandboxLifetime.PER_RUN,
    )
    result = await h.exec(
        ToolCall(tool="bash", args={"command": "echo hi"}, call_id="c1")
    )
    assert result.ok
    assert result.exit_code == 0
    assert result.stdout.strip() == "hi"
    await h.stop()


@pytest.mark.asyncio
async def test_null_backend_env_scrub_no_host_leak(tmp_path, monkeypatch):
    """Host env must not leak into the subprocess spawned for bash."""
    monkeypatch.setenv("HOST_SECRET", "leaked")
    b = NullBackend()
    h = await b.start(
        project_id="p1",
        run_id="r1",
        image="ignored",
        image_digest="",
        env={"PROJECT_KEY": "ok"},
        network_policy=NetworkPolicy.FULL,
        resource_limits=ResourceLimits(),
        workdir_mount=tmp_path,
        lifetime=SandboxLifetime.PER_RUN,
    )
    result = await h.exec(
        ToolCall(
            tool="bash",
            args={"command": "echo $HOST_SECRET-$PROJECT_KEY"},
            call_id="c1",
        )
    )
    # HOST_SECRET should not be passed through; PROJECT_KEY should be.
    assert result.stdout.strip() == "-ok"
    await h.stop()


@pytest.mark.asyncio
async def test_null_backend_reap_noop():
    b = NullBackend()
    reaped = await b.reap(older_than=timedelta(minutes=10))
    assert reaped == 0
