# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Postgres adapters — directive_evaluations + pending_directive_approvals
SQL bindings. Skipped when SAGEWAI_DATABASE_URL is unset."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("SAGEWAI_DATABASE_URL"),
        reason="requires SAGEWAI_DATABASE_URL",
    ),
]


@pytest.fixture
async def postgres_pool():
    """Async asyncpg connection pool backed by SAGEWAI_DATABASE_URL."""
    import asyncpg

    url = os.environ["SAGEWAI_DATABASE_URL"]
    pool = await asyncpg.create_pool(url)
    yield pool
    await pool.close()


@pytest.mark.asyncio
async def test_evaluation_insert_round_trips(postgres_pool):
    from sagewai.sealed.directives.postgres_adapters import (
        DirectiveEvaluationsAdapter,
    )

    adapter = DirectiveEvaluationsAdapter(postgres_pool)
    await adapter.insert_directive_evaluation(
        event_type="directive.evaluated",
        decision_id="dec-1",
        run_id="r-1",
        project_id="p",
        workflow_name="wf",
        policy_id="pol",
        signal_kind="cost_overrun",
        severity="warning",
        details={"actual_cost_usd": 12.4},
    )
    rows = await adapter.list_for_run(run_id="r-1")
    assert len(rows) == 1
    assert rows[0]["details"] == {"actual_cost_usd": 12.4}


@pytest.mark.asyncio
async def test_approval_pending_then_approved(postgres_pool):
    from sagewai.sealed.directives.postgres_adapters import (
        ApprovalsPostgresAdapter,
    )

    adapter = ApprovalsPostgresAdapter(postgres_pool)
    now = datetime.now(tz=timezone.utc)
    await adapter.insert(
        {
            "decision_id": "dec-pg-1",
            "run_id": "r-1",
            "project_id": "p",
            "workflow_name": "wf",
            "policy_id": "pol",
            "triggering_signal": {"kind": "cost_overrun"},
            "proposed_action": {"kind": "abort_run"},
            "requested_at": now,
            "status": "pending",
            "decided_at": None,
            "decided_by": None,
            "operator_note": None,
            "expires_at": now,
        }
    )
    pending = await adapter.fetch_one_pending_for("r-1", "pol")
    assert pending is not None
    await adapter.update_status(
        decision_id="dec-pg-1",
        status="approved",
        decided_at=now,
        decided_by="ops",
        operator_note="x",
    )
    approved = await adapter.fetch_approved_for_run("r-1")
    assert len(approved) == 1
