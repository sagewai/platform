# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Worker selects the right backend class from SandboxConfig.backend string."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_dispatch_resolves_kubernetes(monkeypatch):
    from sagewai.sandbox.models import SandboxConfig, SandboxMode
    from sagewai.core.worker import _select_backend

    cfg = SandboxConfig(backend="kubernetes", mode=SandboxMode.PER_RUN)

    class FakeKB:
        name = "kubernetes"

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    with patch("sagewai.sandbox.kubernetes_backend.KubernetesBackend", FakeKB):
        backend = _select_backend(
            cfg, mode=SandboxMode.PER_RUN, override=None,
            kubernetes_config={
                "kubeconfig_path": "/etc/kubeconfig",
                "use_in_cluster": False,
                "namespace": "sagewai",
                "egress_allowlist": [],
            },
        )
        assert isinstance(backend, FakeKB)
        assert backend.kwargs["kubeconfig_path"] == "/etc/kubeconfig"
        assert backend.kwargs["namespace"] == "sagewai"
        assert backend.name == "kubernetes"


def test_dispatch_resolves_docker():
    from sagewai.sandbox.models import SandboxConfig, SandboxMode
    from sagewai.core.worker import _select_backend

    cfg = SandboxConfig(backend="docker", mode=SandboxMode.PER_RUN)
    with patch("sagewai.sandbox.docker_backend.DockerBackend") as FakeDocker:
        FakeDocker.return_value.name = "docker"
        backend = _select_backend(
            cfg, mode=SandboxMode.PER_RUN, override=None, kubernetes_config=None,
        )
        FakeDocker.assert_called_once()


def test_dispatch_resolves_null_for_mode_none():
    from sagewai.sandbox.models import SandboxConfig, SandboxMode
    from sagewai.core.worker import _select_backend

    cfg = SandboxConfig(backend="docker", mode=SandboxMode.NONE)
    backend = _select_backend(
        cfg, mode=SandboxMode.NONE, override=None, kubernetes_config=None,
    )
    assert backend.name == "null"


def test_dispatch_unknown_backend_raises():
    from sagewai.sandbox.models import SandboxConfig, SandboxMode
    from sagewai.core.worker import _select_backend

    cfg = SandboxConfig(backend="banana", mode=SandboxMode.PER_RUN)
    with pytest.raises(ValueError, match="unknown sandbox backend"):
        _select_backend(
            cfg, mode=SandboxMode.PER_RUN, override=None, kubernetes_config=None,
        )


# ---------------------------------------------------------------------------
# Sealed identity-execution preview gate — worker backstop
# ---------------------------------------------------------------------------
#
# The robust choke point for runs created off the API path (replay,
# direct enqueue, future autopilot). When the preview gate denies an
# identity/full/full_jit run, ``_execute_workflow`` must fail the run and
# return WITHOUT ever invoking ``workflow.run``.


def _worker_with_recording_workflow():
    """Build a worker whose workflow records that it was reached.

    ``workflow.run`` returns a benign dict so that, when the gate *allows*
    the run, ``_execute_workflow`` proceeds to ``complete_run`` rather than
    failing. The store's ``fail_run`` / ``complete_run`` are awaitable mocks
    so we can assert which terminal path was taken.
    """
    from sagewai.core.worker import WorkflowWorker

    workflow = MagicMock()
    workflow.run = AsyncMock(return_value={"ok": True})

    store = MagicMock()
    store.fail_run = AsyncMock()
    store.complete_run = AsyncMock()

    worker = WorkflowWorker(
        store=store,
        workflow_registry={"wf": workflow},
        heartbeat_interval=3600,  # keep the heartbeat loop idle during the test
    )
    return worker, store, workflow


def _full_run():
    from sagewai.core.state import ExecutionMode, WorkflowRun

    run = WorkflowRun(
        workflow_name="wf",
        run_id="r-gate",
        execution_mode=ExecutionMode.FULL,
    )
    run._input = {}
    return run


@pytest.mark.asyncio
async def test_execute_workflow_blocks_identity_run_in_multi_without_optin(monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.delenv("SAGEWAI_SEALED_PREVIEW", raising=False)

    worker, store, workflow = _worker_with_recording_workflow()
    await worker._execute_workflow(_full_run())

    # The run was failed with the preview message; the workflow never ran.
    store.fail_run.assert_awaited_once()
    args = store.fail_run.await_args.args
    assert args[0] == "wf"
    assert args[1] == "r-gate"
    assert "preview-only" in args[2]
    assert "SAGEWAI_SEALED_PREVIEW" in args[2]
    workflow.run.assert_not_called()
    store.complete_run.assert_not_called()


@pytest.mark.asyncio
async def test_execute_workflow_allows_identity_run_in_single_org(monkeypatch):
    monkeypatch.delenv("SAGEWAI_TENANCY_MODE", raising=False)
    monkeypatch.delenv("SAGEWAI_SEALED_PREVIEW", raising=False)

    worker, store, workflow = _worker_with_recording_workflow()
    await worker._execute_workflow(_full_run())

    # Got past the gate: the workflow ran and the run completed normally.
    workflow.run.assert_awaited_once()
    store.complete_run.assert_awaited_once()
    store.fail_run.assert_not_called()


@pytest.mark.asyncio
async def test_execute_workflow_allows_identity_run_in_multi_with_optin(monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_SEALED_PREVIEW", "1")

    worker, store, workflow = _worker_with_recording_workflow()
    await worker._execute_workflow(_full_run())

    workflow.run.assert_awaited_once()
    store.complete_run.assert_awaited_once()
    store.fail_run.assert_not_called()


@pytest.mark.asyncio
async def test_execute_workflow_does_not_gate_sandboxed_run_in_multi(monkeypatch):
    """Non-identity modes (bare/sandboxed) are never preview-gated."""
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.delenv("SAGEWAI_SEALED_PREVIEW", raising=False)

    from sagewai.core.state import ExecutionMode, WorkflowRun

    worker, store, workflow = _worker_with_recording_workflow()
    run = WorkflowRun(
        workflow_name="wf",
        run_id="r-sbx",
        execution_mode=ExecutionMode.SANDBOXED,
    )
    run._input = {}

    await worker._execute_workflow(run)

    workflow.run.assert_awaited_once()
    store.complete_run.assert_awaited_once()
    store.fail_run.assert_not_called()
