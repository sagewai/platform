# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PostgreSQL integration coverage for durable workflow project scope."""

from __future__ import annotations

import os

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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_public_id_and_heartbeat_are_project_isolated() -> None:
    store = PostgresStore(database_url=DATABASE_URL)
    await store.initialize()
    await store._pool.execute("DELETE FROM workflow_runs")
    try:
        scopes = (
            ("alpha", StepStatus.RUNNING),
            ("beta", StepStatus.RUNNING),
            ("global", StepStatus.RUNNING),
            (None, StepStatus.RUNNING),
        )
        for project_id, status in scopes:
            run = WorkflowRun(
                workflow_name="scope",
                run_id="same",
                project_id=project_id,
                status=status,
            )
            await store.save_run(run)

        for project_id, _status in scopes:
            loaded = await store.load_run(
                "scope",
                "same",
                project_id=project_id,
            )
            assert loaded is not None
            assert loaded.project_id == project_id
            assert len(
                await store.list_runs("scope", project_id=project_id)
            ) == 1

        await store._pool.execute(
            "UPDATE workflow_runs SET updated_at = NOW() - INTERVAL '10 minutes' "
            "WHERE workflow_name = 'scope' AND run_id = 'same'"
        )
        await store.heartbeat("scope", "same", project_id="alpha")

        assert await store.recover_stale_runs(
            stale_timeout_seconds=300,
            project_id="alpha",
        ) == []
        assert len(
            await store.recover_stale_runs(
                stale_timeout_seconds=300,
                project_id="beta",
            )
        ) == 1
        assert len(
            await store.recover_stale_runs(
                stale_timeout_seconds=300,
                project_id=None,
            )
        ) == 1
    finally:
        await store._pool.execute("DELETE FROM workflow_runs")
        await store.close()
