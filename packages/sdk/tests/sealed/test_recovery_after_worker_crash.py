# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for recovery sweep handling worker-crash mid-revocation."""
import os
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAGEWAI_DATABASE_URL"),
    reason="SAGEWAI_DATABASE_URL not set",
)


@pytest.mark.asyncio
async def test_recovery_aborts_runs_left_in_revoking_state():
    """Worker crashed after seeing revoked_at; recovery sweep finishes the abort."""
    from sagewai.core.stores.postgres import PostgresStore

    store = PostgresStore(database_url=os.environ["SAGEWAI_DATABASE_URL"])
    await store.initialize()
    try:
        await store._pool.execute(
            """
            INSERT INTO workflow_runs
              (id, workflow_name, run_id, status, revoked_at, revoke_reason)
            VALUES ('wf:r-stuck', 'wf', 'r-stuck', 'running', $1, 'crashed mid-abort')
            ON CONFLICT (id) DO UPDATE
              SET revoked_at = EXCLUDED.revoked_at,
                  revoke_reason = EXCLUDED.revoke_reason,
                  status = 'running'
            """,
            datetime.now(timezone.utc),
        )
        completed = await store.recover_revoked_stuck_runs()
        assert completed >= 1
        row = await store._pool.fetchrow(
            "SELECT status FROM workflow_runs WHERE run_id = 'r-stuck'"
        )
        assert row["status"] == "failed"
    finally:
        await store._pool.execute("DELETE FROM workflow_runs WHERE run_id = 'r-stuck'")
        await store.close()


@pytest.mark.asyncio
async def test_recovery_idempotent_when_no_stuck_runs():
    from sagewai.core.stores.postgres import PostgresStore

    store = PostgresStore(database_url=os.environ["SAGEWAI_DATABASE_URL"])
    await store.initialize()
    try:
        completed = await store.recover_revoked_stuck_runs()
        assert completed == 0
    finally:
        await store.close()
