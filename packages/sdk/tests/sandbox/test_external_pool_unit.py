"""Unit tests for ExternalMinReplicasSandboxPool. Uses fake k8s fixture."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("kubernetes_asyncio")

from tests.sandbox.conftest_k8s import fake_k8s  # noqa: F401  (fixture)


def _config(**overrides):
    from sagewai.sandbox.models import SandboxConfig, SandboxMode
    cfg = SandboxConfig(backend="kubernetes", mode=SandboxMode.PER_RUN)
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _backend(fake_k8s):
    """Construct a real KubernetesBackend wired to the fake api."""
    from sagewai.sandbox.kubernetes_backend import KubernetesBackend

    backend = KubernetesBackend(
        kubeconfig_path=None, use_in_cluster=False, namespace="sagewai", egress_allowlist=[],
    )
    backend._api_client = object()  # bypass make_api_client
    return backend


@pytest.mark.asyncio
async def test_pool_start_stop(fake_k8s, tmp_path: Path):
    from sagewai.sandbox.external_pool import ExternalMinReplicasSandboxPool

    pool = ExternalMinReplicasSandboxPool(
        backend=_backend(fake_k8s),
        config=_config(),
        worker_id="w1",
        scratch_root=tmp_path,
    )
    with patch("sagewai.sandbox.kubernetes_backend._CoreV1Api",
               return_value=fake_k8s.core_v1), \
         patch("sagewai.sandbox.kubernetes_backend._AppsV1Api",
               return_value=fake_k8s.apps_v1):
        await pool.start()
        assert pool._reconcile_task is not None
        await pool.stop()
        assert pool._reconcile_task is None


@pytest.mark.asyncio
async def test_acquire_warm_hit_relabels_and_returns_handle(fake_k8s, tmp_path):
    """Existing warm pod is claimed via CAS; release deletes it."""
    from sagewai.core.state import ExecutionMode
    from sagewai.sandbox.external_pool import ExternalMinReplicasSandboxPool
    from sagewai.sandbox.models import SandboxImageVariant

    pool = ExternalMinReplicasSandboxPool(
        backend=_backend(fake_k8s),
        config=_config(),
        worker_id="w1",
        scratch_root=tmp_path,
    )

    image = "ghcr.io/sagewai/sandbox-base:dev"
    digest = "sha256:" + "a" * 64

    with patch("sagewai.sandbox.kubernetes_backend._CoreV1Api",
               return_value=fake_k8s.core_v1), \
         patch("sagewai.sandbox.kubernetes_backend._AppsV1Api",
               return_value=fake_k8s.apps_v1):
        await pool.start()

        async with pool.acquire(
            project_id="p", run_id="r1",
            execution_mode=ExecutionMode.IDENTITY,
            image=image, image_digest=digest,
            image_variant=SandboxImageVariant.BASE,
        ) as handle:
            assert handle.sandbox_id.startswith("sagewai-pool-")
            pod = fake_k8s.pods[handle.sandbox_id]
            assert pod.labels["sagewai.phase"] == "leased"
            assert pod.labels["sagewai.run_id"] == "r1"

        # On release: pod deleted
        assert handle.sandbox_id not in fake_k8s.pods

        await pool.stop()


@pytest.mark.asyncio
async def test_cardinality_cap_bypasses_pool(fake_k8s, tmp_path, caplog):
    """Acquires beyond pool_max_distinct_keys bypass and create bare pods."""
    import logging
    from sagewai.core.state import ExecutionMode
    from sagewai.sandbox.external_pool import ExternalMinReplicasSandboxPool
    from sagewai.sandbox.models import SandboxImageVariant

    pool = ExternalMinReplicasSandboxPool(
        backend=_backend(fake_k8s),
        config=_config(pool_max_distinct_keys=1, pool_max_warm_per_tuple=1),
        worker_id="w1",
        scratch_root=tmp_path,
    )

    image = "ghcr.io/sagewai/sandbox-base:dev"

    async def _flip_ready(delay: float = 0.05) -> None:
        import asyncio
        await asyncio.sleep(delay)
        for pod in fake_k8s.pods.values():
            pod.phase = "Running"
            pod.ready = True

    with patch("sagewai.sandbox.kubernetes_backend._CoreV1Api",
               return_value=fake_k8s.core_v1), \
         patch("sagewai.sandbox.kubernetes_backend._AppsV1Api",
               return_value=fake_k8s.apps_v1), \
         caplog.at_level(logging.WARNING, logger="sagewai.sandbox.external_pool"):

        await pool.start()

        # Key 1 — uses pool
        async with pool.acquire(
            project_id="p", run_id="r1",
            execution_mode=ExecutionMode.IDENTITY,
            image=image, image_digest="sha256:" + "a" * 64,
            image_variant=SandboxImageVariant.BASE,
        ):
            pass

        # Key 2 (different digest) — over cap, bypasses pool → cold path
        # Need to flip the bare pod ready since the bypass calls backend.start()
        import asyncio
        flipper = asyncio.create_task(_flip_ready())
        async with pool.acquire(
            project_id="p", run_id="r2",
            execution_mode=ExecutionMode.IDENTITY,
            image=image, image_digest="sha256:" + "b" * 64,
            image_variant=SandboxImageVariant.GENERAL,
        ):
            pass
        await flipper

        await pool.stop()

    assert any("cardinality cap" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_stats_snapshot_returns_protocol_shape(fake_k8s, tmp_path):
    from sagewai.sandbox.external_pool import ExternalMinReplicasSandboxPool

    pool = ExternalMinReplicasSandboxPool(
        backend=_backend(fake_k8s), config=_config(),
        worker_id="w1", scratch_root=tmp_path,
    )
    snap = await pool.stats_snapshot()
    assert snap.worker_id == "w1"
    assert snap.aggregate.warm_count == 0
    assert snap.aggregate.active_count == 0


def test_advertised_labels_shape(fake_k8s, tmp_path):
    from sagewai.sandbox.external_pool import ExternalMinReplicasSandboxPool

    pool = ExternalMinReplicasSandboxPool(
        backend=_backend(fake_k8s), config=_config(),
        worker_id="w1", scratch_root=tmp_path,
    )
    labels = pool.advertised_labels()
    assert labels["sandbox.backend"] == "kubernetes"
    assert labels["sandbox.mode"] == "per_run"
    assert "sandbox.network_policy" in labels


@pytest.mark.asyncio
async def test_reconciler_orphan_reap(fake_k8s, tmp_path):
    """A leased pod whose run_id is not in _leases (after older-than grace) is reaped."""
    from sagewai.sandbox.external_pool import ExternalMinReplicasSandboxPool
    from tests.sandbox.conftest_k8s import FakePod

    pool = ExternalMinReplicasSandboxPool(
        backend=_backend(fake_k8s), config=_config(),
        worker_id="w1", scratch_root=tmp_path,
    )

    fake_k8s.pods["orphan"] = FakePod(
        name="orphan", namespace="sagewai",
        labels={"sagewai-pool": "abc", "sagewai.phase": "leased",
                "sagewai.run_id": "r-stale"},
        annotations={"sagewai.io/started-at":
                     (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()},
        phase="Running", ready=True,
    )

    with patch("sagewai.sandbox.kubernetes_backend._CoreV1Api",
               return_value=fake_k8s.core_v1):
        await pool._reap_orphans(grace=timedelta(minutes=10))

    assert "orphan" not in fake_k8s.pods


# Imports needed by the orphan-reap test
from datetime import datetime, timedelta, timezone
