# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Worker-side directive integration helpers — kept out of worker.py
to keep that file focused on the run lifecycle.

See spec §2.3, §6.4."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter

from sagewai.core.state import WorkflowRun
from sagewai.sealed.directives.models import DirectiveAction, DirectiveDecision

logger = logging.getLogger(__name__)


def should_evaluate_directives(run: WorkflowRun) -> bool:
    """Skip evaluation on replay runs unless the operator opted in."""
    if run.replay_of_run_id is None:
        return True
    return run.replay_re_evaluate_directives


_action_adapter = TypeAdapter(DirectiveAction)


async def consume_approved_decisions(
    *,
    run: WorkflowRun,
    registry: Any,  # PendingApprovalsRegistry
    dispatch_callable: Callable[[DirectiveDecision], Awaitable[None]],
) -> None:
    """Find approved-but-not-yet-consumed decisions for this run, dispatch each."""
    rows = await registry.list_approved_for_run(run.run_id)
    for row in rows:
        action = _action_adapter.validate_python(row["proposed_action"])
        decision = DirectiveDecision(
            decision_id=row["decision_id"],
            directive_policy_id=row["policy_id"],
            triggering_signal=_synthetic_signal(run, row),
            action=action,
            requires_approval=True,
            decided_at=row["decided_at"],
        )
        try:
            await dispatch_callable(decision)
            await registry.mark_consumed(decision_id=row["decision_id"])
        except Exception:  # noqa: BLE001
            logger.exception(
                "consume_approved_decisions: dispatch failed for %s", row["decision_id"],
            )


def _synthetic_signal(run: WorkflowRun, row: dict[str, Any]):
    """Fabricate a placeholder triggering_signal — the real one is in the
    approvals row's ``triggering_signal`` JSONB. We only need enough fields
    to satisfy DirectiveDecision validation."""
    from sagewai.sealed.directives.models import SignalEvent
    return SignalEvent(
        kind="post_approval",
        run_id=run.run_id,
        project_id=run.project_id,
        workflow_name=run.workflow_name,
        step_index=0,
        severity="info",
        detail="Approved post-hoc",
        evidence={},
        emitted_at=row["decided_at"] if isinstance(row["decided_at"], datetime) else datetime.now(),
    )
