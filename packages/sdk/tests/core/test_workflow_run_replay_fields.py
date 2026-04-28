# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
from sagewai.core.state import WorkflowRun


def test_workflow_run_default_replay_fields_are_none():
    run = WorkflowRun(workflow_name="wf", run_id="r")
    assert run.replay_of_run_id is None
    assert run.replay_from_step is None
    assert run.code_hash is None


def test_workflow_run_replay_fields_roundtrip():
    run = WorkflowRun(
        workflow_name="wf",
        run_id="r2",
        replay_of_run_id="r1",
        replay_from_step=2,
        code_hash="abc123",
    )
    round = WorkflowRun.from_dict(run.to_dict())
    assert round.replay_of_run_id == "r1"
    assert round.replay_from_step == 2
    assert round.code_hash == "abc123"


def test_workflow_run_handles_missing_replay_fields_legacy():
    legacy = {"workflow_name": "wf", "run_id": "r"}
    run = WorkflowRun.from_dict(legacy)
    assert run.replay_of_run_id is None
    assert run.replay_from_step is None
    assert run.code_hash is None
