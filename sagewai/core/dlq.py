# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Dead Letter Queue (DLQ) for failed workflow runs.

Failed workflows are moved to the DLQ for inspection, manual retry,
or discard. This prevents failed runs from cluttering the main queue
and provides a structured recovery path.

Usage::

    from sagewai.core.dlq import DeadLetterQueue
    from sagewai.core.stores.postgres import PostgresStore

    store = PostgresStore(database_url="postgresql://localhost/sagewai")
    dlq = DeadLetterQueue(store=store)

    # Move failed run to DLQ
    await dlq.move_to_dlq("my-workflow", "run-123", "Step 'research' failed: timeout")

    # List DLQ entries
    entries = await dlq.list_entries()

    # Retry from DLQ
    new_run_id = await dlq.retry("run-123")

    # Purge old entries
    await dlq.purge(older_than_days=30)
"""

# Required table schema (apply via Alembic migration):
#
#   CREATE TABLE IF NOT EXISTS workflow_dlq (
#       id SERIAL PRIMARY KEY,
#       run_id TEXT NOT NULL,
#       workflow_name TEXT NOT NULL,
#       input_data JSONB DEFAULT '{}',
#       error TEXT NOT NULL,
#       original_data JSONB DEFAULT '{}',
#       retry_count INTEGER DEFAULT 0,
#       created_at TIMESTAMPTZ DEFAULT NOW()
#   );
#   CREATE INDEX IF NOT EXISTS idx_workflow_dlq_workflow
#       ON workflow_dlq(workflow_name);
#   CREATE INDEX IF NOT EXISTS idx_workflow_dlq_created
#       ON workflow_dlq(created_at);

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from sagewai.core.context import resolve_project_id

logger = logging.getLogger(__name__)


@dataclass
class DLQEntry:
    """A dead letter queue entry."""

    id: int
    run_id: str
    workflow_name: str
    input_data: dict[str, Any]
    error: str
    original_data: dict[str, Any]
    retry_count: int = 0
    created_at: str = ""


class DeadLetterQueue:
    """Dead Letter Queue manager for failed workflow runs."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def move_to_dlq(
        self,
        workflow_name: str,
        run_id: str,
        error: str,
        *,
        project_id: str | None = None,
    ) -> int:
        """Move a failed run to the DLQ. Returns DLQ entry ID."""
        pid = resolve_project_id(project_id)
        # Load the failed run data
        run_data = await self._store.get_run_by_run_id(run_id)
        if run_data is None:
            raise ValueError(f"Run not found: {run_id}")

        input_data = run_data.get("input", {})
        original_data = run_data.get("data", {})

        entry_id = await self._store._pool.fetchval(
            """
            INSERT INTO workflow_dlq
                (run_id, workflow_name, input_data, error,
                 original_data, retry_count, project_id)
            VALUES ($1, $2, $3::jsonb, $4, $5::jsonb, 0, $6)
            RETURNING id
            """,
            run_id,
            workflow_name,
            json.dumps(input_data, default=str),
            error,
            json.dumps(original_data, default=str),
            pid,
        )
        logger.info("Moved run %s to DLQ (entry %d)", run_id, entry_id)
        return entry_id

    async def list_entries(
        self,
        *,
        workflow_name: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DLQEntry]:
        """List DLQ entries with optional filter."""
        pid = resolve_project_id(project_id)
        conditions = ["project_id = $3"]
        params: list[Any] = [limit, offset, pid]
        idx = 4
        if workflow_name:
            conditions.append(f"workflow_name = ${idx}")
            params.append(workflow_name)
            idx += 1

        where = " AND ".join(conditions)
        rows = await self._store._pool.fetch(
            f"""
            SELECT id, run_id, workflow_name, input_data, error,
                   original_data, retry_count, created_at
            FROM workflow_dlq
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            *params,
        )
        return [self._row_to_entry(row) for row in rows]

    async def get_entry(
        self, run_id: str, *, project_id: str | None = None
    ) -> DLQEntry | None:
        """Get a DLQ entry by run_id, scoped to project."""
        pid = resolve_project_id(project_id)
        row = await self._store._pool.fetchrow(
            """
            SELECT id, run_id, workflow_name, input_data, error,
                   original_data, retry_count, created_at
            FROM workflow_dlq
            WHERE run_id = $1 AND project_id = $2
            """,
            run_id,
            pid,
        )
        if row is None:
            return None
        return self._row_to_entry(row)

    async def retry(
        self, run_id: str, *, priority: int = 0, project_id: str | None = None
    ) -> str:
        """Re-enqueue a DLQ entry as a new PENDING run.

        Returns new run_id.
        """
        pid = resolve_project_id(project_id)
        entry = await self.get_entry(run_id, project_id=pid)
        if entry is None:
            raise ValueError(f"DLQ entry not found: {run_id}")

        # Generate new run_id
        new_run_id = f"{run_id}-retry-{entry.retry_count + 1}"

        # Increment retry count
        await self._store._pool.execute(
            "UPDATE workflow_dlq SET retry_count = retry_count + 1 "
            "WHERE run_id = $1 AND project_id = $2",
            run_id,
            pid,
        )

        # Re-enqueue
        from sagewai.core.state import WorkflowRun

        wf_run = WorkflowRun(
            workflow_name=entry.workflow_name,
            run_id=new_run_id,
            input_data=entry.input_data,
            started_at=time.time(),
        )
        await self._store.enqueue_run(
            wf_run,
            input_data=entry.input_data,
            priority=priority,
            project_id=pid,
        )
        logger.info(
            "Retried DLQ entry %s as %s (attempt %d)",
            run_id,
            new_run_id,
            entry.retry_count + 1,
        )
        return new_run_id

    async def discard(
        self, run_id: str, *, project_id: str | None = None
    ) -> bool:
        """Remove a DLQ entry permanently, scoped to project."""
        pid = resolve_project_id(project_id)
        result = await self._store._pool.execute(
            "DELETE FROM workflow_dlq WHERE run_id = $1 AND project_id = $2",
            run_id,
            pid,
        )
        return result.endswith("1")

    async def purge(
        self, *, older_than_days: int = 30, project_id: str | None = None
    ) -> int:
        """Purge DLQ entries older than N days. Returns count deleted."""
        pid = resolve_project_id(project_id)
        result = await self._store._pool.execute(
            """
            DELETE FROM workflow_dlq
            WHERE project_id = $2
              AND created_at < NOW() - MAKE_INTERVAL(days => $1)
            """,
            older_than_days,
            pid,
        )
        count = int(result.split()[-1]) if result else 0
        if count > 0:
            logger.info(
                "Purged %d DLQ entries older than %d days",
                count,
                older_than_days,
            )
        return count

    async def count(self, *, project_id: str | None = None) -> int:
        """Count total DLQ entries, optionally scoped to a project."""
        pid = resolve_project_id(project_id)
        return (
            await self._store._pool.fetchval(
                "SELECT COUNT(*) FROM workflow_dlq WHERE project_id = $1",
                pid,
            )
            or 0
        )

    @staticmethod
    def _row_to_entry(row: Any) -> DLQEntry:
        """Convert a database row to a DLQEntry."""
        input_data = row["input_data"]
        if isinstance(input_data, str):
            input_data = json.loads(input_data)
        original_data = row["original_data"]
        if isinstance(original_data, str):
            original_data = json.loads(original_data)
        return DLQEntry(
            id=row["id"],
            run_id=row["run_id"],
            workflow_name=row["workflow_name"],
            input_data=input_data,
            error=row["error"],
            original_data=original_data,
            retry_count=row["retry_count"],
            created_at=str(row["created_at"]),
        )
