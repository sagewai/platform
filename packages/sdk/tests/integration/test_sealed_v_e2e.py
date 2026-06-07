# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end: directive evaluation → audit row persisted → admin can list it.

Postgres-gated. Skipped when SAGEWAI_DATABASE_URL is absent.

This validates the full Sealed-v persistence path: signal → evaluator
→ DirectiveAuditWriter → directive_evaluations table → admin list query.
"""
import os
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAGEWAI_DATABASE_URL"),
    reason="SAGEWAI_DATABASE_URL not set",
)


@pytest.mark.asyncio
async def test_audit_writer_persists_directive_evaluated_event() -> None:
    """End-to-end: emit a directive.evaluated event, then list it via admin
    adapter. Demonstrates the audit path that the worker integration uses."""
    from sagewai.db.engine import create_engine
    from sagewai.sealed.directives.audit import DirectiveAuditWriter
    from sagewai.sealed.directives.postgres_adapters import (
        DirectiveEvaluationsAdapter,
    )

    engine = create_engine(os.environ["SAGEWAI_DATABASE_URL"])
    try:
        adapter = DirectiveEvaluationsAdapter(engine=engine)
        writer = DirectiveAuditWriter(store=adapter)

        run_id = f"r-e2e-{int(datetime.now(tz=timezone.utc).timestamp())}"
        await writer.emit(
            event_type="directive.evaluated",
            decision_id="dec-e2e-1",
            run_id=run_id,
            project_id="p-e2e",
            workflow_name="wf-e2e",
            policy_id="cost-overrun-default",
            signal_kind="cost_overrun",
            severity="warning",
            details={"actual_cost_usd": 12.4, "estimated_cost_usd": 1.0},
        )

        rows = await adapter.list_for_run(run_id=run_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["event_type"] == "directive.evaluated"
        assert row["decision_id"] == "dec-e2e-1"
        assert row["policy_id"] == "cost-overrun-default"
        assert row["severity"] == "warning"
        assert row["details"]["actual_cost_usd"] == 12.4

        # Filter API should also find the row by run_id.
        filtered = await adapter.list_filtered(run_id=run_id, limit=10)
        assert any(r["decision_id"] == "dec-e2e-1" for r in filtered)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_approval_lifecycle_round_trip() -> None:
    """End-to-end: insert a pending approval, approve it, fetch by run."""
    from sagewai.db.engine import create_engine
    from sagewai.sealed.directives.postgres_adapters import (
        ApprovalsPostgresAdapter,
    )

    engine = create_engine(os.environ["SAGEWAI_DATABASE_URL"])
    try:
        adapter = ApprovalsPostgresAdapter(engine=engine)
        now = datetime.now(tz=timezone.utc)
        decision_id = f"dec-e2e-{int(now.timestamp())}"
        run_id = f"r-e2e-{int(now.timestamp())}"

        await adapter.insert(
            {
                "decision_id": decision_id,
                "run_id": run_id,
                "project_id": "p",
                "workflow_name": "wf",
                "policy_id": "pol",
                "triggering_signal": {"kind": "cost_overrun"},
                "proposed_action": {"kind": "abort_run", "run_id": run_id, "reason": "x"},
                "requested_at": now,
                "status": "pending",
                "decided_at": None,
                "decided_by": None,
                "operator_note": None,
                "expires_at": now,
            }
        )

        pending = await adapter.fetch_one_pending_for(run_id, "pol")
        assert pending is not None

        await adapter.update_status(
            decision_id=decision_id,
            status="approved",
            decided_at=now,
            decided_by="ops",
            operator_note="ok",
        )

        approved = await adapter.fetch_approved_for_run(run_id)
        assert any(r["decision_id"] == decision_id for r in approved)
    finally:
        await engine.dispose()
