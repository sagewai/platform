# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for sandbox-aware claim_task SQL match."""
import os
import uuid

import pytest

from sagewai.sandbox.models import (
    NetworkPolicy,
    SandboxImageVariant,
    SandboxMode,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAGEWAI_DATABASE_URL"),
    reason="SAGEWAI_DATABASE_URL not set",
)


@pytest.fixture
async def dispatcher_store():
    from sagewai.core.stores.postgres import PostgresStore

    store = PostgresStore(database_url=os.environ["SAGEWAI_DATABASE_URL"])
    await store.initialize()
    # Clean workflow_runs to avoid cross-test interference
    await store._pool.execute("DELETE FROM workflow_runs")
    try:
        yield store
    finally:
        await store._pool.execute("DELETE FROM workflow_runs")
        await store.close()


@pytest.fixture
async def enqueue_run(dispatcher_store):
    from sagewai.core.state import WorkflowRun

    async def _enqueue(
        *,
        mode: SandboxMode = SandboxMode.PER_RUN,
        variant: SandboxImageVariant | None = SandboxImageVariant.BASE,
        network: NetworkPolicy = NetworkPolicy.NONE,
        org: str = "org1",
        pool: str = "default",
    ):
        run = WorkflowRun(
            workflow_name="wf",
            run_id=uuid.uuid4().hex[:12],
            requires_sandbox_mode=mode,
            requires_variant=variant,
            requires_network_policy=network,
        )
        await dispatcher_store.save_run(run)
        # Make it claimable: status=pending, populate dispatch fields.
        await dispatcher_store._pool.execute(
            "UPDATE workflow_runs SET status='pending', org_id=$1, target_pool=$2 "
            "WHERE run_id=$3",
            org, pool, run.run_id,
        )
        return run.run_id

    return _enqueue


@pytest.mark.asyncio
async def test_claim_per_run_worker_claims_per_run_task(dispatcher_store, enqueue_run):
    run_id = await enqueue_run(
        mode=SandboxMode.PER_RUN,
        variant=SandboxImageVariant.ML,
        network=NetworkPolicy.NONE,
    )
    task = await dispatcher_store.claim_task(
        worker_id="w1",
        org_id="org1",
        models_canonical=[],
        pool="default",
        labels=None,
        worker_sandbox_mode=SandboxMode.PER_RUN,
        worker_sandbox_variants=[SandboxImageVariant.ML],
        worker_network_policy=NetworkPolicy.FULL,
    )
    assert task is not None
    assert task["run_id"] == run_id


@pytest.mark.asyncio
async def test_claim_mode_rank_permissive(dispatcher_store, enqueue_run):
    """per_worker worker claims per_run task (rank ≥)."""
    run_id = await enqueue_run(mode=SandboxMode.PER_RUN)
    task = await dispatcher_store.claim_task(
        worker_id="w1", org_id="org1", models_canonical=[], pool="default", labels=None,
        worker_sandbox_mode=SandboxMode.PER_WORKER,
        worker_sandbox_variants=[SandboxImageVariant.BASE],
        worker_network_policy=NetworkPolicy.FULL,
    )
    assert task is not None
    assert task["run_id"] == run_id


@pytest.mark.asyncio
async def test_claim_mode_rank_insufficient(dispatcher_store, enqueue_run):
    """none worker cannot claim per_run task."""
    await enqueue_run(mode=SandboxMode.PER_RUN)
    task = await dispatcher_store.claim_task(
        worker_id="w1", org_id="org1", models_canonical=[], pool="default", labels=None,
        worker_sandbox_mode=SandboxMode.NONE,
        worker_sandbox_variants=[SandboxImageVariant.BASE],
        worker_network_policy=NetworkPolicy.FULL,
    )
    assert task is None


@pytest.mark.asyncio
async def test_claim_variant_match(dispatcher_store, enqueue_run):
    run_id = await enqueue_run(variant=SandboxImageVariant.ML)
    task = await dispatcher_store.claim_task(
        worker_id="w1", org_id="org1", models_canonical=[], pool="default", labels=None,
        worker_sandbox_mode=SandboxMode.PER_RUN,
        worker_sandbox_variants=[SandboxImageVariant.BASE, SandboxImageVariant.ML],
        worker_network_policy=NetworkPolicy.FULL,
    )
    assert task is not None
    assert task["run_id"] == run_id


@pytest.mark.asyncio
async def test_claim_variant_mismatch_skips(dispatcher_store, enqueue_run):
    await enqueue_run(variant=SandboxImageVariant.OPS)
    task = await dispatcher_store.claim_task(
        worker_id="w1", org_id="org1", models_canonical=[], pool="default", labels=None,
        worker_sandbox_mode=SandboxMode.PER_RUN,
        worker_sandbox_variants=[SandboxImageVariant.BASE, SandboxImageVariant.ML],
        worker_network_policy=NetworkPolicy.FULL,
    )
    assert task is None


@pytest.mark.asyncio
async def test_claim_byo_variant_universal(dispatcher_store, enqueue_run):
    """requires_variant=NULL (BYO) matches any worker with compatible mode+net."""
    run_id = await enqueue_run(variant=None)
    task = await dispatcher_store.claim_task(
        worker_id="w1", org_id="org1", models_canonical=[], pool="default", labels=None,
        worker_sandbox_mode=SandboxMode.PER_RUN,
        worker_sandbox_variants=[SandboxImageVariant.BASE],
        worker_network_policy=NetworkPolicy.FULL,
    )
    assert task is not None
    assert task["run_id"] == run_id


@pytest.mark.asyncio
async def test_claim_network_rank_insufficient(dispatcher_store, enqueue_run):
    await enqueue_run(network=NetworkPolicy.FULL)
    task = await dispatcher_store.claim_task(
        worker_id="w1", org_id="org1", models_canonical=[], pool="default", labels=None,
        worker_sandbox_mode=SandboxMode.PER_RUN,
        worker_sandbox_variants=[SandboxImageVariant.BASE],
        worker_network_policy=NetworkPolicy.NONE,
    )
    assert task is None


@pytest.mark.asyncio
async def test_claim_egress_allowlist_stays_pending(dispatcher_store, enqueue_run):
    """No worker advertises egress_allowlist yet (Plan 3d pending)."""
    await enqueue_run(network=NetworkPolicy.EGRESS_ALLOWLIST)
    task = await dispatcher_store.claim_task(
        worker_id="w1", org_id="org1", models_canonical=[], pool="default", labels=None,
        worker_sandbox_mode=SandboxMode.PER_RUN,
        worker_sandbox_variants=[SandboxImageVariant.BASE],
        worker_network_policy=NetworkPolicy.NONE,
    )
    assert task is None


@pytest.mark.asyncio
async def test_unroutable_event_emitted_once_per_hour_per_run(
    dispatcher_store, caplog, enqueue_run
):
    """Stuck PENDING run with no capable worker emits one event per hour."""
    import logging

    run_id = await enqueue_run(variant=SandboxImageVariant.ML)
    # Age the run past the grace window
    await dispatcher_store._pool.execute(
        "UPDATE workflow_runs SET created_at = NOW() - INTERVAL '60 seconds' "
        "WHERE run_id = $1",
        run_id,
    )
    with caplog.at_level(logging.INFO, logger="sagewai.core.stores.postgres"):
        emitted_first = await dispatcher_store.sweep_unroutable_runs()
        # Second call in same hour-bucket — must dedupe
        emitted_second = await dispatcher_store.sweep_unroutable_runs()

    assert len(emitted_first) == 1
    assert emitted_first[0]["run_id"] == run_id
    # Second call returns no new emissions (dedupe)
    assert emitted_second == []

    unroutable_messages = [
        r.message for r in caplog.records
        if "sagewai.run.unroutable" in r.message
    ]
    assert len(unroutable_messages) == 1
    assert run_id in unroutable_messages[0]
