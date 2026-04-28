# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sealed-iii.C — PostgresStore replay-column round-trip + list_replays_of."""
from __future__ import annotations

import os

import pytest

from sagewai.core.state import StepRecord, StepStatus, WorkflowRun
from sagewai.core.stores.postgres import PostgresStore
from sagewai.sealed.replay import InjectionSnapshot

DATABASE_URL = os.environ.get(
    "SAGEWAI_DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://sagecurator:sagecurator_password@localhost:5432/sagecurator",
    ),
)


@pytest.fixture
async def pg_store():
    """PostgresStore connected to the local dev PostgreSQL."""
    store = PostgresStore(database_url=DATABASE_URL)
    await store.initialize()
    # Clean workflow_runs before each test to avoid cross-test interference
    await store._pool.execute("DELETE FROM workflow_runs")
    yield store
    await store._pool.execute("DELETE FROM workflow_runs")
    await store.close()


@pytest.mark.integration
async def test_save_load_run_roundtrips_replay_columns(pg_store):
    run = WorkflowRun(
        workflow_name="wf",
        run_id="r2",
        replay_of_run_id="r1",
        replay_from_step=1,
        code_hash="hash-of-steps",
    )
    await pg_store.save_run(run)
    loaded = await pg_store.load_run("wf", "r2")

    assert loaded.replay_of_run_id == "r1"
    assert loaded.replay_from_step == 1
    assert loaded.code_hash == "hash-of-steps"


@pytest.mark.integration
async def test_save_load_run_roundtrips_step_injection_snapshot(pg_store):
    snap = InjectionSnapshot(
        effective_env_keys=["A"],
        effective_secret_keys=["A"],
        security_profile_ref="builtin://x",
        secret_value_hashes={"A": "h"},
        secret_value_versions={"A": None},
        revocations_active_at_step={},
        captured_at=10.0,
    )
    run = WorkflowRun(workflow_name="wf", run_id="r3")
    run.steps["s"] = StepRecord(
        step_name="s",
        status=StepStatus.COMPLETED,
        result="ok",
        attempts=1,
        completed_at=10.0,
        injection_snapshot=snap,
    )
    await pg_store.save_run(run)
    loaded = await pg_store.load_run("wf", "r3")
    assert loaded.steps["s"].injection_snapshot == snap


@pytest.mark.integration
async def test_list_replays_of_returns_child_runs(pg_store):
    parent = WorkflowRun(workflow_name="wf", run_id="r1")
    child_a = WorkflowRun(
        workflow_name="wf", run_id="r2",
        replay_of_run_id="r1", replay_from_step=0,
    )
    child_b = WorkflowRun(
        workflow_name="wf", run_id="r3",
        replay_of_run_id="r1", replay_from_step=2,
    )
    other = WorkflowRun(
        workflow_name="wf", run_id="r4",
        replay_of_run_id="r99", replay_from_step=0,
    )
    for r in [parent, child_a, child_b, other]:
        await pg_store.save_run(r)

    replays = await pg_store.list_replays_of("r1")
    assert {r.run_id for r in replays} == {"r2", "r3"}
