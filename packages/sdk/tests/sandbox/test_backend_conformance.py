"""Backend conformance suite, parametrized over [DockerBackend, KubernetesBackend].

Each backend gates on its own env var. Tests skip individually when their
backend's gate is not set.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(params=["docker", "kubernetes"])
def backend(request):
    if request.param == "docker":
        if os.environ.get("SAGEWAI_SANDBOX_DOCKER_TESTS") != "1":
            pytest.skip("Docker tests disabled")
        from sagewai.sandbox.docker_backend import DockerBackend
        b = DockerBackend()
        yield b, "ghcr.io/sagewai/sandbox-base:dev"
    else:
        if not os.environ.get("SAGEWAI_K8S_TEST_KUBECONFIG"):
            pytest.skip("K8s tests disabled")
        from sagewai.sandbox.kubernetes_backend import KubernetesBackend
        b = KubernetesBackend(
            kubeconfig_path=os.environ["SAGEWAI_K8S_TEST_KUBECONFIG"],
            use_in_cluster=False,
            namespace=os.environ.get("SAGEWAI_K8S_TEST_NAMESPACE", "default"),
            egress_allowlist=[],
        )
        yield b, "ghcr.io/sagewai/sandbox-base:dev"


@pytest.mark.asyncio
async def test_health_check(backend):
    b, _ = backend
    try:
        h = await b.health_check()
        assert h.ok, h.detail
    finally:
        await b.close()


@pytest.mark.asyncio
async def test_bash_echo(backend, tmp_path: Path):
    from sagewai.sandbox.models import (
        NetworkPolicy, ResourceLimits, SandboxLifetime, ToolCall,
    )

    b, image = backend
    try:
        handle = await b.start(
            project_id="p", run_id="r",
            image=image, image_digest="",
            env={"GREETING": "world"},
            network_policy=NetworkPolicy.NONE,
            resource_limits=ResourceLimits(),
            workdir_mount=tmp_path,
            lifetime=SandboxLifetime.PER_RUN,
        )
        try:
            res = await handle.exec(ToolCall(
                tool="bash", args={"command": "echo $GREETING"},
                call_id="c", timeout_s=10,
            ))
            assert res.ok, res.error or res.stderr
            assert res.stdout.strip() == "world"
        finally:
            await handle.stop()
    finally:
        await b.close()


@pytest.mark.asyncio
async def test_set_env_clears_next_exec(backend, tmp_path: Path):
    from sagewai.sandbox.models import (
        NetworkPolicy, ResourceLimits, SandboxLifetime, ToolCall,
    )

    b, image = backend
    try:
        handle = await b.start(
            project_id="p", run_id="r",
            image=image, image_digest="",
            env={"TOKEN": "secret"},
            network_policy=NetworkPolicy.NONE,
            resource_limits=ResourceLimits(),
            workdir_mount=tmp_path,
            lifetime=SandboxLifetime.PER_RUN,
        )
        try:
            res1 = await handle.exec(ToolCall(
                tool="bash", args={"command": "echo $TOKEN"},
                call_id="c1", timeout_s=10,
            ))
            assert res1.stdout.strip() == "secret"

            await handle.set_env({})

            res2 = await handle.exec(ToolCall(
                tool="bash", args={"command": "echo \"[$TOKEN]\""},
                call_id="c2", timeout_s=10,
            ))
            assert res2.stdout.strip() == "[]"
        finally:
            await handle.stop()
    finally:
        await b.close()
