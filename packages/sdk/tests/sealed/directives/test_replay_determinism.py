# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Replay runs do NOT re-evaluate signals unless replay_re_evaluate_directives=True.

This is the determinism invariant: a replay reproduces what happened at
the original run, not what would happen now. Policy authors who tighten
thresholds between original and replay must not retroactively change
what fired.
"""
from __future__ import annotations

from sagewai.core.state import WorkflowRun
from sagewai.core.worker_directives import should_evaluate_directives


def test_non_replay_always_evaluates() -> None:
    run = WorkflowRun(workflow_name="wf", run_id="r-fresh")
    assert should_evaluate_directives(run) is True


def test_replay_run_default_skips_evaluation() -> None:
    run = WorkflowRun(
        workflow_name="wf",
        run_id="r-replay",
        replay_of_run_id="r-orig",
        replay_re_evaluate_directives=False,
    )
    assert should_evaluate_directives(run) is False


def test_replay_run_with_opt_in_evaluates() -> None:
    run = WorkflowRun(
        workflow_name="wf",
        run_id="r-replay",
        replay_of_run_id="r-orig",
        replay_re_evaluate_directives=True,
    )
    assert should_evaluate_directives(run) is True
