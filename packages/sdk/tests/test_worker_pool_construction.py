# Copyright 2026 Ali Arda Diri, Berlin, Germany
# (Licensed under AGPL-3.0-or-later — see LICENSE)
"""Worker constructs LocalCacheSandboxPool via backend.pool_strategy."""
from __future__ import annotations

import pytest


def test_build_pool_returns_local_cache_pool_for_local_cache_strategy(tmp_path):
    """The factory returns a LocalCacheSandboxPool for backends that declare LOCAL_CACHE."""
    from sagewai.core.worker import _build_pool
    from sagewai.sandbox.null_backend import NullBackend
    from sagewai.sandbox.models import SandboxConfig, SandboxMode
    from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool

    pool = _build_pool(
        backend=NullBackend(),
        config=SandboxConfig(mode=SandboxMode.NONE),
        worker_id="test-worker",
        scratch_root=tmp_path,
        sealed_secret_provider=None,
        audit_writer=None,
    )
    assert isinstance(pool, LocalCacheSandboxPool)


def test_build_pool_raises_for_unsupported_strategy(tmp_path):
    """Unknown pool strategies (Lambda PROVIDER_MANAGED — Thread 4) aren't built yet."""
    from sagewai.core.worker import _build_pool
    from sagewai.sandbox.null_backend import NullBackend
    from sagewai.sandbox.models import SandboxConfig, SandboxMode
    from sagewai.sandbox.pool_protocol import PoolStrategy

    backend = NullBackend()
    backend.pool_strategy = PoolStrategy.PROVIDER_MANAGED  # type: ignore[misc]

    with pytest.raises(NotImplementedError):
        _build_pool(
            backend=backend,
            config=SandboxConfig(mode=SandboxMode.NONE),
            worker_id="test-worker",
            scratch_root=tmp_path,
            sealed_secret_provider=None,
            audit_writer=None,
        )
