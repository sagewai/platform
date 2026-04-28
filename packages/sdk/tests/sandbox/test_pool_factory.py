"""_build_pool dispatch on backend.pool_strategy."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("kubernetes_asyncio")


def test_build_pool_external_returns_external_pool(tmp_path: Path):
    from sagewai.core.worker import _build_pool
    from sagewai.sandbox.external_pool import ExternalMinReplicasSandboxPool
    from sagewai.sandbox.models import SandboxConfig
    from sagewai.sandbox.pool_protocol import PoolStrategy

    backend = MagicMock()
    backend.pool_strategy = PoolStrategy.EXTERNAL_MIN_REPLICAS
    backend.name = "kubernetes"

    pool = _build_pool(
        backend=backend, config=SandboxConfig(),
        worker_id="w1", scratch_root=tmp_path,
    )
    assert isinstance(pool, ExternalMinReplicasSandboxPool)
