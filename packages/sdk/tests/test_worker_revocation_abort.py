# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for worker between-steps revocation poll + abort."""
import os
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAGEWAI_DATABASE_URL"),
    reason="SAGEWAI_DATABASE_URL not set",
)


@pytest.mark.asyncio
async def test_worker_aborts_run_when_revoked_at_set(monkeypatch):
    """Simulate the between-steps poll: if revoked_at is set, abort."""
    from sagewai.core.stores.postgres import PostgresStore
    from sagewai.core.worker import _check_run_revocation_and_abort

    store = PostgresStore(database_url=os.environ["SAGEWAI_DATABASE_URL"])
    await store.initialize()
    try:
        # Insert a running run with revoked_at set
        # id follows the workflow_name:run_id convention used by postgres.py
        await store._pool.execute(
            """
            INSERT INTO workflow_runs
              (id, workflow_name, run_id, status, revoked_at, revoke_reason)
            VALUES ('wf:r-abort', 'wf', 'r-abort', 'running', $1, 'leaked')
            ON CONFLICT (id) DO UPDATE
              SET revoked_at = EXCLUDED.revoked_at,
                  revoke_reason = EXCLUDED.revoke_reason,
                  status = 'running'
            """,
            datetime.now(timezone.utc),
        )

        class _FakeSandbox:
            stopped = False

            async def stop(self):
                self.stopped = True

        sandbox = _FakeSandbox()
        aborted = await _check_run_revocation_and_abort(
            store=store,
            run_id="r-abort",
            sandbox=sandbox,
        )
        assert aborted is True
        assert sandbox.stopped is True

        row = await store._pool.fetchrow(
            "SELECT status FROM workflow_runs WHERE run_id = 'r-abort'"
        )
        assert row["status"] == "failed"
    finally:
        await store._pool.execute(
            "DELETE FROM workflow_runs WHERE run_id = 'r-abort'"
        )
        await store.close()


@pytest.mark.asyncio
async def test_worker_does_not_abort_when_revoked_at_null():
    """Poll returns False when revoked_at is null."""
    from sagewai.core.stores.postgres import PostgresStore
    from sagewai.core.worker import _check_run_revocation_and_abort

    store = PostgresStore(database_url=os.environ["SAGEWAI_DATABASE_URL"])
    await store.initialize()
    try:
        await store._pool.execute(
            """
            INSERT INTO workflow_runs (id, workflow_name, run_id, status)
            VALUES ('wf:r-noabort', 'wf', 'r-noabort', 'running')
            ON CONFLICT (id) DO UPDATE SET status = 'running', revoked_at = NULL
            """,
        )
        sandbox = type("S", (), {"stopped": False})()
        aborted = await _check_run_revocation_and_abort(
            store=store,
            run_id="r-noabort",
            sandbox=sandbox,
        )
        assert aborted is False
    finally:
        await store._pool.execute(
            "DELETE FROM workflow_runs WHERE run_id = 'r-noabort'"
        )
        await store.close()
