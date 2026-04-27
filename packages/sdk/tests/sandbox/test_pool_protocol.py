# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""SandboxBackend.pool_strategy contract."""
from __future__ import annotations

from datetime import datetime, timezone

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.backend import SandboxBackend
from sagewai.sandbox.models import NetworkPolicy, SandboxImageVariant, SandboxMode
from sagewai.sandbox.pool_protocol import BenchEntry, LeasedHandle, PoolKey, PoolStrategy


def test_docker_backend_declares_local_cache_strategy() -> None:
    from sagewai.sandbox.docker_backend import DockerBackend

    assert DockerBackend.pool_strategy == PoolStrategy.LOCAL_CACHE


def test_null_backend_declares_local_cache_strategy() -> None:
    from sagewai.sandbox.null_backend import NullBackend

    assert NullBackend.pool_strategy == PoolStrategy.LOCAL_CACHE


def test_pool_strategy_is_protocol_attribute() -> None:
    """The Protocol declares the attribute; runtime backends must set it.

    `__protocol_attrs__` only exists on Python 3.12+, but `__annotations__`
    is documented and stable since 3.0 and contains class-level type
    annotations, which is exactly what we want to assert here. If the
    `pool_strategy: PoolStrategy` annotation is removed from the Protocol
    body, this assertion fails — the same regression coverage as the
    3.12-only check, but portable to 3.10/3.11.
    """
    assert "pool_strategy" in SandboxBackend.__annotations__


class _FakeHandle:
    sandbox_id = "fake-1"
    image = "x"
    image_digest = "sha256:x"
    mode = SandboxMode.PER_RUN


def test_bench_entry_carries_handle_and_metadata() -> None:
    handle = _FakeHandle()
    now = datetime.now(timezone.utc)
    entry = BenchEntry(handle=handle, pooled_at=now, last_run_id="r-1")
    assert entry.handle is handle
    assert entry.pooled_at == now
    assert entry.last_run_id == "r-1"


def test_leased_handle_carries_key_run_and_lease_time() -> None:
    handle = _FakeHandle()
    key = PoolKey(
        image_digest="sha256:x",
        sandbox_mode=SandboxMode.PER_RUN,
        execution_mode=ExecutionMode.SANDBOXED,
        network_policy=NetworkPolicy.NONE,
        image_variant=SandboxImageVariant.BASE,
    )
    now = datetime.now(timezone.utc)
    lease = LeasedHandle(handle=handle, key=key, run_id="r-1", leased_at=now)
    assert lease.handle is handle
    assert lease.key is key
    assert lease.run_id == "r-1"
    assert lease.leased_at == now


def test_pool_protocol_runtime_checkable() -> None:
    """Backends can be type-checked at runtime."""
    from sagewai.sandbox.pool_protocol import SandboxPool
    # Plain object is NOT a SandboxPool; the Protocol checks attr presence.
    assert not isinstance(object(), SandboxPool)
