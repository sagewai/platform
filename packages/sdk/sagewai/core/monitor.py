# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""WorkflowMonitor — Temporal-like visibility into workflow executions.

Provides a read/write interface for monitoring, inspecting, and controlling
workflow executions. Designed for both CLI and Admin Panel integration.

Usage::

    from sagewai.core.monitor import WorkflowMonitor
    from sagewai.core.stores.postgres import PostgresStore

    store = PostgresStore(database_url="postgresql://localhost/sagewai")
    await store.initialize()

    monitor = WorkflowMonitor(store=store)

    # List running workflows
    executions = await monitor.list_executions(status="running")

    # Inspect a specific execution
    detail = await monitor.get_execution("run-abc123")

    # Get execution timeline (like Temporal's event history)
    timeline = await monitor.get_execution_timeline("run-abc123")

    # Retry a failed execution
    await monitor.retry_execution("run-abc123")
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from pydantic import BaseModel

from sagewai.core.context import resolve_project_id
from sagewai.core.state import StepStatus, WorkflowRun, WorkflowStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class ExecutionSummary(BaseModel):
    """Compact view of a workflow execution."""

    run_id: str
    workflow_name: str
    status: str
    steps_completed: int | None = None
    steps_total: int | None = None
    created_at: str
    updated_at: str
    error: str | None = None


class StepDetail(BaseModel):
    """Detail of a single workflow step."""

    step_name: str
    status: str
    result: Any = None
    error: str | None = None
    attempts: int = 0
    started_at: float | None = None
    completed_at: float | None = None
    duration_seconds: float | None = None


class ExecutionDetail(BaseModel):
    """Full detail of a workflow execution."""

    run_id: str
    workflow_name: str
    status: str
    input_data: Any = None
    output_data: Any = None
    steps: list[StepDetail] = []
    created_at: str
    updated_at: str
    error: str | None = None


class TimelineEvent(BaseModel):
    """A single event in the execution timeline."""

    id: int
    event_type: str
    data: dict[str, Any] = {}
    created_at: str


class WorkerInfo(BaseModel):
    """Status of an active worker."""

    owner_id: str
    active_runs: int
    last_heartbeat: str
    pool: str = "default"
    labels: dict[str, Any] = {}
    status: str = "active"
    max_concurrent: int = 4
    registered_at: str | None = None


class QueueStats(BaseModel):
    """Queue depth and status distribution."""

    pending: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    waiting: int = 0
    total: int = 0


# ---------------------------------------------------------------------------
# WorkflowMonitor
# ---------------------------------------------------------------------------


class WorkflowMonitor:
    """Temporal-like visibility layer for workflow executions.

    Wraps a ``WorkflowStore`` (typically ``PostgresStore``) and exposes
    query and control operations suitable for dashboards and CLIs.

    Parameters
    ----------
    store:
        A ``WorkflowStore`` that supports the required query operations.
        ``PostgresStore`` is the primary implementation.
    """

    def __init__(self, store: WorkflowStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------

    async def list_executions(
        self,
        *,
        status: str | None = None,
        workflow_name: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExecutionSummary]:
        """List workflow executions with optional filters.

        Args:
            status: Filter by status (pending, running, completed, failed, waiting).
            workflow_name: Filter by workflow name (substring match).
            project_id: Filter by project identifier for multi-tenant isolation.
            limit: Maximum number of results.
            offset: Pagination offset.

        Returns:
            List of execution summaries ordered by creation time (newest first).
        """
        if not hasattr(self._store, "list_all_runs"):
            logger.warning(
                "Store %s does not support list_all_runs; returning empty list",
                type(self._store).__name__,
            )
            return []

        rows = await self._store.list_all_runs(
            limit=limit,
            offset=offset,
            status=status,
            search=workflow_name,
            project_id=project_id,
        )
        return [
            ExecutionSummary(
                run_id=row["run_id"],
                workflow_name=row["workflow_name"],
                status=row["status"],
                steps_completed=row.get("steps_completed"),
                steps_total=row.get("steps_total"),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                error=row.get("error"),
            )
            for row in rows
        ]

    async def get_execution(self, run_id: str) -> ExecutionDetail | None:
        """Get full execution detail including all steps.

        Args:
            run_id: The workflow run ID.

        Returns:
            Execution detail with step breakdown, or None if not found.
        """
        if not hasattr(self._store, "get_run_by_run_id"):
            logger.warning(
                "Store %s does not support get_run_by_run_id",
                type(self._store).__name__,
            )
            return None

        row = await self._store.get_run_by_run_id(run_id)
        if row is None:
            return None

        # Parse steps from the embedded data dict
        steps: list[StepDetail] = []
        data = row.get("data", {})
        if isinstance(data, str):
            data = json.loads(data)

        raw_steps = data.get("steps", {}) if data else {}
        for step_name, step_data in raw_steps.items():
            started = step_data.get("started_at")
            completed = step_data.get("completed_at")
            duration = None
            if started is not None and completed is not None:
                duration = round(completed - started, 3)

            steps.append(
                StepDetail(
                    step_name=step_name,
                    status=step_data.get("status", "unknown"),
                    result=step_data.get("result"),
                    error=step_data.get("error"),
                    attempts=step_data.get("attempts", 0),
                    started_at=started,
                    completed_at=completed,
                    duration_seconds=duration,
                )
            )

        return ExecutionDetail(
            run_id=row["run_id"],
            workflow_name=row["workflow_name"],
            status=row["status"],
            input_data=row.get("input"),
            output_data=row.get("output"),
            steps=steps,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            error=row.get("error"),
        )

    async def get_execution_timeline(self, run_id: str) -> list[TimelineEvent]:
        """Get ordered event history for a run (like Temporal's event history).

        Args:
            run_id: The workflow run ID.

        Returns:
            List of timeline events ordered by creation (oldest first).
        """
        if not hasattr(self._store, "list_events"):
            logger.warning(
                "Store %s does not support list_events",
                type(self._store).__name__,
            )
            return []

        events = await self._store.list_events(run_id)
        return [
            TimelineEvent(
                id=ev["id"],
                event_type=ev["event_type"],
                data=ev.get("data", {}),
                created_at=str(ev["created_at"]),
            )
            for ev in events
        ]

    async def get_worker_status(self, *, project_id: str | None = None) -> list[WorkerInfo]:
        """Get status of active workers (those with recent heartbeats).

        Enriches worker info with pool/labels from the ``workers`` table
        when available (LEFT JOIN). Falls back to run-only data for
        workers that haven't registered in the workers table.

        Args:
            project_id: Filter by project identifier for multi-tenant isolation.

        Returns:
            List of worker info records.
        """
        if not hasattr(self._store, "_pool"):
            logger.warning("get_worker_status requires PostgresStore with _pool")
            return []

        pid = resolve_project_id(project_id)

        # Try enriched query with workers table JOIN
        try:
            rows = await self._store._pool.fetch(
                """
                SELECT r.owner_id,
                       COUNT(*) FILTER (WHERE r.status = 'running') AS active_runs,
                       MAX(r.updated_at) AS last_heartbeat,
                       COALESCE(w.pool, 'default') AS pool,
                       COALESCE(w.labels, '{}') AS labels,
                       COALESCE(w.status, 'active') AS worker_status,
                       COALESCE(w.max_concurrent, 4) AS max_concurrent,
                       w.registered_at
                FROM workflow_runs r
                LEFT JOIN workers w ON w.worker_id = r.owner_id
                WHERE r.owner_id IS NOT NULL
                  AND r.project_id = $1
                  AND r.updated_at > NOW() - INTERVAL '5 minutes'
                GROUP BY r.owner_id, w.pool, w.labels, w.status,
                         w.max_concurrent, w.registered_at
                ORDER BY last_heartbeat DESC
                """,
                pid,
            )
        except Exception:
            # Workers table may not exist — fall back to runs-only query
            rows = await self._store._pool.fetch(
                """
                SELECT owner_id,
                       COUNT(*) FILTER (WHERE status = 'running') AS active_runs,
                       MAX(updated_at) AS last_heartbeat
                FROM workflow_runs
                WHERE owner_id IS NOT NULL
                  AND project_id = $1
                  AND updated_at > NOW() - INTERVAL '5 minutes'
                GROUP BY owner_id
                ORDER BY last_heartbeat DESC
                """,
                pid,
            )

        results = []
        for row in rows:
            labels = row.get("labels", {})
            if isinstance(labels, str):
                labels = json.loads(labels)
            results.append(
                WorkerInfo(
                    owner_id=row["owner_id"],
                    active_runs=row["active_runs"],
                    last_heartbeat=str(row["last_heartbeat"]),
                    pool=row.get("pool", "default"),
                    labels=labels,
                    status=row.get("worker_status", "active"),
                    max_concurrent=row.get("max_concurrent", 4),
                    registered_at=(str(row["registered_at"]) if row.get("registered_at") else None),
                )
            )
        return results

    async def list_workers(
        self,
        *,
        pool: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
    ) -> list[WorkerInfo]:
        """List all registered workers from the workers table.

        Unlike ``get_worker_status()`` which only shows workers with
        active runs, this shows all registered workers including idle ones.

        Args:
            pool: Filter by worker pool.
            status: Filter by status (active, draining, offline).
            project_id: Filter by project.

        Returns:
            List of worker info records.
        """
        if not hasattr(self._store, "list_workers"):
            logger.warning("list_workers requires a store with list_workers method")
            return []

        rows = await self._store.list_workers(pool=pool, status=status, project_id=project_id)
        return [
            WorkerInfo(
                owner_id=row["worker_id"],
                active_runs=row.get("active_runs", 0),
                last_heartbeat=str(row["last_heartbeat"]),
                pool=row.get("pool", "default"),
                labels=row.get("labels", {}),
                status=row.get("status", "active"),
                max_concurrent=row.get("max_concurrent", 4),
                registered_at=row.get("registered_at"),
            )
            for row in rows
        ]

    async def get_queue_stats(self, *, project_id: str | None = None) -> QueueStats:
        """Get queue depth and status distribution.

        Args:
            project_id: Filter by project identifier for multi-tenant isolation.

        Returns:
            Aggregate counts of runs by status.
        """
        if not hasattr(self._store, "_pool"):
            logger.warning("get_queue_stats requires PostgresStore with _pool")
            return QueueStats()

        pid = resolve_project_id(project_id)
        rows = await self._store._pool.fetch(
            """
            SELECT status, COUNT(*) AS cnt
            FROM workflow_runs
            WHERE project_id = $1
            GROUP BY status
            """,
            pid,
        )
        counts: dict[str, int] = {row["status"]: row["cnt"] for row in rows}
        total = sum(counts.values())

        return QueueStats(
            pending=counts.get("pending", 0),
            running=counts.get("running", 0),
            completed=counts.get("completed", 0),
            failed=counts.get("failed", 0),
            waiting=counts.get("waiting", 0),
            total=total,
        )

    # ------------------------------------------------------------------
    # Control operations
    # ------------------------------------------------------------------

    async def terminate_execution(self, run_id: str) -> bool:
        """Force-cancel a stuck run.

        Args:
            run_id: The workflow run ID to terminate.

        Returns:
            True if the run was successfully cancelled.
        """
        if hasattr(self._store, "cancel_run"):
            # cancel_run expects (workflow_name, run_id); look up name first
            if hasattr(self._store, "get_run_by_run_id"):
                row = await self._store.get_run_by_run_id(run_id)
                if row is None:
                    return False
                return await self._store.cancel_run(row["workflow_name"], run_id)

        # Fallback: raw UPDATE if we have a pool
        if hasattr(self._store, "_pool"):
            result = await self._store._pool.execute(
                """
                UPDATE workflow_runs
                SET status = 'cancelled', updated_at = NOW()
                WHERE run_id = $1 AND status IN ('pending', 'running')
                """,
                run_id,
            )
            return result.endswith("1")

        logger.warning("terminate_execution not supported by this store")
        return False

    async def retry_execution(self, run_id: str, *, project_id: str | None = None) -> str:
        """Re-enqueue a failed run as PENDING with a new run_id.

        Copies the input data from the original run and creates a new
        execution with ``-retry-N`` appended to the run_id.

        Args:
            run_id: The failed run ID to retry.
            project_id: Project identifier for multi-tenant isolation.

        Returns:
            The new run_id for the retried execution.

        Raises:
            ValueError: If the original run is not found or is not failed.
        """
        if not hasattr(self._store, "get_run_by_run_id"):
            raise ValueError("retry_execution requires a store with get_run_by_run_id")

        row = await self._store.get_run_by_run_id(run_id)
        if row is None:
            raise ValueError(f"Run not found: {run_id}")
        if row["status"] != "failed":
            raise ValueError(f"Can only retry failed runs (current status: {row['status']})")

        # Determine retry suffix
        base_id = run_id.split("-retry-")[0]
        retry_num = 1
        if "-retry-" in run_id:
            try:
                retry_num = int(run_id.rsplit("-retry-", 1)[1]) + 1
            except (ValueError, IndexError):
                retry_num = int(time.time()) % 10000

        new_run_id = f"{base_id}-retry-{retry_num}"
        workflow_name = row["workflow_name"]
        input_data = row.get("input", {})

        new_run = WorkflowRun(
            workflow_name=workflow_name,
            run_id=new_run_id,
            status=StepStatus.PENDING,
            input_data=input_data,
            started_at=None,
        )

        pid = resolve_project_id(project_id)
        if hasattr(self._store, "enqueue_run"):
            await self._store.enqueue_run(
                new_run,
                input_data=input_data if isinstance(input_data, dict) else {},
                steps_total=row.get("steps_total"),
                project_id=pid,
            )
        else:
            await self._store.save_run(new_run)

        logger.info(
            "Retried run %s as %s for workflow %s",
            run_id,
            new_run_id,
            workflow_name,
        )
        return new_run_id

    async def signal_execution(self, run_id: str, signal_name: str, data: Any) -> bool:
        """Inject data into a WAITING workflow.

        Stores the signal in the run's signals dict and marks any WAITING
        steps back to PENDING so the workflow resumes on next claim.

        Args:
            run_id: The workflow run ID.
            signal_name: Name of the signal (matched by the step).
            data: Arbitrary data payload for the signal.

        Returns:
            True if the signal was delivered successfully.
        """
        if not hasattr(self._store, "get_run_by_run_id"):
            logger.warning("signal_execution requires a store with get_run_by_run_id")
            return False

        row = await self._store.get_run_by_run_id(run_id)
        if row is None:
            return False

        workflow_name = row["workflow_name"]

        # Load the full WorkflowRun to manipulate signals and steps
        wf_run = await self._store.load_run(workflow_name, run_id)
        if wf_run is None:
            return False

        wf_run.signals[signal_name] = data

        # Mark WAITING steps as PENDING so the workflow resumes
        for step in wf_run.steps.values():
            if step.status == StepStatus.WAITING:
                step.status = StepStatus.PENDING

        wf_run.status = StepStatus.PENDING
        await self._store.save_run(wf_run)

        logger.info(
            "Delivered signal '%s' to run %s (workflow %s)",
            signal_name,
            run_id,
            workflow_name,
        )
        return True
