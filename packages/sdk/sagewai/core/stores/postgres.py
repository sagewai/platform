# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PostgreSQL-backed workflow store using asyncpg.

Stores WorkflowRun as JSONB documents in the workflow_runs table.
Uses raw asyncpg queries for performance (no ORM overhead).

Usage::

    store = PostgresStore(database_url="postgresql://localhost/sagewai")
    await store.initialize()  # creates connection pool

    wf = DurableWorkflow(name="pipeline", store=store)
"""

from __future__ import annotations

import json
import logging
import os
import platform
from typing import Any

from sagewai.core.context import resolve_project_id
from sagewai.core.state import QueueFullError, StepStatus, WorkflowRun, WorkflowStore

logger = logging.getLogger(__name__)


def _owner_id() -> str:
    """Generate a process identifier for fencing."""
    return f"{platform.node()}:{os.getpid()}"


class PostgresStore(WorkflowStore):
    """PostgreSQL-backed WorkflowStore using asyncpg.

    Parameters
    ----------
    database_url:
        PostgreSQL connection string (asyncpg format).
    pool:
        Existing asyncpg connection pool. If provided, database_url is ignored.
    """

    def __init__(
        self,
        database_url: str | None = None,
        pool: Any = None,
    ) -> None:
        self._database_url = database_url
        self._pool = pool
        self._owner_id = _owner_id()
        self._load_balancer: Any = None  # lazily created, cached for RR counter
        self._emitted_unroutable: set[str] = set()  # dedup: (run_id, hour_bucket)

    async def initialize(self) -> None:
        """Create the connection pool if not already provided."""
        if self._pool is not None:
            return
        if self._database_url is None:
            raise ValueError("Either database_url or pool must be provided")
        try:
            import asyncpg
        except ImportError as exc:
            raise ImportError(
                "asyncpg is required for PostgresStore. Install with: uv add asyncpg"
            ) from exc
        self._pool = await asyncpg.create_pool(self._database_url, min_size=2, max_size=10)
        logger.info("PostgresStore initialized with pool (owner=%s)", self._owner_id)

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()

    async def save_run(self, run: WorkflowRun) -> None:
        """Upsert a workflow run as JSONB plus typed sandbox requirement columns."""
        key = f"{run.workflow_name}:{run.run_id}"
        data = json.dumps(run.to_dict(), default=str)

        await self._pool.execute(
            """
            INSERT INTO workflow_runs (
                id, workflow_name, run_id, status, data, owner_id, updated_at,
                execution_mode,
                requires_sandbox_mode, requires_image, requires_variant,
                requires_network_policy,
                security_profile_ref, effective_env_keys, effective_secret_keys,
                revoked_at, revoke_reason,
                artifact_destination,
                replay_of_run_id, replay_from_step, code_hash,
                directive_chain, estimated_cost_usd,
                replay_re_evaluate_directives, execution_mode_override,
                identity_from
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, NOW(), $7, $8, $9, $10, $11, $12, $13,
                    $14, $15, $16, $17::jsonb, $18, $19, $20,
                    $21::jsonb, $22, $23, $24, $25)
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                data = EXCLUDED.data,
                owner_id = EXCLUDED.owner_id,
                updated_at = NOW(),
                execution_mode = EXCLUDED.execution_mode,
                requires_sandbox_mode = EXCLUDED.requires_sandbox_mode,
                requires_image = EXCLUDED.requires_image,
                requires_variant = EXCLUDED.requires_variant,
                requires_network_policy = EXCLUDED.requires_network_policy,
                security_profile_ref = EXCLUDED.security_profile_ref,
                effective_env_keys = EXCLUDED.effective_env_keys,
                effective_secret_keys = EXCLUDED.effective_secret_keys,
                revoked_at = EXCLUDED.revoked_at,
                revoke_reason = EXCLUDED.revoke_reason,
                artifact_destination = EXCLUDED.artifact_destination,
                replay_of_run_id = EXCLUDED.replay_of_run_id,
                replay_from_step = EXCLUDED.replay_from_step,
                code_hash = EXCLUDED.code_hash,
                directive_chain = EXCLUDED.directive_chain,
                estimated_cost_usd = EXCLUDED.estimated_cost_usd,
                replay_re_evaluate_directives = EXCLUDED.replay_re_evaluate_directives,
                execution_mode_override = EXCLUDED.execution_mode_override,
                identity_from = EXCLUDED.identity_from
            """,
            key,
            run.workflow_name,
            run.run_id,
            run.status.value,
            data,
            self._owner_id,
            run.execution_mode.value,
            run.requires_sandbox_mode.value,
            run.requires_image,
            run.requires_variant.value if run.requires_variant else None,
            run.requires_network_policy.value,
            run.security_profile_ref,
            run.effective_env_keys,
            run.effective_secret_keys,
            run.revoked_at,
            run.revoke_reason,
            (
                json.dumps(run.artifact_destination.model_dump(mode="json"))
                if run.artifact_destination
                else None
            ),
            run.replay_of_run_id,
            run.replay_from_step,
            run.code_hash,
            json.dumps(
                [e.model_dump(mode="json") for e in run.directive_chain],
                default=str,
            ),
            run.estimated_cost_usd,
            run.replay_re_evaluate_directives,
            run.execution_mode_override.value if run.execution_mode_override else None,
            run.identity_from,
        )

    async def load_run(self, workflow_name: str, run_id: str) -> WorkflowRun | None:
        """Load a workflow run by name and ID."""
        key = f"{workflow_name}:{run_id}"
        row = await self._pool.fetchrow(
            "SELECT data FROM workflow_runs WHERE id = $1",
            key,
        )
        if row is None:
            return None
        data = row["data"]
        if isinstance(data, str):
            data = json.loads(data)
        return WorkflowRun.from_dict(data)

    async def list_runs(
        self,
        workflow_name: str,
        status: StepStatus | None = None,
        project_id: str | None = None,
    ) -> list[WorkflowRun]:
        """List runs for a workflow, optionally filtered by status and project."""
        pid = resolve_project_id(project_id)
        conditions = ["workflow_name = $1", "project_id = $2"]
        params: list[Any] = [workflow_name, pid]
        idx = 3
        if status is not None:
            conditions.append(f"status = ${idx}")
            params.append(status.value)
            idx += 1

        where = " AND ".join(conditions)
        rows = await self._pool.fetch(
            f"SELECT data FROM workflow_runs WHERE {where} ORDER BY created_at DESC",
            *params,
        )
        results = []
        for row in rows:
            data = row["data"]
            if isinstance(data, str):
                data = json.loads(data)
            results.append(WorkflowRun.from_dict(data))
        return results

    async def list_replays_of(self, run_id: str) -> list[WorkflowRun]:
        """Return all runs whose replay_of_run_id equals the given id.

        Uses the idx_workflow_runs_replay_of partial index from migration 006.
        """
        rows = await self._pool.fetch(
            """
            SELECT data FROM workflow_runs
            WHERE replay_of_run_id = $1
            ORDER BY created_at ASC NULLS LAST
            """,
            run_id,
        )
        results = []
        for row in rows:
            data = row["data"]
            if isinstance(data, str):
                data = json.loads(data)
            results.append(WorkflowRun.from_dict(data))
        return results

    async def recover_stale_runs(
        self,
        stale_timeout_seconds: int = 300,
        project_id: str | None = None,
    ) -> list[WorkflowRun]:
        """Find RUNNING workflows that haven't been updated within the timeout."""
        pid = resolve_project_id(project_id)
        rows = await self._pool.fetch(
            """
            SELECT data FROM workflow_runs
            WHERE status = $1
              AND project_id = $3
              AND updated_at < NOW() - MAKE_INTERVAL(secs => $2)
            ORDER BY updated_at ASC
            """,
            StepStatus.RUNNING.value,
            float(stale_timeout_seconds),
            pid,
        )
        results = []
        for row in rows:
            data = row["data"]
            if isinstance(data, str):
                data = json.loads(data)
            results.append(WorkflowRun.from_dict(data))
        return results

    async def heartbeat(self, workflow_name: str, run_id: str) -> None:
        """Refresh updated_at to prevent stale detection."""
        key = f"{workflow_name}:{run_id}"
        await self._pool.execute(
            "UPDATE workflow_runs SET updated_at = NOW() WHERE id = $1",
            key,
        )

    # ------------------------------------------------------------------
    # Queue operations — atomic enqueue, claim, complete, fail
    # ------------------------------------------------------------------

    async def queue_depth(self, project_id: str | None = None) -> int:
        """Count pending workflow runs, optionally scoped to a project."""
        pid = resolve_project_id(project_id)
        return await self._pool.fetchval(
            "SELECT COUNT(*) FROM workflow_runs "
            "WHERE status = 'pending' AND project_id = $1",
            pid,
        ) or 0

    async def enqueue_run(
        self,
        run: WorkflowRun,
        input_data: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        steps_total: int | None = None,
        priority: int = 0,
        project_id: str | None = None,
        max_queue_depth: int | None = None,
        target_pool: str | None = None,
        target_labels: dict[str, Any] | None = None,
        target_worker_id: str | None = None,
    ) -> tuple[str, bool]:
        """Atomically enqueue a workflow run. Returns (run_id, is_new).

        If ``idempotency_key`` already exists, returns the existing run_id
        with ``is_new=False`` — no duplicate execution.

        Parameters
        ----------
        priority:
            Higher values are dequeued first (default 0).
        project_id:
            Optional project identifier for multi-tenant isolation.
        max_queue_depth:
            If set, raises ``QueueFullError`` when the number of pending
            runs meets or exceeds this limit.
        target_pool:
            Target worker pool — only workers in this pool can claim.
        target_labels:
            Required worker labels (JSONB containment match).
        target_worker_id:
            Target a specific worker by ID.
        """
        if max_queue_depth is not None:
            depth = await self.queue_depth()
            if depth >= max_queue_depth:
                raise QueueFullError(depth, max_queue_depth)
        key = f"{run.workflow_name}:{run.run_id}"
        data = json.dumps(run.to_dict(), default=str)
        input_json = json.dumps(input_data or {}, default=str)
        labels_json = json.dumps(target_labels) if target_labels else None

        if idempotency_key:
            result = await self._pool.fetchval(
                """
                INSERT INTO workflow_runs
                    (id, workflow_name, run_id, status, data, input,
                     idempotency_key, steps_total, priority,
                     project_id, target_pool, target_labels, target_worker_id,
                     updated_at)
                VALUES ($1, $2, $3, 'pending', $4::jsonb, $5::jsonb,
                        $6, $7, $8, $9, $10, $11::jsonb, $12, NOW())
                ON CONFLICT (idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                DO NOTHING
                RETURNING run_id
                """,
                key,
                run.workflow_name,
                run.run_id,
                data,
                input_json,
                idempotency_key,
                steps_total,
                priority,
                project_id,
                target_pool,
                labels_json,
                target_worker_id,
            )
            if result is None:
                existing = await self._pool.fetchval(
                    "SELECT run_id FROM workflow_runs "
                    "WHERE idempotency_key = $1",
                    idempotency_key,
                )
                return existing or run.run_id, False
            return run.run_id, True
        else:
            await self._pool.execute(
                """
                INSERT INTO workflow_runs
                    (id, workflow_name, run_id, status, data, input,
                     steps_total, priority, project_id,
                     target_pool, target_labels, target_worker_id,
                     updated_at)
                VALUES ($1, $2, $3, 'pending', $4::jsonb, $5::jsonb,
                        $6, $7, $8, $9, $10::jsonb, $11, NOW())
                ON CONFLICT (id) DO NOTHING
                """,
                key,
                run.workflow_name,
                run.run_id,
                data,
                input_json,
                steps_total,
                priority,
                project_id,
                target_pool,
                labels_json,
                target_worker_id,
            )
            return run.run_id, True

    async def claim_pending_run(
        self,
        owner_id: str,
        project_id: str | None = None,
        *,
        worker_pool: str | None = None,
        worker_labels: dict[str, Any] | None = None,
        models_canonical: list[str] | None = None,
    ) -> WorkflowRun | None:
        """Atomically claim the highest-priority PENDING run.

        Returns None if queue is empty.
        Uses ``FOR UPDATE SKIP LOCKED`` to avoid contention between workers.
        Priority is checked first (higher = more urgent), then FIFO by
        creation time.

        Routing filters (Temporal-style task queue matching):

        - ``project_id``: only claim runs belonging to this project.
        - ``worker_pool``: only claim runs targeting this pool (or unrouted).
        - ``worker_labels``: only claim runs whose required labels are a
          subset of the worker's labels (JSONB ``<@`` containment).
        - ``models_canonical``: only claim runs whose ``target_model``
          is in this list (or runs with no ``target_model``).

        A run with ``target_pool IS NULL`` is claimable by any worker.
        A run with ``target_labels IS NULL`` matches any worker's labels.
        A run with ``target_model IS NULL`` is claimable by any worker.
        This preserves backward compatibility.
        """
        conditions = ["status = 'pending'"]
        params: list[Any] = [owner_id]
        idx = 2

        if project_id is not None:
            conditions.append(f"project_id = ${idx}")
            params.append(project_id)
            idx += 1

        # Pool matching: unrouted runs (NULL) match any worker
        if worker_pool is not None:
            conditions.append(
                f"(target_pool IS NULL OR target_pool = ${idx})"
            )
            params.append(worker_pool)
            idx += 1

        # Label matching: unrouted runs (NULL) match any worker
        if worker_labels:
            conditions.append(
                f"(target_labels IS NULL OR target_labels <@ ${idx}::jsonb)"
            )
            params.append(json.dumps(worker_labels))
            idx += 1

        # Worker ID matching: unrouted runs (NULL) match any worker
        conditions.append(
            f"(target_worker_id IS NULL OR target_worker_id = ${idx})"
        )
        params.append(owner_id)
        idx += 1

        # Model matching: runs with no target_model match any worker;
        # runs with target_model only match workers that support it.
        if models_canonical:
            conditions.append(
                f"(target_model IS NULL OR target_model = ANY(${idx}::text[]))"
            )
            params.append(models_canonical)
            idx += 1

        where = " AND ".join(conditions)
        row = await self._pool.fetchrow(
            f"""
            UPDATE workflow_runs
            SET status = 'running', owner_id = $1, updated_at = NOW()
            WHERE id = (
                SELECT id FROM workflow_runs
                WHERE {where}
                ORDER BY COALESCE(priority, 0) DESC, created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING workflow_name, run_id, data, input, steps_total
            """,
            *params,
        )
        if row is None:
            return None

        data = row["data"]
        if isinstance(data, str):
            data = json.loads(data)
        wf_run = WorkflowRun.from_dict(data)
        wf_run.status = StepStatus.RUNNING
        # Attach queue metadata
        wf_run._input = row["input"] if isinstance(row["input"], dict) else json.loads(row["input"] or "{}")
        wf_run._steps_total = row["steps_total"]
        return wf_run

    async def claim_task(
        self,
        worker_id: str,
        org_id: str,
        models_canonical: list[str],
        pool: str,
        labels: dict[str, str] | None,
        *,
        worker_sandbox_mode: Any = None,
        worker_sandbox_variants: list[Any] | None = None,
        worker_network_policy: Any = None,
    ) -> dict[str, Any] | None:
        """Atomically claim a pending task for a fleet worker.

        Implements the :class:`~sagewai.fleet.dispatcher.TaskStore` protocol
        and extends it with three sandbox capability predicates so that only
        workers that can satisfy the run's sandbox requirements will claim it.

        Sandbox matching rules
        ----------------------
        1. ``sandbox_mode_rank(requires_sandbox_mode) <= worker_mode_rank``
           — the worker must support at least the required isolation level.
        2. ``requires_variant IS NULL OR requires_variant = ANY(worker_variants)``
           — NULL means BYO (matches any variant-capable worker); a specific
           variant must appear in the worker's advertised list.
        3. ``network_policy_rank(requires_network_policy) <= worker_network_rank``
           — the worker must support at least the required network policy.
        """
        from sagewai.sandbox.models import NetworkPolicy, SandboxImageVariant, SandboxMode
        from sagewai.sandbox.registry import mode_rank
        from sagewai.sandbox.registry import network_policy_rank as net_rank

        # Resolve defaults so callers can pass None for backward compat
        if worker_sandbox_mode is None:
            worker_sandbox_mode = SandboxMode.NONE
        if worker_network_policy is None:
            worker_network_policy = NetworkPolicy.NONE

        worker_mode_rank_int = mode_rank(worker_sandbox_mode)
        worker_network_rank_int = net_rank(worker_network_policy)
        worker_variants_str: list[str] = [
            v.value if isinstance(v, SandboxImageVariant) else str(v)
            for v in (worker_sandbox_variants or [])
        ]

        conditions = ["status = 'pending'"]
        params: list[Any] = [worker_id]
        idx = 2

        # org_id scoping
        conditions.append(f"(org_id IS NULL OR org_id = ${idx})")
        params.append(org_id)
        idx += 1

        # Pool matching: unrouted runs match any worker
        conditions.append(f"(target_pool IS NULL OR target_pool = ${idx})")
        params.append(pool)
        idx += 1

        # Label matching: unrouted runs match any worker
        if labels:
            conditions.append(f"(target_labels IS NULL OR target_labels <@ ${idx}::jsonb)")
            params.append(json.dumps(labels))
            idx += 1

        # Model matching
        if models_canonical:
            conditions.append(
                f"(target_model IS NULL OR target_model = ANY(${idx}::text[]))"
            )
            params.append(models_canonical)
            idx += 1

        # Sandbox mode rank: worker must meet or exceed the task's required rank
        conditions.append(f"sandbox_mode_rank(requires_sandbox_mode) <= ${idx}")
        params.append(worker_mode_rank_int)
        idx += 1

        # Variant matching: NULL means BYO (universal), otherwise must be in worker list
        conditions.append(
            f"(requires_variant IS NULL OR requires_variant = ANY(${idx}::text[]))"
        )
        params.append(worker_variants_str)
        idx += 1

        # Network policy rank: worker must meet or exceed the task's required rank
        conditions.append(f"network_policy_rank(requires_network_policy) <= ${idx}")
        params.append(worker_network_rank_int)
        idx += 1

        where = " AND ".join(conditions)
        row = await self._pool.fetchrow(
            f"""
            UPDATE workflow_runs
            SET status = 'running', owner_id = $1, updated_at = NOW()
            WHERE id = (
                SELECT id FROM workflow_runs
                WHERE {where}
                ORDER BY COALESCE(priority, 0) DESC, created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING run_id, workflow_name, data, input, output
            """,
            *params,
        )
        if row is None:
            return None

        inp = row["input"]
        return {
            "run_id": row["run_id"],
            "workflow_name": row["workflow_name"],
            "payload": inp if isinstance(inp, dict) else json.loads(inp or "{}"),
        }

    async def complete_run(
        self,
        workflow_name: str,
        run_id: str,
        output: dict[str, Any],
        data: dict[str, Any] | None = None,
        steps_completed: int | None = None,
    ) -> None:
        """Mark a run as completed with output."""
        key = f"{workflow_name}:{run_id}"
        output_json = json.dumps(output, default=str)
        data_json = json.dumps(data, default=str) if data else None

        if data_json:
            await self._pool.execute(
                """
                UPDATE workflow_runs
                SET status = 'completed', output = $2::jsonb, data = $3::jsonb,
                    steps_completed = COALESCE($4, steps_completed), updated_at = NOW()
                WHERE id = $1
                """,
                key,
                output_json,
                data_json,
                steps_completed,
            )
        else:
            await self._pool.execute(
                """
                UPDATE workflow_runs
                SET status = 'completed', output = $2::jsonb,
                    steps_completed = COALESCE($3, steps_completed), updated_at = NOW()
                WHERE id = $1
                """,
                key,
                output_json,
                steps_completed,
            )

    async def fail_run(
        self,
        workflow_name: str,
        run_id: str,
        error: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Mark a run as failed with error message."""
        key = f"{workflow_name}:{run_id}"
        data_json = json.dumps(data, default=str) if data else None

        if data_json:
            await self._pool.execute(
                """
                UPDATE workflow_runs
                SET status = 'failed', error = $2, data = $3::jsonb, updated_at = NOW()
                WHERE id = $1
                """,
                key,
                error,
                data_json,
            )
        else:
            await self._pool.execute(
                """
                UPDATE workflow_runs
                SET status = 'failed', error = $2, updated_at = NOW()
                WHERE id = $1
                """,
                key,
                error,
            )

    async def cancel_run(self, workflow_name: str, run_id: str) -> bool:
        """Cancel a pending or running workflow. Returns True if cancelled."""
        key = f"{workflow_name}:{run_id}"
        result = await self._pool.execute(
            """
            UPDATE workflow_runs
            SET status = 'cancelled', updated_at = NOW()
            WHERE id = $1 AND status IN ('pending', 'running')
            """,
            key,
        )
        return result.endswith("1")  # "UPDATE 1"

    async def reset_stale_to_pending(self, stale_timeout_seconds: int = 300) -> int:
        """Reset stale RUNNING workflows to PENDING for re-claim.

        Returns the number of runs reset.
        """
        result = await self._pool.execute(
            """
            UPDATE workflow_runs
            SET status = 'pending', owner_id = NULL, updated_at = NOW()
            WHERE status = 'running'
              AND updated_at < NOW() - MAKE_INTERVAL(secs => $1)
            """,
            float(stale_timeout_seconds),
        )
        count = int(result.split()[-1]) if result else 0
        if count > 0:
            logger.info("Reset %d stale workflow runs to PENDING", count)
        return count

    async def update_steps_completed(
        self, workflow_name: str, run_id: str, steps_completed: int
    ) -> None:
        """Update the step progress counter."""
        key = f"{workflow_name}:{run_id}"
        await self._pool.execute(
            """
            UPDATE workflow_runs
            SET steps_completed = $2, updated_at = NOW()
            WHERE id = $1
            """,
            key,
            steps_completed,
        )

    # ------------------------------------------------------------------
    # Event persistence
    # ------------------------------------------------------------------

    async def persist_event(
        self, run_id: str, event_type: str, data: dict[str, Any]
    ) -> None:
        """Persist a workflow event to the workflow_events table."""
        data_json = json.dumps(data, default=str)
        await self._pool.execute(
            """
            INSERT INTO workflow_events (run_id, event_type, data)
            VALUES ($1, $2, $3::jsonb)
            """,
            run_id,
            event_type,
            data_json,
        )

    async def list_events(self, run_id: str) -> list[dict[str, Any]]:
        """List all events for a workflow run, ordered by creation."""
        rows = await self._pool.fetch(
            """
            SELECT id, run_id, event_type, data, created_at
            FROM workflow_events
            WHERE run_id = $1
            ORDER BY id ASC
            """,
            run_id,
        )
        results = []
        for row in rows:
            data = row["data"]
            if isinstance(data, str):
                data = json.loads(data)
            results.append({
                "id": row["id"],
                "run_id": row["run_id"],
                "event_type": row["event_type"],
                "data": data,
                "created_at": str(row["created_at"]),
            })
        return results

    # ------------------------------------------------------------------
    # Query operations — list all runs, get by run_id
    # ------------------------------------------------------------------

    async def list_all_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        search: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List runs across all workflows with queue metadata.

        Parameters
        ----------
        project_id:
            If provided, only return runs belonging to this project.
        """
        conditions: list[str] = []
        params: list[Any] = [limit, offset]
        idx = 3

        if status:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1
        if search:
            conditions.append(f"workflow_name ILIKE ${idx}")
            params.append(f"%{search}%")
            idx += 1
        if project_id is not None:
            conditions.append(f"project_id = ${idx}")
            params.append(project_id)
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT id, workflow_name, run_id, status, input, output, error,
                   steps_completed, steps_total, created_at, updated_at
            FROM workflow_runs
            {where}
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
        """
        rows = await self._pool.fetch(query, *params)
        return [self._row_to_dict(row) for row in rows]

    async def get_run_by_run_id(self, run_id: str) -> dict[str, Any] | None:
        """Load a run by run_id (without requiring workflow_name)."""
        row = await self._pool.fetchrow(
            """
            SELECT id, workflow_name, run_id, status, data, input, output, error,
                   steps_completed, steps_total, owner_id, created_at, updated_at
            FROM workflow_runs
            WHERE run_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            run_id,
        )
        if row is None:
            return None
        result = self._row_to_dict(row)
        # Include full step data
        data = row["data"]
        if isinstance(data, str):
            data = json.loads(data)
        result["data"] = data
        return result

    # ------------------------------------------------------------------
    # Manual dispatch + queue stats
    # ------------------------------------------------------------------

    async def enqueue_workflow(
        self,
        workflow_name: str,
        input_data: dict[str, Any] | None = None,
        *,
        run_id: str | None = None,
        priority: int = 0,
        idempotency_key: str | None = None,
        project_id: str | None = None,
        max_queue_depth: int | None = None,
        worker_pool: str | None = None,
        worker_labels: dict[str, Any] | None = None,
        worker_id: str | None = None,
        routing_strategy: str | None = None,
        capacity_threshold: float = 0.9,
        execution_mode: Any = None,
        requires_sandbox_mode: Any = None,
        requires_image: str | None = None,
        requires_network_policy: Any = None,
    ) -> tuple[str, bool]:
        """Enqueue a workflow for execution by a WorkflowWorker.

        Convenience method that creates a WorkflowRun and enqueues it.
        Supports routing constraints for directing work to specific workers.

        Parameters
        ----------
        project_id:
            Optional project identifier for multi-tenant isolation.
            Like Temporal's namespace — workers register for a project
            and only claim runs from that project.
        max_queue_depth:
            If set, raises ``QueueFullError`` when the number of pending
            runs meets or exceeds this limit.
        worker_pool:
            Target worker pool (like a Temporal task queue).
        worker_labels:
            Required worker labels (JSONB containment match).
        worker_id:
            Target a specific worker by ID.
        routing_strategy:
            Load-balancing strategy: "direct", "round_robin",
            "least_loaded" (default), or "threshold".
        capacity_threshold:
            For "threshold" strategy — skip workers above this ratio.

        Returns (run_id, is_new).
        """
        import time

        from sagewai.models.worker import RoutingConstraints, RoutingStrategy

        run_id = run_id or (
            f"wf-{int(time.time())}-{id(input_data) % 10000:04d}"
        )

        # Resolve load-balanced assignment if strategy requires it
        target_worker_id = worker_id
        if routing_strategy and routing_strategy != "direct" and not worker_id:
            try:
                from sagewai.core.load_balancer import WorkerLoadBalancer

                if self._load_balancer is None:
                    self._load_balancer = WorkerLoadBalancer(self)
                constraints = RoutingConstraints(
                    worker_pool=worker_pool,
                    worker_labels=worker_labels,
                    strategy=RoutingStrategy(routing_strategy),
                    capacity_threshold=capacity_threshold,
                )
                target_worker_id = await self._load_balancer.assign(
                    constraints, project_id=project_id
                )
            except Exception:
                logger.warning(
                    "Load balancer assignment failed; falling back to "
                    "claim-time routing",
                    exc_info=True,
                )

        # Resolve sandbox requirements cascade
        from sagewai.core.state import ExecutionMode, sandbox_mode_for
        from sagewai.sandbox.resolution import resolve_requirements

        project_defaults = None
        if project_id:
            try:
                project_defaults = await self.get_project_defaults(project_id)
            except Exception:
                project_defaults = None

        # Normalise execution_mode (accept enum or str). Default to BARE (Mode 0).
        if execution_mode is None:
            exec_mode = ExecutionMode.BARE
        elif isinstance(execution_mode, ExecutionMode):
            exec_mode = execution_mode
        else:
            exec_mode = ExecutionMode(execution_mode)

        # If caller didn't pin a sandbox mode explicitly, derive from execution_mode.
        derived_mode = (
            requires_sandbox_mode
            if requires_sandbox_mode is not None
            else sandbox_mode_for(exec_mode)
        )

        requirements = await resolve_requirements(
            explicit_mode=derived_mode,
            explicit_image=requires_image,
            explicit_network_policy=requires_network_policy,
            project_defaults=project_defaults,
        )

        wf_run = WorkflowRun(
            workflow_name=workflow_name,
            run_id=run_id,
            input_data=input_data,
            started_at=time.time(),
            project_id=project_id,
            execution_mode=exec_mode,
            requires_sandbox_mode=requirements.sandbox_mode,
            requires_image=requirements.image,
            requires_variant=requirements.variant,
            requires_network_policy=requirements.network_policy,
        )
        return await self.enqueue_run(
            wf_run,
            input_data=input_data,
            idempotency_key=idempotency_key,
            priority=priority,
            project_id=project_id,
            max_queue_depth=max_queue_depth,
            target_pool=worker_pool,
            target_labels=worker_labels if worker_labels else None,
            target_worker_id=target_worker_id,
        )

    async def count_by_status(
        self, project_id: str | None = None
    ) -> dict[str, int]:
        """Count workflow runs by status, optionally scoped to a project."""
        pid = resolve_project_id(project_id)
        rows = await self._pool.fetch(
            "SELECT status, COUNT(*) as cnt "
            "FROM workflow_runs WHERE project_id = $1 GROUP BY status",
            pid,
        )
        return {row["status"]: row["cnt"] for row in rows}

    async def get_active_workers(
        self, stale_timeout_seconds: int = 120
    ) -> list[dict[str, Any]]:
        """Get workers with recent heartbeats."""
        rows = await self._pool.fetch(
            """
            SELECT owner_id, COUNT(*) as active_runs,
                   MAX(updated_at) as last_heartbeat
            FROM workflow_runs
            WHERE status = 'running'
              AND owner_id IS NOT NULL
              AND updated_at > NOW() - MAKE_INTERVAL(secs => $1)
            GROUP BY owner_id
            """,
            float(stale_timeout_seconds),
        )
        return [
            {
                "owner_id": row["owner_id"],
                "active_runs": row["active_runs"],
                "last_heartbeat": str(row["last_heartbeat"]),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Worker registry — registration, heartbeat, query
    # ------------------------------------------------------------------

    async def register_worker(
        self,
        worker_id: str,
        *,
        pool: str = "default",
        labels: dict[str, Any] | None = None,
        project_id: str | None = None,
        max_concurrent: int = 4,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register or update a worker in the workers table.

        Called by ``WorkflowWorker.start()`` on startup and periodically
        during heartbeat to keep the worker visible.
        """
        pid = resolve_project_id(project_id)
        labels_json = json.dumps(labels or {})
        meta_json = json.dumps(metadata or {})

        await self._pool.execute(
            """
            INSERT INTO workers
                (worker_id, pool, labels, project_id, status,
                 max_concurrent, last_heartbeat, registered_at, metadata)
            VALUES ($1, $2, $3::jsonb, $4, 'active', $5, NOW(), NOW(), $6::jsonb)
            ON CONFLICT (worker_id) DO UPDATE SET
                pool = EXCLUDED.pool,
                labels = EXCLUDED.labels,
                project_id = EXCLUDED.project_id,
                status = 'active',
                max_concurrent = EXCLUDED.max_concurrent,
                last_heartbeat = NOW(),
                metadata = EXCLUDED.metadata
            """,
            worker_id,
            pool,
            labels_json,
            pid,
            max_concurrent,
            meta_json,
        )

    async def deregister_worker(self, worker_id: str) -> None:
        """Mark a worker as offline (called on graceful shutdown)."""
        await self._pool.execute(
            "UPDATE workers SET status = 'offline', last_heartbeat = NOW() "
            "WHERE worker_id = $1",
            worker_id,
        )

    async def worker_heartbeat(self, worker_id: str) -> None:
        """Update the worker's last_heartbeat timestamp."""
        await self._pool.execute(
            "UPDATE workers SET last_heartbeat = NOW() WHERE worker_id = $1",
            worker_id,
        )

    async def list_workers(
        self,
        *,
        pool: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List registered workers with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if pool is not None:
            conditions.append(f"pool = ${idx}")
            params.append(pool)
            idx += 1
        if status is not None:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1
        if project_id is not None:
            conditions.append(f"project_id = ${idx}")
            params.append(project_id)
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = await self._pool.fetch(
            f"""
            SELECT w.worker_id, w.pool, w.labels, w.project_id, w.status,
                   w.max_concurrent, w.last_heartbeat, w.registered_at, w.metadata,
                   COALESCE(r.active_runs, 0) AS active_runs
            FROM workers w
            LEFT JOIN (
                SELECT owner_id, COUNT(*) AS active_runs
                FROM workflow_runs WHERE status = 'running'
                GROUP BY owner_id
            ) r ON r.owner_id = w.worker_id
            {where}
            ORDER BY w.last_heartbeat DESC
            """,
            *params,
        )
        results = []
        for row in rows:
            labels = row["labels"]
            if isinstance(labels, str):
                labels = json.loads(labels)
            meta = row["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta)
            results.append({
                "worker_id": row["worker_id"],
                "pool": row["pool"],
                "labels": labels,
                "project_id": row["project_id"],
                "status": row["status"],
                "max_concurrent": row["max_concurrent"],
                "active_runs": row["active_runs"],
                "last_heartbeat": str(row["last_heartbeat"]),
                "registered_at": str(row["registered_at"]),
                "metadata": meta,
            })
        return results

    async def list_worker_pools(
        self, *, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List worker pools with worker counts and status summary."""
        conditions = ["status = 'active'"]
        params: list[Any] = []
        idx = 1

        if project_id is not None:
            conditions.append(f"project_id = ${idx}")
            params.append(project_id)
            idx += 1

        where = " AND ".join(conditions)
        rows = await self._pool.fetch(
            f"""
            SELECT pool, COUNT(*) AS worker_count,
                   SUM(max_concurrent) AS total_capacity
            FROM workers
            WHERE {where}
            GROUP BY pool
            ORDER BY pool
            """,
            *params,
        )
        return [
            {
                "pool": row["pool"],
                "worker_count": row["worker_count"],
                "total_capacity": row["total_capacity"],
            }
            for row in rows
        ]

    async def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        """Fetch a single worker by ID. Returns None if not found."""
        row = await self._pool.fetchrow(
            """
            SELECT w.worker_id, w.pool, w.labels, w.project_id, w.status,
                   w.max_concurrent, w.last_heartbeat, w.registered_at, w.metadata,
                   COALESCE(r.active_runs, 0) AS active_runs
            FROM workers w
            LEFT JOIN (
                SELECT owner_id, COUNT(*) AS active_runs
                FROM workflow_runs WHERE status = 'running'
                GROUP BY owner_id
            ) r ON r.owner_id = w.worker_id
            WHERE w.worker_id = $1
            """,
            worker_id,
        )
        if row is None:
            return None
        labels = row["labels"]
        if isinstance(labels, str):
            labels = json.loads(labels)
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        return {
            "worker_id": row["worker_id"],
            "pool": row["pool"],
            "labels": labels,
            "project_id": row["project_id"],
            "status": row["status"],
            "max_concurrent": row["max_concurrent"],
            "active_runs": row["active_runs"],
            "last_heartbeat": str(row["last_heartbeat"]),
            "registered_at": str(row["registered_at"]),
            "metadata": meta,
        }

    # ------------------------------------------------------------------
    # Project defaults
    # ------------------------------------------------------------------

    async def get_project_defaults(self, project_id: str):
        """Return project-level SandboxRequirements defaults, or None if unset.

        Reads admin-state.json (file-backed config store). When the admin UI
        surface ships in Plan 3b, this helper stays compatible — the UI
        writes to the same store.
        """
        from sagewai.admin.state_file import AdminStateFile
        from sagewai.sandbox import image_manifest
        from sagewai.sandbox.models import NetworkPolicy, SandboxMode
        from sagewai.sandbox.resolution import SandboxRequirements

        state = AdminStateFile()
        project = state.get_project(project_id)
        if not project:
            return None
        defaults = project.get("default_sandbox_requirements")
        if not defaults:
            return None

        mode_str = defaults.get("sandbox_mode", "none")
        image = defaults.get(
            "image",
            f"ghcr.io/sagewai/sandbox-base:{image_manifest.SDK_VERSION}",
        )
        policy_str = defaults.get("network_policy", "none")

        return SandboxRequirements(
            sandbox_mode=SandboxMode(mode_str),
            image=image,
            variant=image_manifest.lookup_variant(image),
            network_policy=NetworkPolicy(policy_str),
        )

    # ------------------------------------------------------------------
    # Unroutable-run sweep
    # ------------------------------------------------------------------

    async def sweep_unroutable_runs(
        self, *, grace_seconds: int = 30, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Find stuck PENDING runs and emit sagewai.run.unroutable events.

        Dedup key: (run_id, current_hour). Each run emits at most one event
        per hour, even across multiple sweep cycles.

        Returns the list of newly-emitted event dicts (empty if none).
        Disabled via SAGEWAI_SANDBOX_EMIT_UNROUTABLE_EVENTS=0.
        """
        import datetime
        import os

        if os.environ.get("SAGEWAI_SANDBOX_EMIT_UNROUTABLE_EVENTS", "1") != "1":
            return []

        rows = await self._pool.fetch(
            """
            SELECT run_id, workflow_name, org_id, target_pool,
                   requires_sandbox_mode, requires_image, requires_variant,
                   requires_network_policy,
                   EXTRACT(EPOCH FROM (NOW() - created_at))::INT AS pending_seconds
            FROM workflow_runs
            WHERE status = 'pending'
              AND created_at < NOW() - MAKE_INTERVAL(secs => $1)
            ORDER BY created_at ASC
            LIMIT $2
            """,
            float(grace_seconds),
            limit,
        )

        hour_bucket = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d-%H")
        emitted: list[dict[str, Any]] = []
        for row in rows:
            dedup_key = f"{row['run_id']}:{hour_bucket}"
            if dedup_key in self._emitted_unroutable:
                continue
            self._emitted_unroutable.add(dedup_key)

            diagnosis = await self._diagnose_unroutable(row)

            logger.info(
                "sagewai.run.unroutable: run_id=%s reason=%s pending_seconds=%d "
                "required=%s diagnosis=%s",
                row["run_id"],
                "no_capable_worker",
                row["pending_seconds"],
                {
                    "sandbox_mode": row["requires_sandbox_mode"],
                    "variant": row["requires_variant"],
                    "network_policy": row["requires_network_policy"],
                    "pool": row["target_pool"],
                },
                diagnosis,
            )
            emitted.append({
                "run_id": row["run_id"],
                "diagnosis": diagnosis,
                "pending_seconds": row["pending_seconds"],
            })
        return emitted

    async def _diagnose_unroutable(self, row: Any) -> str:
        """Best-effort explanation of why the row doesn't match any worker."""
        candidates = await self._pool.fetchval(
            "SELECT count(*) FROM workers WHERE status='active' AND pool=$1",
            row["target_pool"],
        )
        if not candidates:
            return f"no active workers in pool '{row['target_pool']}'"

        if row["requires_variant"] is not None:
            variant_match = await self._pool.fetchval(
                """
                SELECT count(*) FROM workers
                WHERE status='active' AND pool=$1
                  AND $2 = ANY(string_to_array(labels->>'sandbox.image_variants', ','))
                """,
                row["target_pool"],
                row["requires_variant"],
            )
            if not variant_match:
                return (
                    f"no worker in pool '{row['target_pool']}' advertises "
                    f"variant={row['requires_variant']}; candidate count={candidates}"
                )

        return (
            f"mode or network policy mismatch "
            f"(pool '{row['target_pool']}' has {candidates} workers)"
        )

    # ------------------------------------------------------------------
    # Revoked-stuck-run recovery sweep
    # ------------------------------------------------------------------

    async def recover_revoked_stuck_runs(self) -> int:
        """Find runs with revoked_at set + still status='running' and abort them.

        Used to recover from worker crashes between seeing revoked_at and
        completing the abort. Idempotent.
        """
        rows = await self._pool.fetch(
            """
            UPDATE workflow_runs
            SET status = 'failed',
                updated_at = NOW(),
                output = COALESCE(output, '{}'::jsonb)
                              || '{"error": "secret_revoked_recovered"}'::jsonb
            WHERE status = 'running' AND revoked_at IS NOT NULL
            RETURNING run_id
            """,
        )
        # Audit emit best-effort per recovered row
        if rows:
            try:
                from sagewai.sealed.audit import AuditWriter

                writer = AuditWriter(self)
                for r in rows:
                    await writer.emit(
                        event_type="run.aborted_by_revocation",
                        run_id=r["run_id"],
                        details={"recovery": True},
                    )
            except Exception:
                pass
        return len(rows)

    # ------------------------------------------------------------------
    # Sealed audit retention sweep
    # ------------------------------------------------------------------

    async def sealed_audit_cleanup(self, *, retention_days: int | None = None) -> int:
        """Delete sealed_audit_events older than the retention threshold.

        retention_days: explicit override; when None, reads from
            AdminStateFile().get_sealed_config()["audit_retention_days"]
            (default 365).

        Returns the count of rows deleted. Emits one
        ``audit_retention_cleanup`` audit event when count > 0 so the
        deletion itself is auditable.

        Intended to be invoked daily by the admin/scheduler process.
        Idempotent — safe to call repeatedly.
        """
        from sagewai.admin.state_file import AdminStateFile
        from sagewai.sealed.audit import AuditWriter

        if retention_days is None:
            state = AdminStateFile()
            retention_days = state.get_sealed_config().get("audit_retention_days", 365)

        deleted = await self._pool.fetchval(
            """
            WITH d AS (
                DELETE FROM sealed_audit_events
                WHERE created_at < NOW() - MAKE_INTERVAL(days => $1)
                RETURNING id
            )
            SELECT count(*) FROM d
            """,
            float(retention_days),
        )

        deleted_count = int(deleted or 0)
        if deleted_count:
            await AuditWriter(self).emit(
                event_type="audit_retention_cleanup",
                details={
                    "deleted_count": deleted_count,
                    "retention_days": retention_days,
                },
            )
        return deleted_count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Convert a database row to a dict."""
        result: dict[str, Any] = {
            "id": row["id"],
            "workflow_name": row["workflow_name"],
            "run_id": row["run_id"],
            "status": row["status"],
            "steps_completed": row["steps_completed"],
            "steps_total": row["steps_total"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
        # Include input/output/error if present in the row
        if "input" in row.keys():
            inp = row["input"]
            result["input"] = json.loads(inp) if isinstance(inp, str) else inp
        if "output" in row.keys():
            out = row["output"]
            result["output"] = json.loads(out) if isinstance(out, str) else out
        if "error" in row.keys():
            result["error"] = row["error"]
        return result
