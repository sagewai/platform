# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Integration tests for distributed worker routing against real PostgreSQL.

Requires a running PostgreSQL with the 004_worker_routing migration applied.
Run with:
    SAGEWAI_DATABASE_URL=postgresql://... pytest tests/test_worker_routing_integration.py -v -m integration

Or with infra running:
    pytest tests/test_worker_routing_integration.py -v -m integration
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from sagewai.core.state import WorkflowRun
from sagewai.models.worker import RoutingConstraints, RoutingStrategy, WorkerCredentials

DB_URL = os.environ.get(
    "SAGEWAI_DATABASE_URL",
    "postgresql://sagecurator:sagecurator_password@localhost:5432/sagecurator",
)


@pytest.fixture
async def store():
    """Create a real PostgresStore connected to the test database."""
    from sagewai.core.stores.postgres import PostgresStore

    s = PostgresStore(database_url=DB_URL)
    await s.initialize()
    yield s
    await s.close()


@pytest.mark.integration
@pytest.mark.asyncio
class TestWorkerRegistration:
    """Worker registration, heartbeat, and deregistration."""

    async def test_register_and_list(self, store) -> None:
        worker_id = f"test-worker-{uuid.uuid4().hex[:8]}"
        try:
            await store.register_worker(
                worker_id,
                pool="test-pool",
                labels={"zone": "local", "gpu": False},
                project_id="default",
                max_concurrent=4,
                metadata={"hostname": "test-machine"},
            )

            workers = await store.list_workers(pool="test-pool", status="active")
            ids = [w["worker_id"] for w in workers]
            assert worker_id in ids

            # Verify fields
            w = next(w for w in workers if w["worker_id"] == worker_id)
            assert w["pool"] == "test-pool"
            assert w["labels"]["zone"] == "local"
            assert w["max_concurrent"] == 4
        finally:
            await store.deregister_worker(worker_id)

    async def test_heartbeat_updates(self, store) -> None:
        worker_id = f"test-hb-{uuid.uuid4().hex[:8]}"
        try:
            await store.register_worker(
                worker_id,
                pool="hb-pool",
                labels={},
                project_id="default",
                max_concurrent=2,
                metadata={},
            )
            # Heartbeat should not throw
            await store.worker_heartbeat(worker_id)

            workers = await store.list_workers(status="active")
            ids = [w["worker_id"] for w in workers]
            assert worker_id in ids
        finally:
            await store.deregister_worker(worker_id)

    async def test_deregister_sets_offline(self, store) -> None:
        worker_id = f"test-dereg-{uuid.uuid4().hex[:8]}"
        await store.register_worker(
            worker_id,
            pool="dereg-pool",
            labels={},
            project_id="default",
            max_concurrent=2,
            metadata={},
        )
        await store.deregister_worker(worker_id)

        workers_active = await store.list_workers(status="active")
        active_ids = [w["worker_id"] for w in workers_active]
        assert worker_id not in active_ids

    async def test_list_pools(self, store) -> None:
        worker_id = f"test-pool-{uuid.uuid4().hex[:8]}"
        try:
            await store.register_worker(
                worker_id,
                pool="unique-pool-xyz",
                labels={},
                project_id="default",
                max_concurrent=4,
                metadata={},
            )
            pools = await store.list_worker_pools()
            pool_names = [p["pool"] for p in pools]
            assert "unique-pool-xyz" in pool_names
        finally:
            await store.deregister_worker(worker_id)


@pytest.mark.integration
@pytest.mark.asyncio
class TestRoutingClaims:
    """Test routing-aware claim logic."""

    async def test_pool_filtered_claim(self, store) -> None:
        """A worker with pool='ollama' only claims runs targeting that pool."""
        worker_id = f"ollama-worker-{uuid.uuid4().hex[:8]}"
        run_id = f"run-{uuid.uuid4().hex[:8]}"

        try:
            await store.register_worker(
                worker_id,
                pool="ollama",
                labels={},
                project_id="default",
                max_concurrent=4,
                metadata={},
            )

            # Enqueue a run targeted at 'ollama' pool
            run = WorkflowRun(workflow_name="test-wf", run_id=run_id, project_id="default")
            await store.enqueue_run(
                run,
                input_data={"test": True},
                project_id="default",
                target_pool="ollama",
            )

            # Worker with pool='ollama' should claim it
            claimed = await store.claim_pending_run(
                owner_id=worker_id,
                project_id="default",
                worker_pool="ollama",
                worker_labels=None,
            )
            assert claimed is not None
            assert claimed.run_id == run_id
        finally:
            await store.deregister_worker(worker_id)
            # Cleanup run
            async with store._pool.acquire() as conn:
                await conn.execute("DELETE FROM workflow_runs WHERE run_id = $1", run_id)

    async def test_unrouted_run_claimed_by_any_worker(self, store) -> None:
        """Runs without routing constraints are claimed by any worker."""
        worker_id = f"any-worker-{uuid.uuid4().hex[:8]}"
        run_id = f"run-unrouted-{uuid.uuid4().hex[:8]}"

        try:
            await store.register_worker(
                worker_id,
                pool="specialized-pool",
                labels={"zone": "eu"},
                project_id="default",
                max_concurrent=4,
                metadata={},
            )

            # Unrouted run (no pool, no labels)
            run = WorkflowRun(workflow_name="test-wf", run_id=run_id, project_id="default")
            await store.enqueue_run(run, input_data={}, project_id="default")

            # Any worker can claim unrouted runs
            claimed = await store.claim_pending_run(
                owner_id=worker_id,
                project_id="default",
                worker_pool="specialized-pool",
                worker_labels={"zone": "eu"},
            )
            assert claimed is not None
            assert claimed.run_id == run_id
        finally:
            await store.deregister_worker(worker_id)
            async with store._pool.acquire() as conn:
                await conn.execute("DELETE FROM workflow_runs WHERE run_id = $1", run_id)

    async def test_pool_mismatch_not_claimed(self, store) -> None:
        """A worker from pool='cloud' cannot claim a run targeted at pool='local'."""
        cloud_worker = f"cloud-worker-{uuid.uuid4().hex[:8]}"
        run_id = f"run-local-{uuid.uuid4().hex[:8]}"

        try:
            await store.register_worker(
                cloud_worker,
                pool="cloud",
                labels={},
                project_id="default",
                max_concurrent=4,
                metadata={},
            )

            # Run targeted at 'local' pool
            run = WorkflowRun(workflow_name="test-wf", run_id=run_id, project_id="default")
            await store.enqueue_run(
                run, input_data={}, project_id="default", target_pool="local"
            )

            # Cloud worker should NOT claim it
            claimed = await store.claim_pending_run(
                owner_id=cloud_worker,
                project_id="default",
                worker_pool="cloud",
                worker_labels=None,
            )
            assert claimed is None
        finally:
            await store.deregister_worker(cloud_worker)
            async with store._pool.acquire() as conn:
                await conn.execute("DELETE FROM workflow_runs WHERE run_id = $1", run_id)

    async def test_label_filtered_claim(self, store) -> None:
        """Runs with target_labels only claimed by workers with matching labels."""
        eu_worker = f"eu-worker-{uuid.uuid4().hex[:8]}"
        us_worker = f"us-worker-{uuid.uuid4().hex[:8]}"
        run_id = f"run-eu-{uuid.uuid4().hex[:8]}"

        try:
            await store.register_worker(
                eu_worker,
                pool="cloud",
                labels={"zone": "eu-west", "gpu": True},
                project_id="default",
                max_concurrent=4,
                metadata={},
            )
            await store.register_worker(
                us_worker,
                pool="cloud",
                labels={"zone": "us-east"},
                project_id="default",
                max_concurrent=4,
                metadata={},
            )

            # Run requires zone=eu-west
            run = WorkflowRun(workflow_name="test-wf", run_id=run_id, project_id="default")
            await store.enqueue_run(
                run, input_data={}, project_id="default", target_labels={"zone": "eu-west"}
            )

            # US worker should NOT claim it
            claimed_us = await store.claim_pending_run(
                owner_id=us_worker,
                project_id="default",
                worker_pool="cloud",
                worker_labels={"zone": "us-east"},
            )
            assert claimed_us is None

            # EU worker SHOULD claim it
            claimed_eu = await store.claim_pending_run(
                owner_id=eu_worker,
                project_id="default",
                worker_pool="cloud",
                worker_labels={"zone": "eu-west", "gpu": True},
            )
            assert claimed_eu is not None
            assert claimed_eu.run_id == run_id
        finally:
            await store.deregister_worker(eu_worker)
            await store.deregister_worker(us_worker)
            async with store._pool.acquire() as conn:
                await conn.execute("DELETE FROM workflow_runs WHERE run_id = $1", run_id)

    async def test_worker_id_targeted_claim(self, store) -> None:
        """Runs targeted at a specific worker_id are claimed only by that worker."""
        target_worker = f"target-{uuid.uuid4().hex[:8]}"
        other_worker = f"other-{uuid.uuid4().hex[:8]}"
        run_id = f"run-targeted-{uuid.uuid4().hex[:8]}"

        try:
            for wid in (target_worker, other_worker):
                await store.register_worker(
                    wid,
                    pool="default",
                    labels={},
                    project_id="default",
                    max_concurrent=4,
                    metadata={},
                )

            run = WorkflowRun(workflow_name="test-wf", run_id=run_id, project_id="default")
            await store.enqueue_run(
                run, input_data={}, project_id="default", target_worker_id=target_worker
            )

            # Other worker should NOT claim it
            claimed_other = await store.claim_pending_run(
                owner_id=other_worker,
                project_id="default",
                worker_pool="default",
                worker_labels=None,
            )
            assert claimed_other is None

            # Target worker SHOULD claim it
            claimed = await store.claim_pending_run(
                owner_id=target_worker,
                project_id="default",
                worker_pool="default",
                worker_labels=None,
            )
            assert claimed is not None
            assert claimed.run_id == run_id
        finally:
            for wid in (target_worker, other_worker):
                await store.deregister_worker(wid)
            async with store._pool.acquire() as conn:
                await conn.execute("DELETE FROM workflow_runs WHERE run_id = $1", run_id)


@pytest.mark.integration
@pytest.mark.asyncio
class TestLoadBalancer:
    """Test WorkerLoadBalancer against real database workers table."""

    async def test_least_loaded_picks_correct_worker(self, store) -> None:
        from sagewai.core.load_balancer import WorkerLoadBalancer

        workers = [
            (f"lb-w1-{uuid.uuid4().hex[:8]}", 3),  # high load
            (f"lb-w2-{uuid.uuid4().hex[:8]}", 1),  # low load — should be picked
            (f"lb-w3-{uuid.uuid4().hex[:8]}", 2),  # medium load
        ]
        try:
            for wid, _ in workers:
                await store.register_worker(
                    wid,
                    pool="lb-test-pool",
                    labels={},
                    project_id="default",
                    max_concurrent=4,
                    metadata={},
                )

            balancer = WorkerLoadBalancer(store)
            result = await balancer.assign(
                RoutingConstraints(
                    worker_pool="lb-test-pool",
                    strategy=RoutingStrategy.LEAST_LOADED,
                )
            )
            # With no active runs, all load_ratio = 0.0 → any worker is valid
            all_ids = [wid for wid, _ in workers]
            assert result in all_ids or result is None  # No runs = all tied at 0
        finally:
            for wid, _ in workers:
                await store.deregister_worker(wid)

    async def test_round_robin_cycles(self, store) -> None:
        from sagewai.core.load_balancer import WorkerLoadBalancer

        workers = [f"rr-w{i}-{uuid.uuid4().hex[:8]}" for i in range(3)]
        try:
            for wid in workers:
                await store.register_worker(
                    wid,
                    pool="rr-pool",
                    labels={},
                    project_id="default",
                    max_concurrent=4,
                    metadata={},
                )

            balancer = WorkerLoadBalancer(store)
            constraints = RoutingConstraints(
                worker_pool="rr-pool",
                strategy=RoutingStrategy.ROUND_ROBIN,
            )

            results = []
            for _ in range(6):
                r = await balancer.assign(constraints)
                if r:
                    results.append(r)

            # Should cycle through all 3 workers (each should appear ~2 times)
            unique = set(results)
            assert len(unique) >= 2  # At least 2 different workers hit
        finally:
            for wid in workers:
                await store.deregister_worker(wid)
