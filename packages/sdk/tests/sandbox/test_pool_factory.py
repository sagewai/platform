# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
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
