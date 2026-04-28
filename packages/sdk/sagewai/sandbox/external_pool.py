# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""ExternalMinReplicasSandboxPool — Deployment-with-min-replicas pool for K8s.

Plan SBX-K8S design spec, Section 6:
docs/superpowers/specs/2026-04-27-sandbox-k8s-backend-design.md
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.models import (
    SandboxConfig,
    SandboxImageVariant,
    SandboxLifetime,
    SandboxMode,
)
from sagewai.sandbox.pool_protocol import LeasedHandle, PoolKey, PoolStrategy
from sagewai.sandbox.pool_stats import PoolStatsRecord, PoolStatsSnapshot

logger = logging.getLogger(__name__)


class ExternalMinReplicasSandboxPool:
    """Pool that delegates warm-bench management to k8s Deployments."""

    strategy = PoolStrategy.EXTERNAL_MIN_REPLICAS

    def __init__(
        self,
        *,
        backend: Any,                                # KubernetesBackend
        config: SandboxConfig,
        worker_id: str,
        scratch_root: Path,
        sealed_secret_provider: Any | None = None,
        audit_writer: Any | None = None,
        otel_meter: Any | None = None,
    ) -> None:
        self._backend = backend
        self._config = config
        self._worker_id = worker_id
        self._scratch_root = scratch_root
        self._secret_provider = sealed_secret_provider
        self._audit = audit_writer
        self._meter = otel_meter

        self._deployments: dict[PoolKey, str] = {}
        self._leases: dict[str, LeasedHandle] = {}
        self._stats: dict[PoolKey, PoolStatsRecord] = {}
        self._lock = asyncio.Lock()
        self._reconcile_task: asyncio.Task | None = None

    @property
    def mode(self) -> SandboxMode:
        return self._config.mode or SandboxMode.NONE

    async def start(self) -> None:
        if self._reconcile_task is not None:
            return
        try:
            await self._backend.ensure_network_policies()
        except Exception:
            logger.exception("ensure_network_policies failed (continuing)")
        self._reconcile_task = asyncio.create_task(
            self._reconcile_loop(), name="external-pool-reconciler",
        )

    async def stop(self) -> None:
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            try:
                await self._reconcile_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reconcile_task = None
        async with self._lock:
            for lease in list(self._leases.values()):
                try:
                    await lease.handle.stop()
                except Exception:
                    pass
            self._leases.clear()
            if not self._config.pool_kubernetes_keep_deployments_on_stop:
                for name in list(self._deployments.values()):
                    try:
                        await self._backend.scale_deployment(name, replicas=0)
                    except Exception:
                        logger.debug("scale-to-zero on stop failed", exc_info=True)

    @asynccontextmanager
    async def acquire(
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
    ) -> AsyncIterator[Any]:
        import time

        key = PoolKey(
            image_digest=image_digest,
            sandbox_mode=self.mode,
            execution_mode=execution_mode,
            network_policy=self._config.network_policy,
            image_variant=image_variant,
        )
        env = await self._build_env(
            project_id=project_id, run_id=run_id,
            security_profile_ref=security_profile_ref,
            effective_env_keys=effective_env_keys,
            effective_secret_keys=effective_secret_keys,
            workflow_name=workflow_name,
        )

        async with self._lock:
            over_cap = (
                key not in self._deployments
                and len(self._deployments) >= self._config.pool_max_distinct_keys
            )
        if over_cap:
            logger.warning(
                "pool cardinality cap (%d) reached — bypassing pool for key=%s",
                self._config.pool_max_distinct_keys, key,
            )
            handle = await self._backend.start(
                project_id=project_id, run_id=run_id,
                image=image, image_digest=image_digest, env=env,
                network_policy=self._config.network_policy,
                resource_limits=self._config.resource_limits,
                workdir_mount=self._scratch_root / self._worker_id / "runs" / run_id,
                lifetime=SandboxLifetime.PER_RUN,
            )
            try:
                yield handle
            finally:
                try:
                    await handle.stop()
                except Exception:
                    pass
            return

        deployment_name = await self._backend.ensure_deployment(
            key=key,
            replicas=self._config.pool_max_warm_per_tuple,
            image=image,
            resource_limits=self._config.resource_limits,
            lifetime=SandboxLifetime.PER_RUN,
        )
        async with self._lock:
            self._deployments[key] = deployment_name

        t0 = time.monotonic()
        handle = None
        warm_pods = await self._backend.list_warm_pods(deployment_name)
        for candidate in warm_pods[:5]:
            handle = await self._backend.claim_pod(candidate, run_id=run_id)
            if handle is not None:
                await handle.set_env(env)
                break

        hit = handle is not None
        if handle is None:
            handle = await self._backend.start(
                project_id=project_id, run_id=run_id,
                image=image, image_digest=image_digest, env=env,
                network_policy=self._config.network_policy,
                resource_limits=self._config.resource_limits,
                workdir_mount=self._scratch_root / self._worker_id / "runs" / run_id,
                lifetime=SandboxLifetime.PER_RUN,
            )
            await self._record_warm(
                key=key, run_id=run_id, project_id=project_id,
                start_latency_ms=int((time.monotonic() - t0) * 1000),
            )

        latency_ms = int((time.monotonic() - t0) * 1000)
        async with self._lock:
            self._leases[run_id] = LeasedHandle(
                handle=handle, key=key, run_id=run_id,
                leased_at=datetime.now(timezone.utc),
            )
        await self._record_acquire(
            key=key, run_id=run_id, project_id=project_id,
            hit=hit, latency_ms=latency_ms,
        )

        try:
            yield handle
        finally:
            await self._release(run_id)

    async def _release(self, run_id: str) -> None:
        async with self._lock:
            lease = self._leases.pop(run_id, None)
        if lease is None:
            return

        # cleanup_run hook (Plan 1.5: in-memory env scrub)
        try:
            await lease.handle.set_env({})
        except Exception:
            logger.debug("set_env({}) cleanup failed", exc_info=True)

        # Always discard for K8s pool (orphan-on-claim model)
        try:
            await lease.handle.stop()
        except Exception:
            logger.debug("release stop failed", exc_info=True)

        lease_ms = int(
            (datetime.now(timezone.utc) - lease.leased_at).total_seconds() * 1000
        )
        await self._record_release(
            key=lease.key, run_id=run_id, project_id="",
            outcome="discarded", lease_duration_ms=lease_ms,
        )

    async def _build_env(self, **kwargs) -> dict[str, str]:
        if self._secret_provider is None:
            return {}
        try:
            return await self._secret_provider.env_for(
                project_id=kwargs["project_id"], run_id=kwargs["run_id"],
                agent_id=None, declared_scopes=[],
                security_profile_ref=kwargs.get("security_profile_ref"),
                effective_env_keys=kwargs.get("effective_env_keys"),
                effective_secret_keys=kwargs.get("effective_secret_keys"),
                sealed_levels=None,
            )
        except Exception:
            logger.debug("secret_provider.env_for failed", exc_info=True)
            return {}

    async def _reconcile_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._config.pool_reap_interval_s)
                try:
                    await self._reap_orphans(grace=timedelta(minutes=10))
                except Exception:
                    logger.exception("reap_orphans failed")
                try:
                    await self._correct_drift()
                except Exception:
                    logger.exception("correct_drift failed")
            except asyncio.CancelledError:
                return

    async def _reap_orphans(self, *, grace: timedelta) -> int:
        from sagewai.sandbox.kubernetes_backend import _CoreV1Api

        api = await self._backend._ensure_client()
        core_v1 = _CoreV1Api(api)
        result = await core_v1.list_namespaced_pod(
            namespace=self._backend._namespace,
            label_selector="sagewai-pool,sagewai.phase=leased",
        )
        cutoff = datetime.now(timezone.utc) - grace
        async with self._lock:
            active_run_ids = {l.run_id for l in self._leases.values()}

        killed = 0
        for pod in result.get("items", []):
            md = pod.get("metadata", {})
            run_id = (md.get("labels") or {}).get("sagewai.run_id")
            if run_id in active_run_ids:
                continue
            started_at_raw = (md.get("annotations") or {}).get(
                "sagewai.io/started-at", "",
            )
            try:
                started_at = datetime.fromisoformat(started_at_raw)
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if started_at > cutoff:
                continue
            try:
                await core_v1.delete_namespaced_pod(
                    name=md["name"], namespace=self._backend._namespace,
                    grace_period_seconds=5,
                )
                killed += 1
            except Exception:
                logger.debug("orphan reap delete failed", exc_info=True)
        return killed

    async def _correct_drift(self) -> None:
        async with self._lock:
            keys_and_names = list(self._deployments.items())
        for _, deployment_name in keys_and_names:
            try:
                await self._backend.scale_deployment(
                    deployment_name, replicas=self._config.pool_max_warm_per_tuple,
                )
            except Exception:
                logger.debug("drift correction scale failed", exc_info=True)

    async def stats_snapshot(self) -> PoolStatsSnapshot:
        from sagewai.sandbox.pool_stats import AggregateStats, PerTupleStats

        now = datetime.now(timezone.utc)
        per_tuple: list[PerTupleStats] = []
        warm_total = 0
        async with self._lock:
            keys_and_names = list(self._deployments.items())
        for key, deployment_name in keys_and_names:
            try:
                warm = await self._backend.list_warm_pods(deployment_name)
            except Exception:
                logger.debug("stats list_warm_pods failed", exc_info=True)
                warm = []
            warm_count = len(warm)
            warm_total += warm_count
            rec = self._stats.get(key, PoolStatsRecord())
            per_tuple.append(PerTupleStats(
                image_variant=key.image_variant.value,
                execution_mode=key.execution_mode.value,
                network_policy=key.network_policy.value,
                warm_count=warm_count,
                warm_max=self._config.pool_max_warm_per_tuple,
                active_count=rec.active_count,
                hit_rate_1h=rec.hit_rate_1h(now=now),
                last_evict_at=rec.last_evict_at,
                last_evict_reason=rec.last_evict_reason,
            ))

        agg_hits = sum(r.hits_total for r in self._stats.values())
        agg_misses = sum(r.misses_total for r in self._stats.values())
        agg_total = agg_hits + agg_misses
        agg_hit_rate = (agg_hits / agg_total) if agg_total else None

        return PoolStatsSnapshot(
            worker_id=self._worker_id,
            captured_at=now,
            per_tuple=per_tuple,
            aggregate=AggregateStats(
                warm_count=warm_total,
                warm_max_global=self._config.pool_max_warm_global,
                active_count=len(self._leases),
                hit_rate_1h=agg_hit_rate,
                last_evict_at=None,
            ),
        )

    def advertised_labels(self) -> dict[str, str]:
        return {
            "sandbox.mode": self.mode.value,
            "sandbox.backend": self._backend.name,
            "sandbox.network_policy": self._config.network_policy.value,
        }

    # Audit / stats helpers

    async def _record_warm(self, **kw) -> None:
        if self._audit is not None:
            try:
                await self._audit.emit(event_type="pool.warm", **{
                    k: v for k, v in kw.items() if k != "key"
                })
            except Exception:
                logger.debug("audit pool.warm failed", exc_info=True)

    async def _record_acquire(self, *, key, run_id, project_id, hit, latency_ms) -> None:
        rec = self._stats.setdefault(key, PoolStatsRecord())
        rec.record_acquire(hit=hit, now=datetime.now(timezone.utc))
        rec.active_count += 1
        if self._audit is not None:
            try:
                await self._audit.emit(
                    event_type="pool.acquire",
                    run_id=run_id, project_id=project_id,
                    details={"hit": hit, "acquire_latency_ms": latency_ms,
                             "execution_mode": key.execution_mode.value,
                             "image_variant": key.image_variant.value},
                )
            except Exception:
                logger.debug("audit pool.acquire failed", exc_info=True)

    async def _record_release(
        self, *, key, run_id, project_id, outcome, lease_duration_ms
    ) -> None:
        rec = self._stats.setdefault(key, PoolStatsRecord())
        rec.active_count = max(rec.active_count - 1, 0)
        if outcome == "discarded":
            rec.record_discard_after_cleanup()
        if self._audit is not None:
            try:
                await self._audit.emit(
                    event_type="pool.release",
                    run_id=run_id, project_id=project_id,
                    details={"outcome": outcome,
                             "lease_duration_ms": lease_duration_ms},
                )
            except Exception:
                logger.debug("audit pool.release failed", exc_info=True)
