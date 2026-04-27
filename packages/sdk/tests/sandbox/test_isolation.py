# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""End-to-end: run A in project P1 cannot see run B's workspace or secrets.

Gated on Docker. Requires sagewai-sandbox-base:dev to be built.
"""
import os
from pathlib import Path

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.models import (
    SandboxConfig,
    SandboxImageVariant,
    SandboxMode,
    ToolCall,
)
from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool
from sagewai.sandbox.secret_provider import EnvSecretProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("SAGEWAI_SANDBOX_DOCKER_TESTS") != "1",
    reason="Docker isolation test requires SAGEWAI_SANDBOX_DOCKER_TESTS=1",
)


@pytest.mark.asyncio
async def test_cross_project_workspace_isolated(tmp_path: Path):
    from sagewai.sandbox.docker_backend import DockerBackend

    backend = DockerBackend()
    secrets = EnvSecretProvider(
        {
            "proj-a": {"A_SECRET": "alpha"},
            "proj-b": {"B_SECRET": "beta"},
        }
    )
    pool = LocalCacheSandboxPool(
        backend=backend,
        config=SandboxConfig(
            mode=SandboxMode.PER_RUN, default_image="ghcr.io/sagewai/sandbox-base:dev"
        ),
        worker_id="w-iso",
        scratch_root=tmp_path,
        sealed_secret_provider=secrets,
    )
    _image = "ghcr.io/sagewai/sandbox-base:dev"
    _digest = "sha256:" + "0" * 64  # placeholder; Docker backend resolves real digest
    _acquire_common = dict(
        execution_mode=ExecutionMode.SANDBOXED,
        image=_image,
        image_digest=_digest,
        image_variant=SandboxImageVariant.BASE,
    )
    try:
        # Run A writes a file and asserts its own secret is visible.
        async with pool.acquire(
            project_id="proj-a", run_id="ra",
            **_acquire_common,
        ) as sbx_a:
            r = await sbx_a.exec(
                ToolCall(
                    tool="bash",
                    args={"command": "echo $A_SECRET > /workspace/leaked.txt; echo $B_SECRET"},
                    call_id="c1",
                    timeout_s=10,
                )
            )
            assert r.ok
            # B_SECRET must not be visible inside project A's sandbox
            assert r.stdout.strip() == ""

        # Run B in a different project must not see run A's /workspace file.
        async with pool.acquire(
            project_id="proj-b", run_id="rb",
            **_acquire_common,
        ) as sbx_b:
            r = await sbx_b.exec(
                ToolCall(
                    tool="bash",
                    args={"command": "ls /workspace/leaked.txt 2>&1; echo ---; echo $A_SECRET; echo $B_SECRET"},
                    call_id="c2",
                    timeout_s=10,
                )
            )
            # No leaked.txt in project B's workdir
            assert "No such file" in r.stdout or "cannot access" in r.stdout
            # A_SECRET not visible in B
            assert "alpha" not in r.stdout
            assert "beta" in r.stdout
    finally:
        await pool.stop()
        await backend.close()
