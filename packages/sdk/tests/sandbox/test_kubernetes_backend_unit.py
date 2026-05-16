# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for KubernetesBackend (no real cluster). Use the fake k8s fixture."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("kubernetes_asyncio")

from tests.sandbox.conftest_k8s import fake_k8s  # noqa: F401  (fixture)


@pytest.mark.asyncio
async def test_init_constructs():
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend

    with patch("sagewai.sandbox.kubernetes_backend.make_api_client", new=AsyncMock(return_value=object())):
        backend = KubernetesBackend(
            kubeconfig_path=None,
            use_in_cluster=False,
            namespace="sagewai",
            egress_allowlist=[],
        )
        assert backend.name == "kubernetes"
        from sagewai.sandbox.pool_protocol import PoolStrategy
        assert backend.pool_strategy == PoolStrategy.EXTERNAL_MIN_REPLICAS


@pytest.mark.asyncio
async def test_health_check_ok():
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend

    fake_version_api = AsyncMock()
    fake_version_api.get_code = AsyncMock(return_value=type("V", (), {"git_version": "v1.30.0"})())

    with patch("sagewai.sandbox.kubernetes_backend.make_api_client", new=AsyncMock(return_value=object())), \
         patch("sagewai.sandbox.kubernetes_backend._VersionApi", return_value=fake_version_api):
        backend = KubernetesBackend(
            kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
        )
        await backend._ensure_client()
        health = await backend.health_check()
        assert health.ok is True
        assert health.backend == "kubernetes"
        assert "v1.30.0" in health.detail


@pytest.mark.asyncio
async def test_health_check_failure():
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend

    fake_version_api = AsyncMock()
    fake_version_api.get_code = AsyncMock(side_effect=RuntimeError("connection refused"))

    with patch("sagewai.sandbox.kubernetes_backend.make_api_client", new=AsyncMock(return_value=object())), \
         patch("sagewai.sandbox.kubernetes_backend._VersionApi", return_value=fake_version_api):
        backend = KubernetesBackend(
            kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
        )
        await backend._ensure_client()
        health = await backend.health_check()
        assert health.ok is False
        assert "connection refused" in health.detail


@pytest.mark.asyncio
async def test_start_creates_pod_and_polls_ready(fake_k8s):
    """start() creates the pod, polls until Running+ready, returns a handle."""
    import asyncio
    from pathlib import Path

    from sagewai.sandbox.kubernetes_backend import KubernetesBackend
    from sagewai.sandbox.models import (
        NetworkPolicy, ResourceLimits, SandboxLifetime,
    )

    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )

    async def _flip_ready_after(delay: float) -> None:
        await asyncio.sleep(delay)
        for pod in fake_k8s.pods.values():
            pod.phase = "Running"
            pod.ready = True

    with patch("sagewai.sandbox.kubernetes_backend.make_api_client",
               new=AsyncMock(return_value=object())), \
         patch("sagewai.sandbox.kubernetes_backend._CoreV1Api",
               return_value=fake_k8s.core_v1):
        flipper = asyncio.create_task(_flip_ready_after(0.05))
        handle = await backend.start(
            project_id="p1", run_id="r1",
            image="ghcr.io/sagewai/sandbox-base:dev", image_digest="sha256:" + "a" * 64,
            env={}, network_policy=NetworkPolicy.NONE,
            resource_limits=ResourceLimits(),
            workdir_mount=Path("/tmp/ignored"),
            lifetime=SandboxLifetime.PER_RUN,
        )
        await flipper
        assert handle.sandbox_id.startswith("sgw-")
        assert handle.image == "ghcr.io/sagewai/sandbox-base:dev"
        assert handle.image_digest == "sha256:" + "a" * 64
        assert handle.sandbox_id in fake_k8s.pods


@pytest.mark.asyncio
async def test_start_timeout_raises_and_cleans_up(fake_k8s):
    """Pod that never becomes ready → SandboxError + best-effort delete."""
    from pathlib import Path

    from sagewai.sandbox.docker_backend import SandboxError
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend
    from sagewai.sandbox.models import (
        NetworkPolicy, ResourceLimits, SandboxLifetime,
    )

    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )

    with patch("sagewai.sandbox.kubernetes_backend.make_api_client",
               new=AsyncMock(return_value=object())), \
         patch("sagewai.sandbox.kubernetes_backend._CoreV1Api",
               return_value=fake_k8s.core_v1), \
         patch("sagewai.sandbox.kubernetes_backend._POD_READY_TIMEOUT_S", 0.1):
        with pytest.raises(SandboxError, match="failed to become ready"):
            await backend.start(
                project_id="p1", run_id="r1",
                image="img", image_digest="",
                env={}, network_policy=NetworkPolicy.NONE,
                resource_limits=ResourceLimits(),
                workdir_mount=Path("/tmp/ignored"),
                lifetime=SandboxLifetime.PER_RUN,
            )
        # Pod should have been deleted on cleanup
        assert len(fake_k8s.pods) == 0


@pytest.mark.asyncio
async def test_exec_passes_env_prefix_and_parses_response(fake_k8s):
    """exec builds command=['env','K=V',...,'sagewai-tool-runner'] and parses JSON-RPC."""
    import json
    from sagewai.sandbox.kubernetes_backend import KubernetesSandboxHandle
    from sagewai.sandbox.models import ToolCall

    captured: dict[str, Any] = {}

    async def fake_ws_exec(*, name, namespace, command, stdin, stdout, stderr, tty):
        captured["command"] = list(command)

        class FakeWs:
            async def write_stdin(self, data: bytes) -> None:
                captured["stdin"] = data

            async def read_stdout(self, timeout: float = 0) -> str:
                return json.dumps({
                    "jsonrpc": "2.0", "id": 1,
                    "result": {"ok": True, "exit_code": 0, "stdout": "hello\n", "stderr": ""},
                }) + "\n"

            async def close(self) -> None: ...

            @property
            def returncode(self) -> int: return 0

        return FakeWs()

    handle = KubernetesSandboxHandle(
        api_client=object(), namespace="sagewai", pod_name="sgw-x",
        image="img", image_digest="", sandbox_id="sgw-x",
    )
    await handle.set_env({"FOO": "bar", "BAZ": "qux"})

    with patch("sagewai.sandbox.kubernetes_backend._ws_exec", new=fake_ws_exec):
        result = await handle.exec(ToolCall(
            tool="bash", args={"command": "echo hello"}, call_id="c1", timeout_s=5,
        ))

    assert result.ok is True
    assert result.stdout == "hello\n"
    cmd = captured["command"]
    assert cmd[0] == "env"
    # The order of FOO=bar / BAZ=qux is sorted for determinism
    assert "FOO=bar" in cmd
    assert "BAZ=qux" in cmd
    assert cmd[-1] == "sagewai-tool-runner"


@pytest.mark.asyncio
async def test_handle_stop_deletes_pod(fake_k8s):
    from sagewai.sandbox.kubernetes_backend import KubernetesSandboxHandle
    from tests.sandbox.conftest_k8s import FakePod

    fake_k8s.pods["sgw-x"] = FakePod(name="sgw-x", namespace="sagewai")

    handle = KubernetesSandboxHandle(
        api_client=object(), namespace="sagewai", pod_name="sgw-x",
        image="img", image_digest="", sandbox_id="sgw-x",
    )
    with patch("sagewai.sandbox.kubernetes_backend._CoreV1Api",
               return_value=fake_k8s.core_v1):
        await handle.stop()
    assert "sgw-x" not in fake_k8s.pods


@pytest.mark.asyncio
async def test_handle_stats_returns_zero_on_no_metrics():
    from sagewai.sandbox.kubernetes_backend import KubernetesSandboxHandle

    handle = KubernetesSandboxHandle(
        api_client=object(), namespace="sagewai", pod_name="sgw-x",
        image="img", image_digest="", sandbox_id="sgw-x",
    )
    stats = await handle.stats()
    assert stats.cpu_percent == 0.0
    assert stats.mem_bytes == 0


@pytest.mark.asyncio
async def test_handle_copy_in_out_raises_not_implemented():
    from pathlib import Path, PurePosixPath

    from sagewai.sandbox.kubernetes_backend import KubernetesSandboxHandle

    handle = KubernetesSandboxHandle(
        api_client=object(), namespace="sagewai", pod_name="sgw-x",
        image="img", image_digest="", sandbox_id="sgw-x",
    )
    with pytest.raises(NotImplementedError, match="Plan 2"):
        await handle.copy_in(Path("/tmp/x"), PurePosixPath("/workspace/x"))
    with pytest.raises(NotImplementedError, match="Plan 2"):
        await handle.copy_out(PurePosixPath("/workspace/x"), Path("/tmp/x"))


@pytest.mark.asyncio
async def test_reap_deletes_old_pods(fake_k8s):
    from datetime import datetime, timedelta, timezone

    from sagewai.sandbox.kubernetes_backend import KubernetesBackend
    from tests.sandbox.conftest_k8s import FakePod

    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )

    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    fake_k8s.pods["sgw-old"] = FakePod(
        name="sgw-old", namespace="sagewai",
        labels={"sagewai.sandbox_id": "sgw-old"},
        annotations={"sagewai.io/started-at": old},
    )
    fake_k8s.pods["sgw-new"] = FakePod(
        name="sgw-new", namespace="sagewai",
        labels={"sagewai.sandbox_id": "sgw-new"},
        annotations={"sagewai.io/started-at": new},
    )

    with patch("sagewai.sandbox.kubernetes_backend.make_api_client",
               new=AsyncMock(return_value=object())), \
         patch("sagewai.sandbox.kubernetes_backend._CoreV1Api",
               return_value=fake_k8s.core_v1):
        await backend._ensure_client()
        killed = await backend.reap(older_than=timedelta(hours=1))

    assert killed == 1
    assert "sgw-old" not in fake_k8s.pods
    assert "sgw-new" in fake_k8s.pods


@pytest.mark.asyncio
async def test_probe_runner_returns_version():
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend, KubernetesSandboxHandle

    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )
    handle = KubernetesSandboxHandle(
        api_client=object(), namespace="sagewai", pod_name="sgw-x",
        image="img", image_digest="", sandbox_id="sgw-x",
    )

    async def fake_ws(**kw):
        class FakeWs:
            async def read_stdout(self, timeout: float = 0) -> str: return "0.1.5\n"
            async def close(self) -> None: ...
            async def write_stdin(self, _data: bytes) -> None: ...
        assert kw["command"] == ["sagewai-tool-runner", "--version"]
        return FakeWs()

    with patch("sagewai.sandbox.kubernetes_backend._ws_exec", new=fake_ws):
        version = await backend.probe_runner(handle)
    assert version == "0.1.5"


@pytest.mark.asyncio
async def test_verify_digest_known_match_passes():
    from sagewai.sandbox import image_manifest
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend

    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )
    digest = "sha256:" + "c" * 64
    with patch.dict(image_manifest.PINNED_DIGESTS, {"k8s-test": digest}):
        await backend.verify_digest(
            image_ref="ghcr.io/sagewai/sandbox-k8s-test:1.0",
            actual_digest=digest,
        )


@pytest.mark.asyncio
async def test_verify_digest_known_mismatch_raises():
    from sagewai.sandbox import image_manifest
    from sagewai.sandbox.docker_backend import SandboxError
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend

    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )
    with patch.dict(image_manifest.PINNED_DIGESTS, {"zzz": "sha256:" + "0" * 64}):
        with pytest.raises(SandboxError, match="digest mismatch"):
            await backend.verify_digest(
                image_ref="ghcr.io/sagewai/sandbox-zzz:1.0",
                actual_digest="sha256:" + "1" * 64,
            )


@pytest.mark.asyncio
async def test_ensure_deployment_creates_then_idempotent(fake_k8s):
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend
    from sagewai.sandbox.models import (
        NetworkPolicy, ResourceLimits, SandboxImageVariant, SandboxLifetime, SandboxMode,
    )
    from sagewai.core.state import ExecutionMode
    from sagewai.sandbox.pool_protocol import PoolKey

    key = PoolKey(
        image_digest="sha256:" + "a" * 64,
        sandbox_mode=SandboxMode.PER_RUN,
        execution_mode=ExecutionMode.IDENTITY,
        network_policy=NetworkPolicy.NONE,
        image_variant=SandboxImageVariant.BASE,
    )

    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )

    with patch("sagewai.sandbox.kubernetes_backend.make_api_client",
               new=AsyncMock(return_value=object())), \
         patch("sagewai.sandbox.kubernetes_backend._AppsV1Api",
               return_value=fake_k8s.apps_v1):
        name1 = await backend.ensure_deployment(
            key=key, replicas=4,
            image="ghcr.io/sagewai/sandbox-base:dev",
            resource_limits=ResourceLimits(),
            lifetime=SandboxLifetime.PER_RUN,
        )
        name2 = await backend.ensure_deployment(
            key=key, replicas=4,
            image="ghcr.io/sagewai/sandbox-base:dev",
            resource_limits=ResourceLimits(),
            lifetime=SandboxLifetime.PER_RUN,
        )
    assert name1 == name2
    assert name1 in fake_k8s.deployments


@pytest.mark.asyncio
async def test_scale_deployment_updates_replicas(fake_k8s):
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend
    from tests.sandbox.conftest_k8s import FakeDeployment

    fake_k8s.deployments["sagewai-pool-deadbeef"] = FakeDeployment(
        name="sagewai-pool-deadbeef", namespace="sagewai",
        selector={"sagewai-pool": "deadbeef"},
        replicas=4, template_labels={"sagewai-pool": "deadbeef"},
    )
    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )
    with patch("sagewai.sandbox.kubernetes_backend.make_api_client",
               new=AsyncMock(return_value=object())), \
         patch("sagewai.sandbox.kubernetes_backend._AppsV1Api",
               return_value=fake_k8s.apps_v1):
        await backend.scale_deployment("sagewai-pool-deadbeef", replicas=2)
    assert fake_k8s.deployments["sagewai-pool-deadbeef"].replicas == 2


@pytest.mark.asyncio
async def test_list_warm_pods_filters_by_pool_and_phase(fake_k8s):
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend
    from tests.sandbox.conftest_k8s import FakePod

    fake_k8s.pods["p1"] = FakePod(
        name="p1", namespace="sagewai",
        labels={"sagewai-pool": "abc", "sagewai.phase": "warm"},
        phase="Running", ready=True,
    )
    fake_k8s.pods["p2"] = FakePod(
        name="p2", namespace="sagewai",
        labels={"sagewai-pool": "abc", "sagewai.phase": "warm",
                "sagewai.run_id": "r1"},  # already leased
        phase="Running", ready=True,
    )
    fake_k8s.pods["p3"] = FakePod(
        name="p3", namespace="sagewai",
        labels={"sagewai-pool": "abc", "sagewai.phase": "warm"},
        phase="Pending", ready=False,
    )

    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )
    with patch("sagewai.sandbox.kubernetes_backend.make_api_client",
               new=AsyncMock(return_value=object())), \
         patch("sagewai.sandbox.kubernetes_backend._CoreV1Api",
               return_value=fake_k8s.core_v1):
        pods = await backend.list_warm_pods("sagewai-pool-abc")
    assert [p["name"] for p in pods] == ["p1"]


@pytest.mark.asyncio
async def test_claim_pod_relabels_and_returns_handle(fake_k8s):
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend
    from tests.sandbox.conftest_k8s import FakePod

    fake_k8s.pods["p1"] = FakePod(
        name="p1", namespace="sagewai",
        labels={"sagewai-pool": "abc", "sagewai.phase": "warm"},
        phase="Running", ready=True,
        resource_version="rv-1",
    )
    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )
    pod = {
        "name": "p1",
        "resource_version": "rv-1",
        "image": "ghcr.io/sagewai/sandbox-base:dev",
        "image_digest": "sha256:" + "a" * 64,
    }
    with patch("sagewai.sandbox.kubernetes_backend.make_api_client",
               new=AsyncMock(return_value=object())), \
         patch("sagewai.sandbox.kubernetes_backend._CoreV1Api",
               return_value=fake_k8s.core_v1):
        handle = await backend.claim_pod(pod, run_id="r-claim")

    assert handle is not None
    assert handle.sandbox_id == "p1"
    assert fake_k8s.pods["p1"].labels["sagewai.phase"] == "leased"
    assert fake_k8s.pods["p1"].labels["sagewai.run_id"] == "r-claim"


@pytest.mark.asyncio
async def test_claim_pod_returns_none_on_409(fake_k8s):
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend
    from tests.sandbox.conftest_k8s import FakePod

    fake_k8s.pods["p1"] = FakePod(
        name="p1", namespace="sagewai",
        labels={"sagewai-pool": "abc", "sagewai.phase": "warm"},
        phase="Running", ready=True,
        resource_version="rv-current",
    )
    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )
    pod = {
        "name": "p1",
        "resource_version": "rv-stale",
        "image": "img", "image_digest": "",
    }
    with patch("sagewai.sandbox.kubernetes_backend.make_api_client",
               new=AsyncMock(return_value=object())), \
         patch("sagewai.sandbox.kubernetes_backend._CoreV1Api",
               return_value=fake_k8s.core_v1):
        handle = await backend.claim_pod(pod, run_id="r-loser")
    assert handle is None
    assert fake_k8s.pods["p1"].labels["sagewai.phase"] == "warm"


@pytest.mark.asyncio
async def test_ensure_network_policies_creates_three(fake_k8s):
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend

    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai",
        egress_allowlist=["10.0.0.0/8"],
    )
    with patch("sagewai.sandbox.kubernetes_backend.make_api_client",
               new=AsyncMock(return_value=object())), \
         patch("sagewai.sandbox.kubernetes_backend._NetworkingV1Api",
               return_value=fake_k8s.networking_v1):
        await backend.ensure_network_policies()
    assert {"sagewai-netpol-none", "sagewai-netpol-egress-allowlist",
            "sagewai-netpol-full"}.issubset(fake_k8s.network_policies.keys())


@pytest.mark.asyncio
async def test_ensure_network_policies_403_warns_and_continues(fake_k8s, caplog):
    """RBAC 403 logs WARN but does not raise."""
    import logging

    from sagewai.sandbox.kubernetes_backend import KubernetesBackend
    from tests.sandbox.conftest_k8s import FakeApiException

    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )

    class Forbidden:
        async def list_namespaced_network_policy(self, **kw):
            raise FakeApiException(403, "Forbidden", "RBAC denied")
        async def create_namespaced_network_policy(self, **kw):
            raise FakeApiException(403, "Forbidden")
        async def replace_namespaced_network_policy(self, **kw):
            raise FakeApiException(403, "Forbidden")

    with patch("sagewai.sandbox.kubernetes_backend.make_api_client",
               new=AsyncMock(return_value=object())), \
         patch("sagewai.sandbox.kubernetes_backend._NetworkingV1Api",
               return_value=Forbidden()), \
         caplog.at_level(logging.WARNING, logger="sagewai.sandbox.kubernetes_backend"):
        await backend.ensure_network_policies()  # must not raise

    assert any("403" in r.message or "Forbidden" in r.message for r in caplog.records)
