# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Worker-side directive integration: poll fires sources, dispatches, links chain."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from sagewai.core.state import WorkflowRun
from sagewai.core.worker_directives import (
    consume_approved_decisions,
    should_evaluate_directives,
)


def test_should_evaluate_when_not_replay():
    run = WorkflowRun(workflow_name="wf", run_id="r-1")
    assert should_evaluate_directives(run) is True


def test_should_skip_when_replay_without_flag():
    run = WorkflowRun(workflow_name="wf", run_id="r-1", replay_of_run_id="r-old")
    assert should_evaluate_directives(run) is False


def test_should_evaluate_when_replay_with_flag_set():
    run = WorkflowRun(
        workflow_name="wf", run_id="r-1",
        replay_of_run_id="r-old",
        replay_re_evaluate_directives=True,
    )
    assert should_evaluate_directives(run) is True


@pytest.mark.asyncio
async def test_consume_approved_decisions_dispatches_then_marks_consumed():
    """Approved decision in queue → dispatched → marked consumed."""
    dispatched: list[Any] = []
    consumed: list[str] = []

    class _Reg:
        async def list_approved_for_run(self, run_id):
            return [
                {
                    "decision_id": "dec-1",
                    "policy_id": "pol",
                    "proposed_action": {"kind": "abort_run", "run_id": "r-1", "reason": "x"},
                    "decided_at": datetime.now(tz=timezone.utc),
                }
            ]

        async def mark_consumed(self, *, decision_id):
            consumed.append(decision_id)

    async def _fake_dispatch(decision):
        dispatched.append(decision.decision_id)

    await consume_approved_decisions(
        run=WorkflowRun(workflow_name="wf", run_id="r-1"),
        registry=_Reg(),
        dispatch_callable=_fake_dispatch,
    )
    assert dispatched == ["dec-1"]
    assert consumed == ["dec-1"]
