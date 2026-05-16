# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for StepRecord.injection_snapshot (Sealed-iii.C)."""
from sagewai.core.state import StepRecord, StepStatus, WorkflowRun
from sagewai.sealed.replay import InjectionSnapshot


def test_step_record_default_injection_snapshot_is_none():
    rec = StepRecord(step_name="s")
    assert rec.injection_snapshot is None


def test_workflow_run_step_serialises_injection_snapshot():
    snap = InjectionSnapshot(
        effective_env_keys=["X"],
        effective_secret_keys=["X"],
        security_profile_ref="builtin://p",
        secret_value_hashes={"X": "h"},
        secret_value_versions={"X": None},
        revocations_active_at_step={},
        captured_at=42.0,
    )
    run = WorkflowRun(workflow_name="wf", run_id="r1")
    run.steps["s"] = StepRecord(
        step_name="s",
        status=StepStatus.COMPLETED,
        result="ok",
        attempts=1,
        completed_at=42.0,
        injection_snapshot=snap,
    )

    round = WorkflowRun.from_dict(run.to_dict())
    assert round.steps["s"].injection_snapshot == snap


def test_workflow_run_step_handles_missing_injection_snapshot_legacy():
    """A pre-iii.C step dict (no injection_snapshot key) loads as None."""
    legacy = {
        "workflow_name": "wf",
        "run_id": "r1",
        "steps": {
            "s": {
                "status": "completed",
                "result": "ok",
                "error": None,
                "attempts": 1,
                "started_at": 1.0,
                "completed_at": 2.0,
                # no injection_snapshot key
            }
        },
    }
    run = WorkflowRun.from_dict(legacy)
    assert run.steps["s"].injection_snapshot is None
