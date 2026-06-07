# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.

"""Tests for host-backed execution policy guard at _select_backend (#10B)."""
from __future__ import annotations

import pytest

from sagewai.sandbox.models import BackendHealth, SandboxConfig, SandboxMode
from sagewai.core.worker import WorkflowWorker, _select_backend


class _UnhealthyDockerBackend:
    """Minimal backend whose health_check fails — drives the fallback path.

    Only ``name`` and ``health_check`` are reached: the guard raises before
    the pool is ever built, so the rest of the SandboxBackend protocol is
    intentionally unimplemented.
    """

    name = "docker"  # must be != "null" to hit the fallback swap branch

    async def health_check(self) -> BackendHealth:
        return BackendHealth(ok=False, backend="docker", detail="daemon unreachable")


def test_host_exec_refused_without_flag(monkeypatch):
    """Default-deny: no flag → execution blocked regardless of runtime."""
    monkeypatch.delenv("SAGEWAI_ALLOW_HOST_EXEC", raising=False)
    with pytest.raises(RuntimeError, match="Host-backed execution disabled"):
        _select_backend(SandboxConfig(), mode=SandboxMode.NONE, override=None, kubernetes_config=None)


def test_host_exec_allowed_with_flag(monkeypatch):
    """Opt-in: SAGEWAI_ALLOW_HOST_EXEC=1 enables NullBackend anywhere."""
    monkeypatch.setenv("SAGEWAI_ALLOW_HOST_EXEC", "1")
    b = _select_backend(SandboxConfig(), mode=SandboxMode.NONE, override=None, kubernetes_config=None)
    assert b.__class__.__name__ == "NullBackend"


def test_null_backend_name_refused_without_flag(monkeypatch):
    """The 'null' config.backend path is also guarded."""
    monkeypatch.delenv("SAGEWAI_ALLOW_HOST_EXEC", raising=False)
    cfg = SandboxConfig(backend="null")
    # mode must NOT be NONE so we hit the name == "null" branch
    with pytest.raises(RuntimeError, match="Host-backed execution disabled"):
        _select_backend(cfg, mode=SandboxMode.PER_RUN, override=None, kubernetes_config=None)


def test_null_backend_name_allowed_with_flag(monkeypatch):
    """The 'null' config.backend path is allowed when flag is set."""
    monkeypatch.setenv("SAGEWAI_ALLOW_HOST_EXEC", "1")
    cfg = SandboxConfig(backend="null")
    b = _select_backend(cfg, mode=SandboxMode.PER_RUN, override=None, kubernetes_config=None)
    assert b.__class__.__name__ == "NullBackend"


def test_guarded_null_backend_factory(monkeypatch):
    from sagewai.core.worker import _guarded_null_backend
    monkeypatch.delenv("SAGEWAI_ALLOW_HOST_EXEC", raising=False)
    with pytest.raises(RuntimeError, match="Host-backed execution disabled"):
        _guarded_null_backend()
    monkeypatch.setenv("SAGEWAI_ALLOW_HOST_EXEC", "1")
    assert _guarded_null_backend().__class__.__name__ == "NullBackend"


async def test_fallback_to_none_refused_without_flag(tmp_path, monkeypatch):
    """Fallback-to-NONE in a non-prod container must hit the guard, not silently
    spawn a NullBackend.

    Drives the real ``_start_sandbox_pool`` path: an unhealthy Docker backend in
    a non-production environment downgrades PER_TOOL → NONE via ``apply_fallback``,
    which swaps in a host-backed NullBackend. That swap must be guarded just like
    the two ``_select_backend`` sites. Without this test, reverting only the
    fallback line (worker.py) back to a bare ``NullBackend()`` would pass every
    other guard test.
    """
    monkeypatch.delenv("SAGEWAI_ALLOW_HOST_EXEC", raising=False)

    worker = WorkflowWorker(
        store=None,
        workflow_registry={},
        sandbox_backend=_UnhealthyDockerBackend(),
        # PER_TOOL downgrades exactly one step to NONE on fallback.
        sandbox_config=SandboxConfig(mode=SandboxMode.PER_TOOL),
        sandbox_scratch_root=tmp_path,
        project_environment="development",  # non-prod → downgrade (prod would raise earlier)
    )

    with pytest.raises(RuntimeError, match="Host-backed execution disabled"):
        await worker._start_sandbox_pool()
    assert worker._sandbox_pool is None  # never reached pool construction
