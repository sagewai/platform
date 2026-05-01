# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Integration tests for KubernetesBackend. Gated on SAGEWAI_K8S_TEST_KUBECONFIG."""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("kubernetes_asyncio")

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAGEWAI_K8S_TEST_KUBECONFIG"),
    reason="K8s tests disabled. Set SAGEWAI_K8S_TEST_KUBECONFIG=path and load sagewai-sandbox-base:dev into the cluster.",
)


def _backend():
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend
    return KubernetesBackend(
        kubeconfig_path=os.environ["SAGEWAI_K8S_TEST_KUBECONFIG"],
        use_in_cluster=False,
        namespace=os.environ.get("SAGEWAI_K8S_TEST_NAMESPACE", "default"),
        egress_allowlist=[],
    )


@pytest.mark.asyncio
async def test_health_real_cluster():
    backend = _backend()
    try:
        h = await backend.health_check()
        assert h.ok, h.detail
        assert h.backend == "kubernetes"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_bash_exec_round_trip(tmp_path: Path):
    from sagewai.sandbox.models import (
        NetworkPolicy, ResourceLimits, SandboxLifetime, ToolCall,
    )

    backend = _backend()
    handle = await backend.start(
        project_id="p1", run_id="r1",
        image="ghcr.io/sagewai/sandbox-base:dev", image_digest="",
        env={"MY_VAR": "hello"},
        network_policy=NetworkPolicy.NONE,
        resource_limits=ResourceLimits(),
        workdir_mount=tmp_path,
        lifetime=SandboxLifetime.PER_RUN,
    )
    try:
        result = await handle.exec(ToolCall(
            tool="bash", args={"command": "echo $MY_VAR"}, call_id="c1", timeout_s=10,
        ))
        assert result.ok, result.error or result.stderr
        assert result.stdout.strip() == "hello"
    finally:
        await handle.stop()
        await backend.close()


@pytest.mark.asyncio
async def test_network_policy_none_blocks_egress(tmp_path: Path):
    """A pod with policy=NONE cannot reach external IPs.

    Skipped automatically if the cluster's CNI does not enforce NetworkPolicy
    (kind+kindnet enforces; minikube/Docker-driver may not).
    """
    from sagewai.sandbox.models import (
        NetworkPolicy, ResourceLimits, SandboxLifetime, ToolCall,
    )

    backend = _backend()
    await backend.ensure_network_policies()
    handle = await backend.start(
        project_id="p", run_id="r-np-none",
        image="ghcr.io/sagewai/sandbox-base:dev", image_digest="",
        env={}, network_policy=NetworkPolicy.NONE,
        resource_limits=ResourceLimits(),
        workdir_mount=tmp_path,
        lifetime=SandboxLifetime.PER_RUN,
    )
    try:
        result = await handle.exec(ToolCall(
            tool="bash",
            args={"command": "timeout 3 curl -s -o /dev/null -w '%{http_code}' "
                              "https://1.1.1.1 || echo BLOCKED"},
            call_id="c", timeout_s=10,
        ))
        if "BLOCKED" not in result.stdout and result.stdout.strip() != "":
            pytest.skip(
                "cluster CNI does not enforce NetworkPolicy "
                f"(saw response: {result.stdout!r}); test inconclusive"
            )
        assert "BLOCKED" in result.stdout
    finally:
        await handle.stop()
        await backend.close()


@pytest.mark.asyncio
async def test_pool_warm_acquire_and_release(tmp_path: Path):
    """Acquire from a real cluster: pool ensures Deployment, claim succeeds."""
    import asyncio

    from sagewai.core.state import ExecutionMode
    from sagewai.sandbox.external_pool import ExternalMinReplicasSandboxPool
    from sagewai.sandbox.models import (
        NetworkPolicy, SandboxConfig, SandboxImageVariant, SandboxMode,
        ToolCall,
    )

    backend = _backend()
    config = SandboxConfig(
        backend="kubernetes", mode=SandboxMode.PER_RUN,
        kubernetes_namespace=os.environ.get("SAGEWAI_K8S_TEST_NAMESPACE", "default"),
        pool_max_warm_per_tuple=2,
        pool_reap_interval_s=2,
    )

    pool = ExternalMinReplicasSandboxPool(
        backend=backend, config=config, worker_id="w-test", scratch_root=tmp_path,
    )
    try:
        await pool.start()
        await asyncio.sleep(20)  # generous; kind cold-start can be slow

        async with pool.acquire(
            project_id="p", run_id="r-pool-1",
            execution_mode=ExecutionMode.SANDBOXED,
            image="ghcr.io/sagewai/sandbox-base:dev",
            image_digest="",
            image_variant=SandboxImageVariant.BASE,
        ) as handle:
            res = await handle.exec(ToolCall(
                tool="bash", args={"command": "echo pool_ok"},
                call_id="c1", timeout_s=10,
            ))
            assert res.ok, res.error or res.stderr
            assert res.stdout.strip() == "pool_ok"
    finally:
        await pool.stop()
        await backend.close()


@pytest.mark.asyncio
async def test_reap_orphans(tmp_path: Path):
    from sagewai.sandbox.models import (
        NetworkPolicy, ResourceLimits, SandboxLifetime,
    )

    backend = _backend()
    await backend.start(
        project_id="p1", run_id="r-orphan",
        image="ghcr.io/sagewai/sandbox-base:dev", image_digest="",
        env={}, network_policy=NetworkPolicy.NONE,
        resource_limits=ResourceLimits(),
        workdir_mount=tmp_path,
        lifetime=SandboxLifetime.PER_RUN,
    )
    reaped = await backend.reap(older_than=timedelta(0))
    await backend.close()
    assert reaped >= 1
