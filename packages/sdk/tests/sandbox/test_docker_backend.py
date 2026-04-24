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


@pytest.mark.asyncio
async def test_docker_backend_digest_match_accepted(tmp_path, monkeypatch):
    """Image listed in the manifest with a matching digest is accepted silently."""
    from sagewai.sandbox import image_manifest
    from sagewai.sandbox.docker_backend import DockerBackend

    backend = DockerBackend()
    try:
        # Look up the real digest of the locally-built :dev image.
        real_digest = await backend._inspect_image_digest("ghcr.io/sagewai/sandbox-base:dev")
        assert real_digest.startswith("sha256:")

        # Seed the manifest with that digest keyed under a test-only variant
        # and construct a ref that lookup_digest() will match.
        monkeypatch.setitem(image_manifest.PINNED_DIGESTS, "test-match", real_digest)
        await backend.verify_digest(
            image_ref="ghcr.io/sagewai/sandbox-test-match:any",
            actual_digest=real_digest,
        )
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_docker_backend_digest_mismatch_raises(monkeypatch):
    """Manifest entry + wrong digest raises SandboxError with 'digest mismatch'."""
    from sagewai.sandbox import image_manifest
    from sagewai.sandbox.docker_backend import DockerBackend, SandboxError

    monkeypatch.setitem(image_manifest.PINNED_DIGESTS, "zzz", "sha256:" + "0" * 64)
    backend = DockerBackend()
    try:
        with pytest.raises(SandboxError, match="digest mismatch"):
            await backend.verify_digest(
                image_ref="ghcr.io/sagewai/sandbox-zzz:1.0",
                actual_digest="sha256:" + "1" * 64,
            )
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_docker_backend_unknown_image_skip_with_info_log(caplog):
    """BYO image (unknown to manifest) skips verification with an INFO log."""
    import logging

    from sagewai.sandbox.docker_backend import DockerBackend

    backend = DockerBackend()
    try:
        with caplog.at_level(logging.INFO, logger="sagewai.sandbox.docker_backend"):
            await backend.verify_digest(
                image_ref="ghcr.io/acme/my-sandbox:1.0",
                actual_digest="sha256:" + "a" * 64,
            )
        assert any("unverified image" in r.message for r in caplog.records)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_docker_backend_probe_compatible_runner(tmp_path):
    """base:dev ships runner >=0.1,<0.2 — probe passes."""
    from sagewai.sandbox.docker_backend import DockerBackend
    from sagewai.sandbox.models import (
        NetworkPolicy,
        ResourceLimits,
        SandboxLifetime,
    )

    backend = DockerBackend()
    handle = await backend.start(
        project_id="p1",
        run_id="r-probe",
        image="ghcr.io/sagewai/sandbox-base:dev",
        image_digest="",
        env={},
        network_policy=NetworkPolicy.NONE,
        resource_limits=ResourceLimits(),
        workdir_mount=tmp_path,
        lifetime=SandboxLifetime.PER_RUN,
    )
    try:
        version = await backend.probe_runner(handle)
        assert version
        assert version.startswith("0.1")
    finally:
        await handle.stop()
        await backend.close()


@pytest.mark.asyncio
async def test_docker_backend_probe_incompatible_runner_raises(tmp_path, monkeypatch):
    """Incompatible spec (spoofed) → SandboxError mentioning tool-runner."""
    from sagewai.sandbox import image_manifest
    from sagewai.sandbox.docker_backend import DockerBackend, SandboxError
    from sagewai.sandbox.models import (
        NetworkPolicy,
        ResourceLimits,
        SandboxLifetime,
    )

    monkeypatch.setattr(image_manifest, "TOOL_RUNNER_VERSION_SPEC", ">=99.0")
    backend = DockerBackend()
    handle = await backend.start(
        project_id="p1",
        run_id="r-probe-fail",
        image="ghcr.io/sagewai/sandbox-base:dev",
        image_digest="",
        env={},
        network_policy=NetworkPolicy.NONE,
        resource_limits=ResourceLimits(),
        workdir_mount=tmp_path,
        lifetime=SandboxLifetime.PER_RUN,
    )
    try:
        with pytest.raises(SandboxError, match="tool-runner"):
            await backend.probe_runner(handle)
    finally:
        await handle.stop()
        await backend.close()
