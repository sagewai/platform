# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PostgreSQL integration tests — require docker-compose.dev.yml running.

Run: pytest tests/test_integration_postgres.py -v -m integration
"""

import os
import uuid

import pytest

from sagewai.core.state import StepStatus, WorkflowRun
from sagewai.core.stores.postgres import PostgresStore

DATABASE_URL = os.environ.get(
    "SAGEWAI_DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://sagecurator:sagecurator_password@localhost:5432/sagecurator",
    ),
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def pg_store():
    store = PostgresStore(database_url=DATABASE_URL)
    await store.initialize()
    yield store
    await store.close()


class TestPostgresWorkflowLifecycle:
    """Full workflow lifecycle with real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_save_load_roundtrip(self, pg_store):
        run_id = str(uuid.uuid4())
        wf_run = WorkflowRun(
            workflow_name="test-wf",
            run_id=run_id,
            input_data="hello",
            status=StepStatus.RUNNING,
        )
        await pg_store.save_run(wf_run)
        loaded = await pg_store.load_run("test-wf", run_id)
        assert loaded is not None
        assert loaded.run_id == run_id
        assert loaded.input_data == "hello"

    @pytest.mark.asyncio
    async def test_list_runs_by_status(self, pg_store):
        for i in range(3):
            wf = WorkflowRun(
                workflow_name="filter-test",
                run_id=str(uuid.uuid4()),
                input_data=f"input-{i}",
                status=StepStatus.COMPLETED if i < 2 else StepStatus.RUNNING,
            )
            await pg_store.save_run(wf)

        completed = await pg_store.list_runs("filter-test", status=StepStatus.COMPLETED)
        assert len(completed) >= 2

    @pytest.mark.asyncio
    async def test_heartbeat_updates_timestamp(self, pg_store):
        run_id = str(uuid.uuid4())
        wf = WorkflowRun(
            workflow_name="hb-test",
            run_id=run_id,
            input_data="x",
            status=StepStatus.RUNNING,
        )
        await pg_store.save_run(wf)
        await pg_store.heartbeat("hb-test", run_id)
        loaded = await pg_store.load_run("hb-test", run_id)
        assert loaded is not None

    @pytest.mark.asyncio
    async def test_recover_stale_runs(self, pg_store):
        run_id = str(uuid.uuid4())
        wf = WorkflowRun(
            workflow_name="stale-test",
            run_id=run_id,
            input_data="stale",
            status=StepStatus.RUNNING,
        )
        await pg_store.save_run(wf)
        # With 0 timeout, everything is stale
        stale = await pg_store.recover_stale_runs(stale_timeout_seconds=0)
        assert any(r.run_id == run_id for r in stale)

    @pytest.mark.asyncio
    async def test_load_nonexistent_returns_none(self, pg_store):
        result = await pg_store.load_run("nonexistent", "no-such-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self, pg_store):
        run_id = str(uuid.uuid4())
        wf = WorkflowRun(
            workflow_name="upsert-test",
            run_id=run_id,
            input_data="v1",
            status=StepStatus.RUNNING,
        )
        await pg_store.save_run(wf)
        wf.status = StepStatus.COMPLETED
        wf.output = "done"
        await pg_store.save_run(wf)
        loaded = await pg_store.load_run("upsert-test", run_id)
        assert loaded.status == StepStatus.COMPLETED
