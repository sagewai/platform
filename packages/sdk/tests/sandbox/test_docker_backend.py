# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for DockerBackend. Gated on a reachable Docker daemon."""
import os
from datetime import timedelta
from pathlib import Path

import pytest

from sagewai.sandbox.models import (
    NetworkPolicy,
    ResourceLimits,
    SandboxLifetime,
    ToolCall,
)

pytest.importorskip("aiodocker")

pytestmark = pytest.mark.skipif(
    os.environ.get("SAGEWAI_SANDBOX_DOCKER_TESTS") != "1",
    reason="Docker tests disabled. Set SAGEWAI_SANDBOX_DOCKER_TESTS=1 and ensure sagewai-sandbox-base:dev is built.",
)


@pytest.mark.asyncio
async def test_docker_backend_health():
    from sagewai.sandbox.docker_backend import DockerBackend

    backend = DockerBackend()
    health = await backend.health_check()
    await backend.close()
    assert health.ok
    assert health.backend == "docker"


@pytest.mark.asyncio
async def test_docker_backend_bash_exec(tmp_path: Path):
    from sagewai.sandbox.docker_backend import DockerBackend

    backend = DockerBackend()
    handle = await backend.start(
        project_id="p1",
        run_id="r1",
        image="ghcr.io/sagewai/sandbox-base:dev",
        image_digest="",
        env={"MY_VAR": "hello"},
        network_policy=NetworkPolicy.NONE,
        resource_limits=ResourceLimits(),
        workdir_mount=tmp_path,
        lifetime=SandboxLifetime.PER_RUN,
    )
    try:
        result = await handle.exec(
            ToolCall(
                tool="bash",
                args={"command": "echo $MY_VAR"},
                call_id="c1",
                timeout_s=10,
            )
        )
        assert result.ok, result.error or result.stderr
        assert result.stdout.strip() == "hello"
    finally:
        await handle.stop()
        await backend.close()


@pytest.mark.asyncio
async def test_docker_backend_reap_orphans(tmp_path: Path):
    """reap(older_than=0) must remove any sandbox tagged with sagewai.sandbox_id."""
    from sagewai.sandbox.docker_backend import DockerBackend

    backend = DockerBackend()
    await backend.start(
        project_id="p1",
        run_id="r-orphan",
        image="ghcr.io/sagewai/sandbox-base:dev",
        image_digest="",
        env={},
        network_policy=NetworkPolicy.NONE,
        resource_limits=ResourceLimits(),
        workdir_mount=tmp_path,
        lifetime=SandboxLifetime.PER_RUN,
    )
    reaped = await backend.reap(older_than=timedelta(0))
    await backend.close()
    assert reaped >= 1
