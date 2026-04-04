# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""WorkerLoadBalancer — load-aware worker assignment for workflow runs.

Assigns workflow runs to workers at enqueue time based on the selected
routing strategy. For DIRECT routing, no assignment is made and workers
self-select at claim time via pool/label/id filters.

Usage::

    from sagewai.core.load_balancer import WorkerLoadBalancer
    from sagewai.models.worker import RoutingConstraints, RoutingStrategy

    balancer = WorkerLoadBalancer(store=postgres_store)

    # Auto-assign to least-loaded worker
    worker_id = await balancer.assign()

    # Assign within a specific pool using round-robin
    worker_id = await balancer.assign(
        RoutingConstraints(
            worker_pool="cloud-gpt4",
            strategy=RoutingStrategy.ROUND_ROBIN,
        )
    )
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from sagewai.core.context import resolve_project_id
from sagewai.models.worker import RoutingConstraints, RoutingStrategy

if TYPE_CHECKING:
    from sagewai.core.stores.postgres import PostgresStore

logger = logging.getLogger(__name__)


class WorkerLoadBalancer:
    """Assigns workflow runs to workers based on routing strategy.

    For ``ROUND_ROBIN``, ``LEAST_LOADED``, and ``THRESHOLD`` strategies,
    the balancer queries the ``workers`` table (joined with active run
    counts from ``workflow_runs``) to pick the best worker.

    For ``DIRECT`` strategy, returns ``None`` — the existing claim-time
    pool/label/id filters handle worker selection.

    Parameters
    ----------
    store:
        PostgresStore with access to both ``workers`` and ``workflow_runs``
        tables.
    """

    def __init__(self, store: PostgresStore) -> None:
        self._store = store
        self._rr_counters: dict[str, int] = {}

    async def assign(
        self,
        constraints: RoutingConstraints | None = None,
        project_id: str | None = None,
    ) -> str | None:
        """Pick a target worker_id based on the routing strategy.

        Returns ``None`` when no assignment should be made at enqueue
        time (``DIRECT`` strategy, or no eligible workers found).

        Parameters
        ----------
        constraints:
            Routing constraints from the submission. Defaults to
            ``LEAST_LOADED`` with no pool/label filters.
        project_id:
            Project scope for worker lookup.
        """
        if constraints is None:
            constraints = RoutingConstraints()

        strategy = constraints.strategy

        # If any explicit target is set, infer DIRECT strategy
        if constraints.worker_id:
            return constraints.worker_id

        if strategy == RoutingStrategy.DIRECT:
            return None

        # Fetch eligible workers with their load
        workers = await self._get_eligible_workers(
            pool=constraints.worker_pool,
            labels=constraints.worker_labels,
            project_id=project_id,
        )

        if not workers:
            logger.debug(
                "No eligible workers found for pool=%s labels=%s; "
                "falling back to claim-time routing",
                constraints.worker_pool,
                constraints.worker_labels,
            )
            return None

        if strategy == RoutingStrategy.ROUND_ROBIN:
            return self._round_robin(workers, constraints.worker_pool or "__all__")

        if strategy == RoutingStrategy.LEAST_LOADED:
            return self._least_loaded(workers)

        if strategy == RoutingStrategy.THRESHOLD:
            return self._threshold(workers, constraints.capacity_threshold)

        return None

    def _round_robin(self, workers: list[dict[str, Any]], pool_key: str) -> str | None:
        """Rotate through workers in order."""
        if not workers:
            return None
        idx = self._rr_counters.get(pool_key, 0) % len(workers)
        self._rr_counters[pool_key] = idx + 1
        return workers[idx]["worker_id"]

    def _least_loaded(self, workers: list[dict[str, Any]]) -> str | None:
        """Pick the worker with the lowest load ratio."""
        if not workers:
            return None
        best = min(workers, key=lambda w: w["load_ratio"])
        return best["worker_id"]

    def _threshold(self, workers: list[dict[str, Any]], threshold: float) -> str | None:
        """Pick the least-loaded worker below the capacity threshold.

        Falls back to least_loaded if all workers exceed the threshold.
        """
        if not workers:
            return None
        below = [w for w in workers if w["load_ratio"] < threshold]
        if below:
            return self._least_loaded(below)
        # All workers above threshold — best-effort fallback
        logger.warning(
            "All %d workers above capacity threshold %.1f; " "falling back to least-loaded",
            len(workers),
            threshold,
        )
        return self._least_loaded(workers)

    async def _get_eligible_workers(
        self,
        *,
        pool: str | None = None,
        labels: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query workers with their current load ratio.

        Returns a list of dicts with keys:
        - worker_id
        - pool
        - max_concurrent
        - active_runs
        - load_ratio (active_runs / max_concurrent)
        """
        if not hasattr(self._store, "_pool") or self._store._pool is None:
            return []

        pid = resolve_project_id(project_id)

        conditions = [
            "w.status = 'active'",
            "w.last_heartbeat > NOW() - INTERVAL '5 minutes'",
            "w.project_id = $1",
        ]
        params: list[Any] = [pid]
        idx = 2

        if pool is not None:
            conditions.append(f"w.pool = ${idx}")
            params.append(pool)
            idx += 1

        if labels:
            conditions.append(f"w.labels @> ${idx}::jsonb")
            params.append(json.dumps(labels))
            idx += 1

        where = " AND ".join(conditions)

        rows = await self._store._pool.fetch(
            f"""
            SELECT
                w.worker_id,
                w.pool,
                w.max_concurrent,
                COALESCE(r.active_runs, 0) AS active_runs,
                CASE
                    WHEN w.max_concurrent > 0
                    THEN COALESCE(r.active_runs, 0)::float / w.max_concurrent
                    ELSE 1.0
                END AS load_ratio
            FROM workers w
            LEFT JOIN (
                SELECT owner_id, COUNT(*) AS active_runs
                FROM workflow_runs
                WHERE status = 'running'
                GROUP BY owner_id
            ) r ON r.owner_id = w.worker_id
            WHERE {where}
            ORDER BY load_ratio ASC, w.last_heartbeat DESC
            """,
            *params,
        )

        return [
            {
                "worker_id": row["worker_id"],
                "pool": row["pool"],
                "max_concurrent": row["max_concurrent"],
                "active_runs": row["active_runs"],
                "load_ratio": float(row["load_ratio"]),
            }
            for row in rows
        ]
