# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""WorkflowRun gains directive_chain, estimated_cost_usd,
replay_re_evaluate_directives, execution_mode_override, identity_from."""
from __future__ import annotations

from datetime import datetime, timezone

from sagewai.core.state import ExecutionMode, WorkflowRun
from sagewai.sealed.directives.models import DirectiveChainEntry


def test_new_fields_default_values():
    run = WorkflowRun(workflow_name="wf", run_id="r-1")
    assert run.directive_chain == []
    assert run.estimated_cost_usd is None
    assert run.replay_re_evaluate_directives is False
    assert run.execution_mode_override is None
    assert run.identity_from is None


def test_to_dict_round_trip_with_chain_entry():
    entry = DirectiveChainEntry(
        decision_id="d1",
        direction="caused_replay",
        counterpart_run_id="r-old",
        action_kind="promote_run_mode",
        decided_at=datetime.now(tz=timezone.utc),
        target_mode=ExecutionMode.IDENTITY,
        reason="capability gap",
    )
    run = WorkflowRun(
        workflow_name="wf",
        run_id="r-1",
        directive_chain=[entry],
        estimated_cost_usd=2.5,
        replay_re_evaluate_directives=True,
        execution_mode_override=ExecutionMode.IDENTITY,
        identity_from="current_cascade",
    )
    dumped = run.to_dict()
    revived = WorkflowRun.from_dict(dumped)
    assert revived.directive_chain == [entry]
    assert revived.estimated_cost_usd == 2.5
    assert revived.replay_re_evaluate_directives is True
    assert revived.execution_mode_override is ExecutionMode.IDENTITY
    assert revived.identity_from == "current_cascade"
