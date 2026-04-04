# Copyright 2026 Sagecurator
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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sagewai.core.state import DurableWorkflow, WorkflowRun
    from sagewai.core.stores.postgres import PostgresStore
    from sagewai.models.worker import WorkerCredentials

from sagewai.fleet.normalizer import ModelNormalizer

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

    @property
    def worker_id(self) -> str:
        """Unique identifier for this worker process."""
        return f"{platform.node()}:{os.getpid()}"

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

        # Register in the workers table for visibility and load balancing
        if hasattr(self._store, "register_worker"):
            try:
                await self._store.register_worker(
                    self.worker_id,
                    pool=self._pool,
                    labels=self._labels,
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

        Args:
            wf_run: The claimed WorkflowRun with ``_input``
                attached by ``claim_pending_run``.
        """
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
