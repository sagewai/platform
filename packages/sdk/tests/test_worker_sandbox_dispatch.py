# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Worker selects the right backend class from SandboxConfig.backend string."""
from __future__ import annotations

from unittest.mock import patch

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
