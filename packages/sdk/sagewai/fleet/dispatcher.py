# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Fleet task dispatcher for enterprise remote workers.

Handles the server-side long-poll claim and report flow:

1. Workers call :meth:`FleetDispatcher.claim` to long-poll for available tasks.
2. When a task matches the worker's capabilities, it is returned.
3. After execution, workers call :meth:`FleetDispatcher.report` with the result.

The dispatcher is decoupled from any specific persistence backend via the
:class:`TaskStore` protocol.  An :class:`InMemoryTaskStore` is provided for
testing; production use will wire in the Postgres workflow-run store via the
gateway layer.

Usage::

    from sagewai.fleet.dispatcher import FleetDispatcher, InMemoryTaskStore

    store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(store=store, poll_timeout=5.0)

    # Enqueue a task (test helper)
    store.enqueue({"run_id": "r1", "model": "gpt-4o", "pool": "default", "payload": "..."})

    # Worker claims
    task = await dispatcher.claim(
        worker_id="w1", org_id="org1", models_canonical=["gpt-4o"],
    )

    # Worker reports back
    await dispatcher.report(worker_id="w1", org_id="org1", run_id="r1", status="completed")
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from sagewai.sandbox.models import NetworkPolicy, SandboxImageVariant, SandboxMode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TaskStore protocol
# ---------------------------------------------------------------------------


class NotTaskOwnerError(Exception):
    """Raised when a worker reports a run it did not claim."""


@runtime_checkable
class TaskStore(Protocol):
    """Abstract store for task claim/report operations.

    Implementations must provide async ``claim_task`` and ``report_task``
    methods.  The dispatcher calls these in a polling loop.
    """

    async def claim_task(
        self,
        worker_id: str,
        org_id: str,
        models_canonical: list[str],
        pool: str,
        labels: dict[str, str] | None,
        *,
        project_id: str | None = None,
        worker_sandbox_mode: SandboxMode = SandboxMode.NONE,
        worker_sandbox_variants: list[SandboxImageVariant] | None = None,
        worker_network_policy: NetworkPolicy = NetworkPolicy.NONE,
    ) -> dict[str, Any] | None:
        """Attempt to claim a single task matching the worker's capabilities.

        The three keyword-only sandbox params allow the store to filter out
        tasks whose requirements exceed the worker's capabilities.

        Returns:
            A task dict with at least ``run_id`` and ``payload`` keys, or
            ``None`` if nothing is available right now.
        """
        ...

    async def report_task(
        self,
        run_id: str,
        status: str,
        output: str | None,
        error: str | None,
        *,
        worker_id: str,
    ) -> None:
        """Report completion/failure. Raises NotTaskOwnerError if run_id was not
        claimed by worker_id; idempotent no-op for a same-worker+status duplicate."""
        ...


# ---------------------------------------------------------------------------
# InMemoryTaskStore — for testing
# ---------------------------------------------------------------------------


class InMemoryTaskStore:
    """In-memory :class:`TaskStore` for unit tests and local development.

    Tasks are stored as plain dicts in an internal list.  The
    :meth:`enqueue` helper adds tasks that :meth:`claim_task` will match
    against.

    Matching rules (all optional — an empty task matches any worker):

    * ``model`` — must appear in the worker's ``models_canonical`` list.
    * ``pool`` — must equal the worker's ``pool``.
    * ``labels`` — every key-value pair must be present in the worker's labels.
    """

    def __init__(self) -> None:
        self._pending: list[dict[str, Any]] = []
        self._claimed: dict[str, dict[str, Any]] = {}  # run_id -> task
        self._completed: dict[str, dict[str, Any]] = {}  # run_id -> result

    def enqueue(self, task: dict[str, Any]) -> None:
        """Add a task to the pending queue.

        The task dict should contain at least ``run_id``.  Optional keys
        used for matching: ``model``, ``pool``, ``labels``.
        """
        self._pending.append(task)

    async def claim_task(
        self,
        worker_id: str,
        org_id: str,
        models_canonical: list[str],
        pool: str,
        labels: dict[str, str] | None,
        *,
        project_id: str | None = None,
        worker_sandbox_mode: SandboxMode = SandboxMode.NONE,
        worker_sandbox_variants: list[SandboxImageVariant] | None = None,
        worker_network_policy: NetworkPolicy = NetworkPolicy.NONE,
    ) -> dict[str, Any] | None:
        """Claim the first matching task from the pending queue.

        The sandbox kwargs are accepted for protocol compatibility with
        :class:`PostgresStore` but are not enforced in the in-memory store
        (sandbox filtering is a Postgres SQL concern; unit tests stub it out).
        """
        for i, task in enumerate(self._pending):
            # Org filter (cross-org isolation): a task stamped with an org_id may
            # only be claimed by a worker from that same org. Tasks with no org_id
            # (legacy / internal self-claimed) are not org-restricted.
            task_org = task.get("org_id")
            if task_org is not None and task_org != org_id:
                continue

            # Project filter (strict equality; None == None = org-global).
            if task.get("project_id") != project_id:
                continue

            # Model filter
            task_model = task.get("model")
            if task_model and task_model not in models_canonical:
                continue

            # Pool filter
            task_pool = task.get("pool", "default")
            if task_pool != pool:
                continue

            # Label filter
            task_labels = task.get("labels") or {}
            worker_labels = labels or {}
            if not all(worker_labels.get(k) == v for k, v in task_labels.items()):
                continue

            # Match found — remove from pending
            claimed = self._pending.pop(i)
            claimed["worker_id"] = worker_id
            claimed["claimed_at"] = datetime.now(timezone.utc).isoformat()
            self._claimed[claimed["run_id"]] = claimed
            return claimed

        return None

    async def report_task(
        self,
        run_id: str,
        status: str,
        output: str | None,
        error: str | None,
        *,
        worker_id: str,
    ) -> None:
        """Complete a claimed task. Ownership-checked + idempotent."""
        claimed = self._claimed.get(run_id)
        if claimed is not None:
            if claimed.get("worker_id") != worker_id:
                raise NotTaskOwnerError(run_id)
            self._completed[run_id] = {
                "run_id": run_id,
                "status": status,
                "output": output,
                "error": error,
                "worker_id": worker_id,
                "reported_at": datetime.now(timezone.utc).isoformat(),
            }
            self._claimed.pop(run_id, None)
            return
        prior = self._completed.get(run_id)
        if prior is not None:
            # lost-ack retry: a duplicate from the same worker + status is OK.
            if prior.get("worker_id") == worker_id and prior.get("status") == status:
                return
            raise NotTaskOwnerError(run_id)
        raise NotTaskOwnerError(run_id)


# ---------------------------------------------------------------------------
# FleetDispatcher
# ---------------------------------------------------------------------------


class FleetDispatcher:
    """Server-side task dispatcher for fleet workers.

    Workers call :meth:`claim` to long-poll for available tasks.
    When a task is available it is returned to the worker.
    After completion, workers call :meth:`report` with the result.

    Args:
        store: A :class:`TaskStore` implementation.
        encryption: Optional :class:`FleetPayloadEncryption` for
            encrypting/decrypting task payloads in transit.
        audit: Optional audit backend for recording fleet events.
        poll_interval: Seconds between store polls during a long-poll.
        poll_timeout: Maximum seconds to wait before returning ``None``.
    """

    def __init__(
        self,
        store: TaskStore,
        encryption: Any | None = None,
        audit: Any | None = None,
        poll_interval: float = 2.0,
        poll_timeout: float = 30.0,
    ) -> None:
        self._store = store
        self._encryption = encryption
        self._audit = audit
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    async def claim(
        self,
        worker_id: str,
        org_id: str,
        models_canonical: list[str],
        pool: str = "default",
        labels: dict[str, str] | None = None,
        *,
        project_id: str | None = None,
        poll_timeout: float | None = None,
        worker_sandbox_mode: SandboxMode = SandboxMode.NONE,
        worker_sandbox_variants: list[SandboxImageVariant] | None = None,
        worker_network_policy: NetworkPolicy = NetworkPolicy.NONE,
    ) -> dict[str, Any] | None:
        """Long-poll for a task matching the worker's capabilities.

        Polls the store every ``poll_interval`` seconds for up to
        ``poll_timeout`` seconds.  Returns the task dict on success or
        ``None`` on timeout.

        The three keyword-only sandbox params are forwarded to the underlying
        store so that only tasks the worker can satisfy are claimed.

        If encryption is configured, the ``payload`` field is decrypted
        before returning.  A ``RUN_CLAIMED`` audit event is recorded on
        success.
        """
        loop = asyncio.get_event_loop()
        effective_timeout = self._poll_timeout if poll_timeout is None else poll_timeout
        deadline = loop.time() + effective_timeout

        while loop.time() < deadline:
            task = await self._store.claim_task(
                worker_id=worker_id,
                org_id=org_id,
                models_canonical=models_canonical,
                pool=pool,
                labels=labels,
                project_id=project_id,
                worker_sandbox_mode=worker_sandbox_mode,
                worker_sandbox_variants=worker_sandbox_variants,
                worker_network_policy=worker_network_policy,
            )
            if task is not None:
                # Decrypt payload if encryption is configured
                if self._encryption and "payload" in task:
                    try:
                        task["payload"] = self._encryption.decrypt(
                            org_id, task["payload"],
                        )
                    except Exception:
                        logger.warning(
                            "Failed to decrypt payload for run %s",
                            task.get("run_id", "?"),
                        )

                # Record audit event
                if self._audit:
                    await self._record_audit(
                        event_type="RUN_CLAIMED",
                        worker_id=worker_id,
                        org_id=org_id,
                        run_id=task.get("run_id", ""),
                        detail=f"Claimed by worker {worker_id}",
                    )

                logger.debug(
                    "Worker %s claimed run %s",
                    worker_id,
                    task.get("run_id", "?"),
                )
                return task

            # Not found — wait and retry
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(self._poll_interval, remaining))

        logger.debug("Worker %s claim timed out after %.1fs", worker_id, self._poll_timeout)
        return None

    async def report(
        self,
        worker_id: str,
        org_id: str,
        run_id: str,
        status: str,
        output: str | None = None,
        error: str | None = None,
    ) -> None:
        """Report task completion or failure.

        If encryption is configured, the ``output`` is encrypted before
        being stored.  A ``RUN_REPORTED`` audit event is recorded.

        Args:
            worker_id: Reporting worker's ID.
            org_id: Organization ID.
            run_id: The run being reported on.
            status: ``"completed"`` or ``"failed"``.
            output: Task output (on success).
            error: Error message (on failure).
        """
        encrypted_output = output
        if self._encryption and output:
            try:
                encrypted_output = self._encryption.encrypt(org_id, output)
            except Exception:
                logger.warning("Failed to encrypt output for run %s", run_id)

        await self._store.report_task(
            run_id=run_id,
            status=status,
            output=encrypted_output,
            error=error,
            worker_id=worker_id,
        )

        if self._audit:
            detail = f"Reported {status} by worker {worker_id}"
            if error:
                detail += f": {error}"
            await self._record_audit(
                event_type="RUN_REPORTED",
                worker_id=worker_id,
                org_id=org_id,
                run_id=run_id,
                detail=detail,
            )

        logger.debug("Worker %s reported run %s as %s", worker_id, run_id, status)

    async def heartbeat(self, worker_id: str) -> None:
        """Forward a heartbeat signal.

        If an audit backend is configured, records a ``HEARTBEAT`` event.
        The actual heartbeat update on the worker record is handled by the
        fleet registry (not yet wired).
        """
        if self._audit:
            await self._record_audit(
                event_type="HEARTBEAT",
                worker_id=worker_id,
                org_id="",
                run_id="",
                detail=f"Heartbeat from {worker_id}",
            )
        logger.debug("Heartbeat from worker %s", worker_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _record_audit(
        self,
        event_type: str,
        worker_id: str,
        org_id: str,
        run_id: str,
        detail: str,
    ) -> None:
        """Record an audit event if the backend supports it."""
        try:
            await self._audit.record(
                event_type=event_type,
                worker_id=worker_id,
                org_id=org_id,
                run_id=run_id,
                detail=detail,
                timestamp=datetime.now(timezone.utc),
            )
        except Exception:
            logger.warning("Failed to record audit event: %s", event_type)
