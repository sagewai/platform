# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SandboxPool Protocol + shared dataclasses.

The Protocol is the contract every pool implementation satisfies.
Plan 1.5 ships LocalCacheSandboxPool (Docker, future Firecracker).
Threads 3 + 4 will add ExternalMinReplicasSandboxPool (K8s) and
ProviderManagedSandboxPool (Lambda) without touching the local-cache pool.
"""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sagewai.sandbox.pool_stats import PoolStatsSnapshot

if TYPE_CHECKING:
    from sagewai.core.state import ExecutionMode
    from sagewai.sandbox.models import NetworkPolicy, SandboxImageVariant, SandboxMode


class PoolStrategy(str, Enum):
    """Backend's expected pool implementation family."""

    LOCAL_CACHE = "local_cache"                      # Docker, Firecracker, gVisor
    EXTERNAL_MIN_REPLICAS = "external_min_replicas"  # Kubernetes Deployment
    PROVIDER_MANAGED = "provider_managed"            # Lambda provisioned concurrency


@dataclass(frozen=True, slots=True)
class PoolKey:
    """Partition key for the warm sandbox bench.

    Two pooled sandboxes are interchangeable iff their PoolKeys are equal.
    Partitioning by `execution_mode` is required because a Mode 3 sandbox
    accumulates state (CLI agent process, populated /workspace) that the
    Sealed-iii.A cleanup_run hook does not address.
    """

    image_digest: str
    sandbox_mode: SandboxMode
    execution_mode: ExecutionMode
    network_policy: NetworkPolicy
    image_variant: SandboxImageVariant


@dataclass(slots=True)
class BenchEntry:
    """A single warm sandbox sitting on the bench, ready for the next acquire."""

    handle: Any              # SandboxHandle (Protocol forward ref to avoid circular import)
    pooled_at: datetime      # set when entry enters the bench
    last_run_id: str | None  # the run that most recently used it (None if cold-warmed pre-emptively, future)


@dataclass(slots=True)
class LeasedHandle:
    """Pool's record of a sandbox currently in use by a run."""

    handle: Any      # SandboxHandle
    key: PoolKey
    run_id: str
    leased_at: datetime


@runtime_checkable
class SandboxPool(Protocol):
    """Pool contract. Implementations: LocalCacheSandboxPool (Plan 1.5),
    ExternalMinReplicasSandboxPool (Thread 3), ProviderManagedSandboxPool
    (Thread 4)."""

    strategy: PoolStrategy

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    def acquire(
        self,
        *,
        project_id: str,
        run_id: str,
        execution_mode: ExecutionMode,
        image: str,
        image_digest: str,
        image_variant: SandboxImageVariant,
        security_profile_ref: str | None = None,
        effective_env_keys: list[str] | None = None,
        effective_secret_keys: list[str] | None = None,
        workflow_name: str | None = None,
        # Sealed-iii.C: when present, the pool injects env from this
        # snapshot via SealedSecretProvider.replay_env_for instead of
        # re-resolving the cascade via env_for.
        replay_snapshot: object | None = None,
    ) -> AbstractAsyncContextManager: ...

    async def stats_snapshot(self) -> PoolStatsSnapshot: ...

    def advertised_labels(self) -> dict[str, str]: ...
