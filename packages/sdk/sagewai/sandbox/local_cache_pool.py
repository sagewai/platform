# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LocalCacheSandboxPool — Docker-friendly warm-bench pool (Plan 1.5)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.backend import SandboxBackend, SandboxHandle
from sagewai.sandbox.models import (
    SandboxConfig,
    SandboxImageVariant,
    SandboxLifetime,
    SandboxMode,
)
from sagewai.sandbox.pool_protocol import (
    BenchEntry,
    LeasedHandle,
    PoolKey,
    PoolStrategy,
)
from sagewai.sandbox.pool_stats import PoolStatsRecord, PoolStatsSnapshot

logger = logging.getLogger(__name__)


class LocalCacheSandboxPool:
    """Pool that maintains its own warm bench keyed by `PoolKey`.

    Plan 1.5 ships only this implementation. Future Firecracker/gVisor
    backends share this class; K8s + Lambda get their own pool classes.
    """

    strategy = PoolStrategy.LOCAL_CACHE

    def __init__(
        self,
        *,
        backend: SandboxBackend,
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

        self._benches: dict[PoolKey, deque[BenchEntry]] = {}
        self._leases: dict[str, LeasedHandle] = {}
        self._global_warm_count: int = 0
        self._stats: dict[PoolKey, PoolStatsRecord] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task | None = None
        self._probed_digests: set[str] = set()

    @property
    def mode(self) -> SandboxMode:
        return self._config.mode or SandboxMode.NONE

    async def start(self) -> None:
        if self._reaper_task is not None:
            return
        self._reaper_task = asyncio.create_task(self._reap_loop(), name="sandbox-pool-reaper")

    async def stop(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reaper_task = None
        async with self._lock:
            for lease in list(self._leases.values()):
                try:
                    await lease.handle.stop()
                except Exception:
                    pass
            self._leases.clear()
            for bench in self._benches.values():
                while bench:
                    entry = bench.popleft()
                    try:
                        await entry.handle.stop()
                    except Exception:
                        pass
            self._benches.clear()
            self._global_warm_count = 0

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
        acl: dict[str, list[str]] | None = None,
        replay_snapshot: object | None = None,
    ) -> AsyncIterator[SandboxHandle]:
        key = PoolKey(
            image_digest=image_digest,
            sandbox_mode=self.mode,
            execution_mode=execution_mode,
            network_policy=self._config.network_policy,
            image_variant=image_variant,
        )
        env = await self._build_env(
            project_id=project_id,
            run_id=run_id,
            security_profile_ref=security_profile_ref,
            effective_env_keys=effective_env_keys,
            effective_secret_keys=effective_secret_keys,
            workflow_name=workflow_name,
            replay_snapshot=replay_snapshot,
        )
        workdir = self._scratch_root / self._worker_id / "runs" / run_id
        workdir.mkdir(parents=True, exist_ok=True)

        t0 = time.monotonic()

        # Bench-first path: warm hit reuses a pooled sandbox; cold miss starts a new one.
        hit_entry: BenchEntry | None = None
        async with self._lock:
            if self._config.pool_disable_warm_reuse:
                bench = None  # skip the bench
            else:
                bench = self._benches.get(key)
            if bench:
                hit_entry = bench.popleft()
                self._global_warm_count -= 1

        if hit_entry is not None:
            handle = hit_entry.handle
            await handle.set_env(env)
        else:
            handle = await self._backend.start(
                project_id=project_id,
                run_id=run_id,
                image=image,
                image_digest=image_digest,
                env=env,
                network_policy=self._config.network_policy,
                resource_limits=self._config.resource_limits,
                workdir_mount=workdir,
                lifetime=SandboxLifetime.PER_RUN,
            )
            if image_digest and image_digest not in self._probed_digests:
                try:
                    await self._backend.probe_runner(handle)
                    self._probed_digests.add(image_digest)
                except Exception:
                    logger.debug("probe_runner failed", exc_info=True)

        # Sealed-iii.B: wrap inner handle in RedactingSandboxHandle when
        # secrets are present in the run's effective profile. Redactor reads
        # the per-exec env dict (Plan 1.5 mechanic) — the same values the
        # sandbox sees — as its forbidden-substring source-of-truth.
        if effective_secret_keys and self._secret_provider is not None:
            try:
                secret_values = {k: env[k] for k in effective_secret_keys if k in env}
                if secret_values:
                    from sagewai.sandbox.redacting_handle import RedactingSandboxHandle
                    from sagewai.sealed.redaction import Redactor

                    redactor = Redactor(secret_values)
                    audit_writer = getattr(self._secret_provider, "_audit", None) or self._audit
                    if audit_writer is not None and redactor.value_count > 0:
                        handle = RedactingSandboxHandle(
                            handle,
                            redactor=redactor,
                            audit_writer=audit_writer,
                            run_id=run_id,
                            profile_id=security_profile_ref,
                        )
            except Exception:
                logger.debug("redacting handle wrap failed", exc_info=True)
                # Fail-open here: the inner handle is still usable.

        # Sealed-iii.D: wrap in AclFilteringSandboxHandle as the outermost
        # layer when ACL is configured. ACL filters env BEFORE inner exec;
        # composition order matters (acl outside redactor).
        if acl and effective_secret_keys:
            try:
                from sagewai.sandbox.acl_handle import AclFilteringSandboxHandle

                audit_writer = getattr(self._secret_provider, "_audit", None) or self._audit
                if audit_writer is not None:
                    handle = AclFilteringSandboxHandle(
                        handle,
                        secret_keys=set(effective_secret_keys),
                        acl=acl,
                        audit_writer=audit_writer,
                        run_id=run_id,
                        profile_id=security_profile_ref,
                    )
            except Exception:
                logger.debug("ACL handle wrap failed", exc_info=True)

        latency_ms = int((time.monotonic() - t0) * 1000)
        now = datetime.now(timezone.utc)

        if hit_entry is None:
            # Cold path — emit pool.warm before pool.acquire
            await self._record_warm(
                key=key, run_id=run_id, project_id=project_id,
                start_latency_ms=latency_ms,
            )

        async with self._lock:
            self._leases[run_id] = LeasedHandle(
                handle=handle,
                key=key,
                run_id=run_id,
                leased_at=datetime.now(timezone.utc),
            )
            bench_size_after = len(self._benches.get(key, ()))

        hit = hit_entry is not None
        await self._record_acquire(
            key=key, run_id=run_id, project_id=project_id,
            hit=hit, latency_ms=latency_ms, bench_size_after=bench_size_after, now=now,
        )

        try:
            yield handle
        finally:
            await self._release(run_id)

    @staticmethod
    async def _release_with_cleanup(
        *,
        provider: Any,
        run: Any,
        handle: Any,
    ) -> str:
        """Cleanup hook for pool release. Returns 'pooled' or 'discarded'.

        On any cleanup exception: emit discard audit (best-effort) and
        return 'discarded'. Caller (the actual release path) is then
        responsible for stopping `handle` and not pooling it.
        """
        try:
            result = await provider.cleanup_run(
                run_id=run.run_id,
                project_id=getattr(run, "project_id", None),
                sandbox_handle=handle,
                effective_env_keys=list(getattr(run, "effective_env_keys", []) or []),
                effective_secret_keys=list(getattr(run, "effective_secret_keys", []) or []),
                security_profile_ref=getattr(run, "security_profile_ref", None),
            )
        except Exception as exc:
            # Best-effort discard audit (use the provider's audit writer if
            # we can reach it)
            audit = getattr(provider, "_audit", None)
            if audit is not None:
                try:
                    await audit.emit(
                        event_type="pool.sandbox.discarded_after_cleanup_failure",
                        run_id=run.run_id,
                        project_id=getattr(run, "project_id", None),
                        details={
                            "error_type": type(exc).__name__,
                            "error_message_redacted": str(exc)[:200],
                            "env_keys_intended_to_scrub": sorted(
                                getattr(run, "effective_env_keys", []) or []
                            ),
                        },
                    )
                except Exception:
                    pass  # audit best-effort; pool decision is what matters
            try:
                await handle.stop()
            except Exception:
                pass
            return "discarded"

        # If provider reported a non-empty error field, also discard
        if result.error:
            try:
                await handle.stop()
            except Exception:
                pass
            return "discarded"

        return "pooled"

    async def _release(self, run_id: str) -> None:
        async with self._lock:
            lease = self._leases.pop(run_id, None)
        if lease is None:
            return
        key = lease.key

        # cleanup_run hook (Sealed-iii.A).
        if self._secret_provider is not None:
            verdict = await LocalCacheSandboxPool._release_with_cleanup(
                provider=self._secret_provider,
                run=_RunRecordShim(run_id=run_id),
                handle=lease.handle,
            )
        else:
            verdict = "pooled"

        if verdict == "discarded":
            # _release_with_cleanup already stopped the handle.
            lease_ms = int((datetime.now(timezone.utc) - lease.leased_at).total_seconds() * 1000)
            await self._record_release(
                key=key, run_id=run_id, project_id="",
                outcome="discarded", lease_duration_ms=lease_ms,
            )
            return

        # Pool the handle on the bench, or evict if over cap.
        outcome: str
        async with self._lock:
            if self._config.pool_disable_warm_reuse:
                evict_handle = lease.handle
                outcome = "evicted_bench_full"
            else:
                bench = self._benches.setdefault(key, deque())
                over_per_tuple = len(bench) >= self._config.pool_max_warm_per_tuple
                over_global = self._global_warm_count >= self._config.pool_max_warm_global
                if over_per_tuple or over_global:
                    # Passive eviction: stop instead of bench
                    evict_handle = lease.handle
                    outcome = "evicted_bench_full"
                else:
                    bench.append(BenchEntry(handle=lease.handle, pooled_at=datetime.now(timezone.utc), last_run_id=run_id))
                    self._global_warm_count += 1
                    evict_handle = None
                    outcome = "pooled"

        if evict_handle is not None:
            try:
                await evict_handle.stop()
            except Exception:
                logger.debug("evict on release stop failed", exc_info=True)

        lease_ms = int((datetime.now(timezone.utc) - lease.leased_at).total_seconds() * 1000)
        await self._record_release(
            key=key, run_id=run_id, project_id="",
            outcome=outcome, lease_duration_ms=lease_ms,
        )

    async def _build_env(self, **kwargs) -> dict[str, str]:
        if self._secret_provider is None:
            return {}

        # Sealed-iii.C: replay path bypasses cascade re-resolution.
        # Errors (e.g. RotationDriftError) deliberately propagate so the
        # worker fails the replay run with a clear cause.
        replay_snapshot = kwargs.get("replay_snapshot")
        if replay_snapshot is not None:
            return await self._secret_provider.replay_env_for(
                project_id=kwargs["project_id"],
                run_id=kwargs["run_id"],
                agent_id=None,
                snapshot=replay_snapshot,
            )

        try:
            return await self._secret_provider.env_for(
                project_id=kwargs["project_id"],
                run_id=kwargs["run_id"],
                agent_id=None,
                declared_scopes=[],
                security_profile_ref=kwargs.get("security_profile_ref"),
                effective_env_keys=kwargs.get("effective_env_keys"),
                effective_secret_keys=kwargs.get("effective_secret_keys"),
                sealed_levels=None,
            )
        except Exception:
            logger.debug("secret_provider.env_for failed", exc_info=True)
            return {}

    async def _reap_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._config.pool_reap_interval_s)
                try:
                    await self._backend.reap(older_than=timedelta(minutes=10))
                except Exception:
                    logger.debug("backend.reap failed", exc_info=True)
                try:
                    await self._reap_once(now=datetime.now(timezone.utc))
                except Exception:
                    logger.exception("pool reap_once failed")
            except asyncio.CancelledError:
                return

    async def _reap_once(self, *, now: datetime) -> None:
        idle_cutoff = now - timedelta(seconds=self._config.pool_idle_timeout_s)
        expired: list[tuple[PoolKey, BenchEntry, str]] = []

        async with self._lock:
            # 1. Idle-timeout sweep (per bench)
            for key, bench in self._benches.items():
                while bench and bench[0].pooled_at <= idle_cutoff:
                    entry = bench.popleft()
                    self._global_warm_count -= 1
                    expired.append((key, entry, "idle_timeout"))

            # 2. Global-LRU enforcement (rare; usually no-op)
            if self._global_warm_count > self._config.pool_max_warm_global:
                overflow = self._global_warm_count - self._config.pool_max_warm_global
                all_entries = sorted(
                    ((k, e) for k, b in self._benches.items() for e in b),
                    key=lambda kv: kv[1].pooled_at,
                )
                for key, entry in all_entries[:overflow]:
                    self._benches[key].remove(entry)
                    self._global_warm_count -= 1
                    expired.append((key, entry, "global_lru"))

        # Stop handles outside the lock.
        for key, entry, reason in expired:
            try:
                await entry.handle.stop()
            except Exception:
                logger.debug("evict.stop failed for %s", key, exc_info=True)
            self._record_evict(key=key, reason=reason, now=now)

    def _record_evict(self, *, key: PoolKey, reason: str, now: datetime) -> None:
        rec = self._stats.setdefault(key, PoolStatsRecord())
        rec.record_evict(reason=reason, now=now)
        rec.warm_count = max(rec.warm_count - 1, 0)
        if self._audit is not None:
            # Sync method called from reaper; schedule emit but don't block.
            asyncio.create_task(
                self._audit.emit(
                    event_type="pool.evict",
                    run_id=None,
                    project_id=None,
                    details={
                        "image_variant": key.image_variant.value,
                        "execution_mode": key.execution_mode.value,
                        "reason": reason,
                    },
                )
            )

    async def _record_acquire(
        self, *, key: PoolKey, run_id: str, project_id: str,
        hit: bool, latency_ms: int, bench_size_after: int, now: datetime
    ) -> None:
        rec = self._stats.setdefault(key, PoolStatsRecord())
        rec.record_acquire(hit=hit, now=now)
        if hit:
            rec.warm_count = max(rec.warm_count - 1, 0)
        rec.active_count += 1

        if self._audit is not None:
            try:
                await self._audit.emit(
                    event_type="pool.acquire",
                    run_id=run_id,
                    project_id=project_id,
                    details={
                        "image_variant": key.image_variant.value,
                        "execution_mode": key.execution_mode.value,
                        "network_policy": key.network_policy.value,
                        "hit": hit,
                        "bench_size_after": bench_size_after,
                        "acquire_latency_ms": latency_ms,
                    },
                )
            except Exception:
                logger.debug("audit pool.acquire failed", exc_info=True)

    async def _record_warm(
        self, *, key: PoolKey, run_id: str, project_id: str, start_latency_ms: int
    ) -> None:
        if self._audit is not None:
            try:
                await self._audit.emit(
                    event_type="pool.warm",
                    run_id=run_id,
                    project_id=project_id,
                    details={
                        "image_variant": key.image_variant.value,
                        "execution_mode": key.execution_mode.value,
                        "image_digest": key.image_digest,
                        "start_latency_ms": start_latency_ms,
                    },
                )
            except Exception:
                logger.debug("audit pool.warm failed", exc_info=True)

    async def _record_release(
        self, *, key: PoolKey, run_id: str, project_id: str,
        outcome: str, lease_duration_ms: int
    ) -> None:
        rec = self._stats.setdefault(key, PoolStatsRecord())
        rec.active_count = max(rec.active_count - 1, 0)
        if outcome == "pooled":
            rec.warm_count += 1
        if outcome == "discarded":
            rec.record_discard_after_cleanup()

        if self._audit is not None:
            try:
                await self._audit.emit(
                    event_type="pool.release",
                    run_id=run_id,
                    project_id=project_id,
                    details={
                        "image_variant": key.image_variant.value,
                        "execution_mode": key.execution_mode.value,
                        "outcome": outcome,
                        "lease_duration_ms": lease_duration_ms,
                    },
                )
            except Exception:
                logger.debug("audit pool.release failed", exc_info=True)

    async def stats_snapshot(self) -> PoolStatsSnapshot:
        """Read the live in-memory stats and produce a JSON-serialisable snapshot."""
        from sagewai.sandbox.pool_stats import AggregateStats, PerTupleStats

        now = datetime.now(timezone.utc)
        async with self._lock:
            per_tuple = [
                PerTupleStats(
                    image_variant=k.image_variant.value,
                    execution_mode=k.execution_mode.value,
                    network_policy=k.network_policy.value,
                    warm_count=r.warm_count,
                    warm_max=self._config.pool_max_warm_per_tuple,
                    active_count=r.active_count,
                    hit_rate_1h=r.hit_rate_1h(now=now),
                    last_evict_at=r.last_evict_at,
                    last_evict_reason=r.last_evict_reason,
                )
                for k, r in self._stats.items()
            ]
            aggregate_warm = self._global_warm_count
            aggregate_active = len(self._leases)
            # Aggregate hit_rate: combine ring totals across all per-tuple records
            agg_hits = sum(rec.hits_total for rec in self._stats.values())
            agg_misses = sum(rec.misses_total for rec in self._stats.values())
            agg_total = agg_hits + agg_misses
            agg_hit_rate = (agg_hits / agg_total) if agg_total else None
            agg_last_evict = max(
                (r.last_evict_at for r in self._stats.values() if r.last_evict_at is not None),
                default=None,
            )

        return PoolStatsSnapshot(
            worker_id=self._worker_id,
            captured_at=now,
            per_tuple=per_tuple,
            aggregate=AggregateStats(
                warm_count=aggregate_warm,
                warm_max_global=self._config.pool_max_warm_global,
                active_count=aggregate_active,
                hit_rate_1h=agg_hit_rate,
                last_evict_at=agg_last_evict,
            ),
        )

    def advertised_labels(self) -> dict[str, str]:
        return {
            "sandbox.mode": self.mode.value,
            "sandbox.backend": self._backend.name,
            "sandbox.network_policy": self._config.network_policy.value,
        }


class _RunRecordShim:
    """Minimal `run` shim for _release_with_cleanup (Sealed-iii.A) which uses
    `getattr(run, ...)` to pull effective_env_keys, etc.

    Future-proof: when per-step mode lands, the worker passes a real
    WorkflowRun in via _release()."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.project_id = None
        self.effective_env_keys: list[str] = []
        self.effective_secret_keys: list[str] = []
        self.security_profile_ref = None
