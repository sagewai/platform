# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pull each published variant and do a full round-trip smoke test.

Gated on:
  - SAGEWAI_SANDBOX_DOCKER_TESTS=1 (Docker available)
  - SAGEWAI_SANDBOX_NETWORK_TESTS=1 (GHCR reachable, pulls OK)
"""
import os
from pathlib import Path

import pytest

from sagewai.sandbox.docker_backend import DockerBackend
from sagewai.sandbox.models import (
    NetworkPolicy,
    ResourceLimits,
    SandboxLifetime,
    ToolCall,
)

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("SAGEWAI_SANDBOX_DOCKER_TESTS") != "1",
        reason="Docker tests disabled",
    ),
    pytest.mark.skipif(
        os.environ.get("SAGEWAI_SANDBOX_NETWORK_TESTS") != "1",
        reason="Network tests disabled (pulls from GHCR)",
    ),
]


@pytest.mark.parametrize(
    "variant",
    ["base", "general", "ml", "ops", "erp", "ecommerce", "api"],
)
@pytest.mark.asyncio
async def test_published_variant_smoke(variant: str, tmp_path: Path):
    """Pull ghcr.io/sagewai/sandbox-<variant>:latest, probe runner, round-trip bash."""
    image = f"ghcr.io/sagewai/sandbox-{variant}:latest"
    backend = DockerBackend()
    try:
        digest = await backend._inspect_image_digest(image)
        assert digest.startswith("sha256:")
        await backend.verify_digest(image_ref=image, actual_digest=digest)

        handle = await backend.start(
            project_id="pub",
            run_id=f"r-{variant}",
            image=image,
            image_digest=digest,
            env={},
            network_policy=NetworkPolicy.NONE,
            resource_limits=ResourceLimits(),
            workdir_mount=tmp_path,
            lifetime=SandboxLifetime.PER_RUN,
        )
        try:
            version = await backend.probe_runner(handle)
            assert version

            r = await handle.exec(
                ToolCall(
                    tool="bash",
                    args={"command": "echo hello-from-" + variant},
                    call_id="c1",
                    timeout_s=10,
                )
            )
            assert r.ok
            assert r.stdout.strip() == f"hello-from-{variant}"
        finally:
            await handle.stop()
    finally:
        await backend.close()
