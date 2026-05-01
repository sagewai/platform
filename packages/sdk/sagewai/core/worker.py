# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""WorkflowWorker — distributed workflow execution consumer.

Polls PostgresStore for PENDING workflow runs, claims them atomically,
and executes them with heartbeat emission. Multiple workers can run
concurrently against the same database for horizontal scaling.

The worker uses the same FOR UPDATE SKIP LOCKED pattern as Temporal,
pgqueuer, and graphile-worker for contention-free work distribution.

Workers support **pool/label-based routing** (like Temporal task queues)
and **per-worker credential injection** via ``ContextVar``.

Usage::

    from sagewai.core.worker import WorkflowWorker
    from sagewai.core.stores.postgres import PostgresStore
    from sagewai.models.worker import WorkerCredentials
    from sagewai.models.inference import InferenceParams

    store = PostgresStore(database_url="postgresql://localhost/sagewai")
    await store.initialize()

    # Worker with Ollama on local GPU
    worker = WorkflowWorker(
        store=store,
        workflow_registry={"article-pipeline": my_workflow},
        pool="local-ollama",
        labels={"zone": "local", "gpu": True},
        credentials=WorkerCredentials(
            model_overrides={"default": "ollama/llama3.2"},
            inference_overrides=InferenceParams(
                api_base="http://localhost:11434",
            ),
        ),
    )
    await worker.start()  # blocks until shutdown
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
import traceback
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sagewai.core.state import DurableWorkflow, WorkflowRun
    from sagewai.core.stores.postgres import PostgresStore
    from sagewai.models.worker import WorkerCredentials

from sagewai.fleet.normalizer import ModelNormalizer
from sagewai.sandbox.backend import SandboxBackend
from sagewai.sandbox.fallback import apply_fallback
from sagewai.sandbox.models import SandboxConfig, SandboxMode
from sagewai.sandbox.null_backend import NullBackend
from sagewai.sandbox.pool_protocol import SandboxPool
from sagewai.sandbox.registry import resolve_mode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ContextVar for per-worker credential injection
# ---------------------------------------------------------------------------

_worker_credentials: ContextVar[WorkerCredentials | None] = ContextVar(
    "_worker_credentials", default=None
)


def get_worker_credentials() -> WorkerCredentials | None:
    """Get the current worker's credentials from the ContextVar.

    Called by ``UniversalAgent._build_litellm_kwargs()`` to apply
    worker-level model/api_key/api_base overrides at LLM call time.

    Returns ``None`` when not running inside a WorkflowWorker or when
    the worker has no credentials configured.
    """
    return _worker_credentials.get()


async def _check_run_revocation_and_abort(
    *,
    store: Any,
    run_id: str,
    sandbox: Any,
) -> bool:
    """Check workflow_runs.revoked_at; if set, stop sandbox + mark run failed.

    Reads the typed ``revoked_at`` column directly via SQL (NOT via
    ``load_run``/JSONB) so that hard-revoke fan-out written by Task 4's
    SQL UPDATE is visible before the next ``save_run`` call.

    Returns True if abort happened, False otherwise. Idempotent: subsequent
    calls on a run already aborted just see status='failed' and return False.
    """
    row = await store._pool.fetchrow(
        """
        SELECT revoked_at, revoke_reason, status
        FROM workflow_runs WHERE run_id = $1
        """,
        run_id,
    )
    if row is None:
        return False
    if row["revoked_at"] is None:
        return False
    if row["status"] != "running":
        return False  # already aborted or never running

    # Stop sandbox (best-effort)
    try:
        await sandbox.stop()
    except Exception:
        logger.exception(
            "sandbox.stop failed during revocation abort run_id=%s", run_id,
        )

    # Mark run failed
    await store._pool.execute(
        """
        UPDATE workflow_runs
        SET status = 'failed',
            updated_at = NOW(),
            output = COALESCE(output, '{}'::jsonb) || '{"error": "secret_revoked"}'::jsonb
        WHERE run_id = $1 AND status = 'running'
        """,
        run_id,
    )

    # Audit emit (best-effort)
    try:
        from sagewai.sealed.audit import AuditWriter

        await AuditWriter(store).emit(
            event_type="run.aborted_by_revocation",
            run_id=run_id,
            details={
                "revoke_reason": row["revoke_reason"],
                "original_revoked_at": row["revoked_at"].isoformat()
                if row["revoked_at"]
                else None,
            },
        )
    except Exception:
        logger.exception(
            "audit emit failed during revocation abort run_id=%s", run_id,
        )

    return True


def _select_backend(
    config: SandboxConfig,
    *,
    mode: SandboxMode,
    override: Any | None,
    kubernetes_config: dict | None,
) -> SandboxBackend:
    """Resolve the SandboxBackend instance from config."""
    if mode is SandboxMode.NONE:
        return NullBackend()
    if override is not None:
        return override
    name = config.backend
    if name == "docker":
        from sagewai.sandbox.docker_backend import DockerBackend  # lazy
        return DockerBackend()
    if name == "kubernetes":
        from sagewai.sandbox.kubernetes_backend import KubernetesBackend  # lazy
        kc = kubernetes_config or {}
        return KubernetesBackend(
            kubeconfig_path=kc.get("kubeconfig_path") or config.kubernetes_kubeconfig_path,
            use_in_cluster=kc.get("use_in_cluster", config.kubernetes_use_in_cluster),
            namespace=kc.get("namespace") or config.kubernetes_namespace,
            egress_allowlist=kc.get("egress_allowlist") or list(config.network_egress_allowlist),
        )
    if name == "null":
        return NullBackend()
    raise ValueError(f"unknown sandbox backend: {name!r}")


def _build_pool(
    *,
    backend,
    config,
    worker_id: str,
    scratch_root,
    sealed_secret_provider=None,
    audit_writer=None,
):
    """Pick the SandboxPool implementation based on backend.pool_strategy.

    Plan 1.5 ships LocalCacheSandboxPool (Docker, Null, future Firecracker).
    Threads 3 and 4 add ExternalMinReplicasSandboxPool (K8s) and
    ProviderManagedSandboxPool (Lambda) — when those land, extend this
    factory with a new branch.
    """
    from sagewai.sandbox.pool_protocol import PoolStrategy

    strategy = getattr(backend, "pool_strategy", None)
    if strategy == PoolStrategy.LOCAL_CACHE:
        from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool
        return LocalCacheSandboxPool(
            backend=backend,
            config=config,
            worker_id=worker_id,
            scratch_root=scratch_root,
            sealed_secret_provider=sealed_secret_provider,
            audit_writer=audit_writer,
        )
    if strategy == PoolStrategy.EXTERNAL_MIN_REPLICAS:
        from sagewai.sandbox.external_pool import ExternalMinReplicasSandboxPool
        return ExternalMinReplicasSandboxPool(
            backend=backend,
            config=config,
            worker_id=worker_id,
            scratch_root=scratch_root,
            sealed_secret_provider=sealed_secret_provider,
            audit_writer=audit_writer,
        )
    raise NotImplementedError(
        f"Pool strategy {strategy} not yet implemented (Thread 4 / Lambda)"
    )


class WorkflowWorker:
    """Distributed workflow execution consumer.

    Polls a PostgresStore for PENDING workflow runs, claims them
    atomically via ``FOR UPDATE SKIP LOCKED``, and executes them
    with periodic heartbeat emission. Multiple workers can run
    concurrently against the same database for horizontal scaling.

    Supports **pool/label-based routing** (like Temporal task queues)
    and **per-worker credential injection** via ``ContextVar``.

    Parameters
    ----------
    store:
        PostgresStore instance with queue operations.
    workflow_registry:
        Maps workflow names to DurableWorkflow instances.
    max_concurrent:
        Maximum number of workflows to execute concurrently.
    poll_interval:
        Seconds between poll cycles when the queue is empty.
    heartbeat_interval:
        Seconds between heartbeats for each in-flight workflow.
    shutdown_timeout:
        Maximum seconds to wait for in-flight workflows on shutdown.
    project_id:
        Project scope — like Temporal's namespace. Workers only claim
        runs belonging to this project. Set to ``None`` for global.
    pool:
        Worker pool name — like a Temporal task queue. Workers only
        claim runs targeting this pool (or unrouted runs).
    labels:
        Key-value metadata for label-based routing. Runs can require
        specific labels via JSONB containment matching.
    credentials:
        Per-worker LLM credentials injected via ``ContextVar`` during
        execution. Never stored in the database.
    models_supported:
        Raw model names this worker can serve. Auto-normalized to
        canonical form via ``ModelNormalizer`` for matching at claim
        time. When set, the worker only claims runs whose
        ``target_model`` matches one of the canonical names (or runs
        with no ``target_model``).
    """

    def __init__(
        self,
        store: PostgresStore,
        workflow_registry: dict[str, DurableWorkflow],
        *,
        max_concurrent: int = 4,
        poll_interval: float = 2.0,
        heartbeat_interval: float = 30.0,
        shutdown_timeout: float = 30.0,
        project_id: str | None = None,
        pool: str = "default",
        labels: dict[str, Any] | None = None,
        credentials: WorkerCredentials | None = None,
        models_supported: list[str] | None = None,
        # ── sandboxing ────────────────────────────────────────────────
        sandbox_backend: SandboxBackend | None = None,
        sandbox_config: SandboxConfig | None = None,
        sandbox_scratch_root: Path | None = None,
        sandbox_kubernetes_config: dict | None = None,
        project_environment: str | None = None,
        # ── fleet registry (for pool_stats heartbeat forwarding) ──────
        fleet_registry: Any | None = None,
    ) -> None:
        self._store = store
        self._registry = workflow_registry
        self._max_concurrent = max_concurrent
        self._poll_interval = poll_interval
        self._heartbeat_interval = heartbeat_interval
        self._shutdown_timeout = shutdown_timeout
        self._project_id = project_id
        self._pool = pool
        self._labels = labels or {}
        self._credentials = credentials
        self._models_supported = models_supported or []
        self._models_canonical = ModelNormalizer.canonical_list(
            self._models_supported
        )

        self._shutdown_event = asyncio.Event()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_tasks: set[asyncio.Task[None]] = set()

        self._sandbox_backend_override = sandbox_backend
        self._sandbox_config = sandbox_config or SandboxConfig()
        self._sandbox_scratch_root = (
            sandbox_scratch_root or Path.home() / ".sagewai" / "workers"
        )
        self._kubernetes_config = sandbox_kubernetes_config
        self._project_environment = project_environment
        self._sandbox_pool: SandboxPool | None = None
        self._fleet_registry = fleet_registry

    @property
    def worker_id(self) -> str:
        """Unique identifier for this worker process."""
        return f"{platform.node()}:{os.getpid()}"

    async def _start_sandbox_pool(self) -> None:
        """Resolve the effective mode, select a backend, and start the pool."""
        cli_flag = self._sandbox_config.mode
        effective = resolve_mode(
            cli_flag=cli_flag,
            config=self._sandbox_config,
            project_environment=self._project_environment,
        )

        # Select backend (delegated for testability)
        backend = _select_backend(
            self._sandbox_config,
            mode=effective,
            override=self._sandbox_backend_override,
            kubernetes_config=self._kubernetes_config,
        )

        # Health check + fallback
        health = await backend.health_check()
        production = (self._project_environment == "production")
        effective = apply_fallback(effective, health, production=production)

        # If fallback dropped us to NONE, swap backend.
        if effective is SandboxMode.NONE and backend.name != "null":
            backend = NullBackend()

        config = self._sandbox_config.model_copy(update={"mode": effective})
        self._sandbox_pool = _build_pool(
            backend=backend,
            config=config,
            worker_id=self.worker_id.replace(":", "-"),
            scratch_root=self._sandbox_scratch_root,
            sealed_secret_provider=None,   # wired when secret-provider integration lands
            audit_writer=None,             # wired when audit integration lands
        )
        await self._sandbox_pool.start()
        logger.info(
            "sandbox pool started mode=%s backend=%s",
            effective.value,
            backend.name,
        )

    async def _stop_sandbox_pool(self) -> None:
        if self._sandbox_pool is not None:
            await self._sandbox_pool.stop()
            self._sandbox_pool = None

    async def start(self) -> None:
        """Main loop: poll for pending runs, claim and execute.

        Registers the worker in the ``workers`` table on startup
        and deregisters on shutdown. Blocks until ``stop()`` is
        called or a shutdown signal is received.
        """
        logger.info(
            "WorkflowWorker started (id=%s, pool=%s, "
            "labels=%s, max_concurrent=%d, poll_interval=%.1fs)",
            self.worker_id,
            self._pool,
            self._labels,
            self._max_concurrent,
            self._poll_interval,
        )

        await self._start_sandbox_pool()

        # Build merged labels: operator labels + sandbox capability labels
        merged_labels = dict(self._labels or {})
        if self._sandbox_pool is not None:
            merged_labels.update(self._sandbox_pool.advertised_labels())

        # Register in the workers table for visibility and load balancing
        if hasattr(self._store, "register_worker"):
            try:
                await self._store.register_worker(
                    self.worker_id,
                    pool=self._pool,
                    labels=merged_labels,
                    project_id=self._project_id,
                    max_concurrent=self._max_concurrent,
                    metadata={
                        "hostname": platform.node(),
                        "pid": os.getpid(),
                        "platform": platform.system(),
                    },
                )
            except Exception:  # noqa: broad-exception-caught — resilience: table may not exist
                logger.warning(
                    "Failed to register worker %s (workers table may "
                    "not exist yet — run migrations)",
                    self.worker_id,
                    exc_info=True,
                )

        try:
            _last_worker_heartbeat = 0.0
            while not self._shutdown_event.is_set():
                await self._poll_and_execute()

                # Heartbeat the worker row periodically so the load balancer
                # sees idle workers as active (not just workers with active runs).
                now = time.monotonic()
                if (
                    hasattr(self._store, "worker_heartbeat")
                    and now - _last_worker_heartbeat >= self._heartbeat_interval
                ):
                    try:
                        await self._store.worker_heartbeat(self.worker_id)
                        _last_worker_heartbeat = now
                    except Exception:  # noqa: broad-exception-caught
                        logger.warning(
                            "Worker heartbeat failed for %s", self.worker_id
                        )

                    # Forward pool stats to the fleet registry so the admin UI
                    # `<PoolStatsPanel>` can render live data. Only when both
                    # a fleet registry and a sandbox pool are configured.
                    if (
                        self._fleet_registry is not None
                        and self._sandbox_pool is not None
                    ):
                        try:
                            snap = await self._sandbox_pool.stats_snapshot()
                            await self._fleet_registry.heartbeat(
                                self.worker_id,
                                pool_stats=snap.model_dump(mode="json"),
                            )
                        except Exception:  # noqa: broad-exception-caught
                            logger.warning(
                                "Pool-stats heartbeat to fleet registry failed for %s",
                                self.worker_id,
                                exc_info=True,
                            )

                # Wait for poll_interval or shutdown, whichever first
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self._poll_interval,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            # Deregister worker on shutdown
            if hasattr(self._store, "deregister_worker"):
                try:
                    await self._store.deregister_worker(self.worker_id)
                except Exception:  # noqa: broad-exception-caught — resilience: best-effort cleanup
                    logger.warning(
                        "Failed to deregister worker %s",
                        self.worker_id,
                        exc_info=True,
                    )
            await self._stop_sandbox_pool()
            await self._drain()
            logger.info("WorkflowWorker stopped (id=%s)", self.worker_id)

    async def stop(self) -> None:
        """Signal graceful shutdown and wait for in-flight workflows."""
        logger.info(
            "WorkflowWorker shutdown requested (id=%s, " "in_flight=%d)",
            self.worker_id,
            len(self._active_tasks),
        )
        self._shutdown_event.set()

    async def _drain(self) -> None:
        """Wait for all in-flight tasks to finish within timeout."""
        if not self._active_tasks:
            return
        logger.info(
            "Draining %d in-flight workflows (timeout=%.1fs)",
            len(self._active_tasks),
            self._shutdown_timeout,
        )
        done, pending = await asyncio.wait(
            self._active_tasks,
            timeout=self._shutdown_timeout,
        )
        if pending:
            logger.warning(
                "Cancelling %d workflows that did not finish " "in time",
                len(pending),
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _poll_and_execute(self) -> None:
        """One poll cycle: claim up to available slots, start tasks.

        Claims as many PENDING runs as there are free concurrency
        slots, wrapping each in a task managed by the semaphore.
        """
        available = self._semaphore._value
        if available <= 0:
            return

        for _ in range(available):
            if self._shutdown_event.is_set():
                break

            wf_run = await self._store.claim_pending_run(
                self.worker_id,
                project_id=self._project_id,
                worker_pool=self._pool,
                worker_labels=self._labels if self._labels else None,
                models_canonical=(
                    self._models_canonical if self._models_canonical else None
                ),
            )
            if wf_run is None:
                break  # Queue empty

            logger.info(
                "Claimed workflow %s run %s",
                wf_run.workflow_name,
                wf_run.run_id,
            )

            task: asyncio.Task[None] = asyncio.create_task(
                self._execute_guarded(wf_run),
                name=(f"wf:{wf_run.workflow_name}" f":{wf_run.run_id}"),
            )
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)

    async def _execute_guarded(self, wf_run: WorkflowRun) -> None:
        """Acquire the semaphore then execute the workflow."""
        async with self._semaphore:
            await self._execute_workflow(wf_run)

    async def _execute_workflow(self, wf_run: WorkflowRun) -> None:
        """Execute a single claimed workflow run.

        Looks up the DurableWorkflow from the registry, starts a
        heartbeat loop, and calls ``wf.run()``. Updates the store
        on completion or failure.

        The run-level ``execution_mode`` (architecture's Mode 0/1/2/3/3b)
        is logged here. Per-step mode override and per-mode dispatch
        branching are a follow-up; today the existing sandbox-pool path
        handles all modes, with mode-specific behaviour delegated to the
        Sealed cascade resolver and pool acquisition.

        Args:
            wf_run: The claimed WorkflowRun with ``_input``
                attached by ``claim_pending_run``.
        """
        logger.info(
            "Dispatching %s run %s (execution_mode=%s, sandbox_mode=%s)",
            wf_run.workflow_name,
            wf_run.run_id,
            wf_run.execution_mode.value,
            wf_run.requires_sandbox_mode.value,
        )
        workflow = self._registry.get(wf_run.workflow_name)
        if workflow is None:
            error_msg = f"Unknown workflow: {wf_run.workflow_name}"
            logger.warning(
                "Cannot execute run %s: %s",
                wf_run.run_id,
                error_msg,
            )
            await self._store.fail_run(
                wf_run.workflow_name,
                wf_run.run_id,
                error_msg,
            )
            return

        # Start heartbeat background task
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(wf_run),
            name=(f"hb:{wf_run.workflow_name}" f":{wf_run.run_id}"),
        )

        # Inject worker credentials via ContextVar so UniversalAgent
        # reads them in _build_litellm_kwargs at LLM call time.
        cred_token = _worker_credentials.set(self._credentials)

        try:
            input_data: dict[str, Any] = getattr(wf_run, "_input", {}) or {}
            result = await workflow.run(run_id=wf_run.run_id, **input_data)

            # Sealed-v directive integration — fires once after the run finishes all
            # steps (the DurableWorkflow step loop lives in state.py, not here, so
            # per-step evaluation would require a step callback; this post-run poll
            # handles approved decisions and end-of-run signal collection).
            # Opt-in: only fires when the worker has the subsystem wired in.
            # Existing tests construct WorkflowWorker without these attrs; they
            # continue to behave exactly as before.
            if getattr(self, "_signal_collector", None) is not None:
                from sagewai.sealed.directives.actions import dispatch as directive_dispatch
                from sagewai.sealed.directives.approvals import SuppressedAlreadyPendingError
                from sagewai.sealed.directives.policies import resolve_directive_policies
                from sagewai.sealed.directives.signals import SignalContext
                from sagewai.core.worker_directives import (
                    consume_approved_decisions,
                    should_evaluate_directives,
                )

                # Step 1: collect signals (runs all registered sources)
                ctx = SignalContext(
                    cost_tracker=getattr(self, "_cost_tracker_view", None),
                    audit_reader=getattr(self, "_audit_reader", None),
                    store=getattr(self, "_store_view", None),
                )
                step_index = len(wf_run.steps)
                signals = await self._signal_collector.collect(
                    run=wf_run, step_index=step_index, context=ctx,
                )

                # Step 2: dispatch any approved-but-not-consumed HITL decisions,
                # even on replay runs (HITL approval is an explicit operator action).
                _approvals = getattr(self, "_approvals", None)
                _directive_audit = getattr(self, "_directive_audit", None)
                _notifications = getattr(self, "_notifications", None)
                _store_for_dispatch = getattr(self, "_store_for_dispatch", None)

                if _approvals is not None:
                    await consume_approved_decisions(
                        run=wf_run,
                        registry=_approvals,
                        dispatch_callable=lambda d: directive_dispatch(
                            decision=d,
                            store=_store_for_dispatch,
                            audit=_directive_audit,
                            notifications=_notifications,
                        ),
                    )

                # Step 3: evaluate policies against collected signals, unless replay
                # with re-evaluation disabled (observe-only mode logs signals only).
                if not should_evaluate_directives(wf_run):
                    if _directive_audit is not None:
                        await _directive_audit.emit(
                            event_type="directive.signals_collected_replay_observed_only",
                            run_id=wf_run.run_id,
                            project_id=wf_run.project_id,
                            workflow_name=wf_run.workflow_name,
                            policy_id=None,
                            signal_kind=None,
                            severity=None,
                            details={"signal_count": len(signals)},
                        )
                elif getattr(self, "_evaluator", None) is not None:
                    _directives_config = getattr(self, "_directives_config", None)
                    config = _directives_config() if callable(_directives_config) else None
                    if config is not None:
                        policies = resolve_directive_policies(
                            workflow_name=wf_run.workflow_name,
                            project_id=wf_run.project_id,
                            config=config,
                        )
                        decisions = self._evaluator.evaluate(
                            signals=signals, policies=policies,
                        )
                        for decision in decisions:
                            if _directive_audit is not None:
                                await _directive_audit.emit(
                                    event_type="directive.evaluated",
                                    decision_id=decision.decision_id,
                                    run_id=wf_run.run_id,
                                    project_id=wf_run.project_id,
                                    workflow_name=wf_run.workflow_name,
                                    policy_id=decision.directive_policy_id,
                                    signal_kind=decision.triggering_signal.kind,
                                    severity=decision.triggering_signal.severity,
                                    details={"action_kind": decision.action.kind},
                                )
                            if decision.requires_approval:
                                if _approvals is not None:
                                    try:
                                        await _approvals.request(
                                            decision=decision,
                                            ttl_seconds=config.evaluator_settings.approval_default_ttl_seconds,
                                        )
                                    except SuppressedAlreadyPendingError:
                                        pass
                            else:
                                await directive_dispatch(
                                    decision=decision,
                                    store=_store_for_dispatch,
                                    audit=_directive_audit,
                                    notifications=_notifications,
                                )

            output: dict[str, Any] = result if isinstance(result, dict) else {"result": result}
            await self._store.complete_run(
                wf_run.workflow_name,
                wf_run.run_id,
                output=output,
            )
            logger.info(
                "Workflow %s run %s completed",
                wf_run.workflow_name,
                wf_run.run_id,
            )
        except Exception as exc:  # noqa: broad-exception-caught — worker resilience
            error_detail = f"{type(exc).__name__}: {exc}\n" f"{traceback.format_exc()}"
            logger.error(
                "Workflow %s run %s failed: %s",
                wf_run.workflow_name,
                wf_run.run_id,
                exc,
            )
            await self._store.fail_run(
                wf_run.workflow_name,
                wf_run.run_id,
                error_detail,
            )
        finally:
            _worker_credentials.reset(cred_token)
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self, wf_run: WorkflowRun) -> None:
        """Emit periodic heartbeats for a running workflow.

        Keeps calling ``store.heartbeat()`` every
        ``heartbeat_interval`` seconds so the run is not detected
        as stale by other workers or the recovery process.

        Args:
            wf_run: The running WorkflowRun to heartbeat for.
        """
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                await self._store.heartbeat(wf_run.workflow_name, wf_run.run_id)
                # Also update the worker's own heartbeat in the
                # workers table for load balancer visibility
                if hasattr(self._store, "worker_heartbeat"):
                    await self._store.worker_heartbeat(self.worker_id)
            except Exception:  # noqa: broad-exception-caught — resilience: non-critical heartbeat
                logger.warning(
                    "Heartbeat failed for %s:%s",
                    wf_run.workflow_name,
                    wf_run.run_id,
                )
