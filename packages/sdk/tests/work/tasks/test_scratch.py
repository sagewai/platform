# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Scratch workspaces and their read-only result validator."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from sagewai.work.models import ActionResult, ActionScope
from sagewai.work.runtime import OperatorResult, WorkRequest
from sagewai.work.tasks.scratch import (
    ScratchResultValidator,
    ScratchWorkspace,
    ScratchWorkspaceManager,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_prepare_creates_isolated_directory_and_is_idempotent(tmp_path: Path) -> None:
    manager = ScratchWorkspaceManager(root=tmp_path / "scratch")
    workspace = await manager.prepare(project_id="project-a", work_id="task-1:plan:1:1", attempt_id="plan")
    assert isinstance(workspace, ScratchWorkspace)
    assert workspace.path.is_dir()
    assert workspace.ref == "scratch://project-a/task-1:plan:1:1/plan"
    assert workspace.path == (tmp_path / "scratch" / "project-a" / "task-1:plan:1:1" / "plan").resolve()
    again = await manager.prepare(project_id="project-a", work_id="task-1:plan:1:1", attempt_id="plan")
    assert again == workspace


@pytest.mark.asyncio
async def test_prepare_rejects_path_components(tmp_path: Path) -> None:
    manager = ScratchWorkspaceManager(root=tmp_path)
    with pytest.raises(ValueError):
        await manager.prepare(project_id="../x", work_id="w", attempt_id="a")
    with pytest.raises(ValueError):
        await manager.prepare(project_id="p", work_id="w/../../x", attempt_id="a")


def _request(work_id: str = "w") -> WorkRequest:
    return WorkRequest(
        project_id="project-a", work_id=work_id, run_id="r", stage="plan",
        action_scope=ActionScope(project_id="project-a", objective="plan", allowed_targets=(".",)),
        action_intents=(), control_preconditions=(),
    )


def _result(request: WorkRequest, action_results=()) -> OperatorResult:
    return OperatorResult(
        project_id=request.project_id, work_id=request.work_id, run_id=request.run_id, status="passed",
        summary="ok", evidence_refs=(), artifact_refs=(), changes=(), verification=(), risks=(),
        action_results=tuple(action_results), profile_context={},
    )


@pytest.mark.asyncio
async def test_validator_blocks_action_results_and_foreign_workspaces(tmp_path: Path) -> None:
    manager = ScratchWorkspaceManager(root=tmp_path)
    workspace = await manager.prepare(project_id="project-a", work_id="w", attempt_id="a")
    validator = ScratchResultValidator()
    report = await validator.validate(request=_request(), result=_result(_request()), workspace=workspace)
    assert report.verdict == "pass"
    receipt = ActionResult(
        project_id="project-a", action_id="w:x", status="succeeded", external_ref=None,
        evidence_refs=(), started_at=NOW, completed_at=NOW,
    )
    report = await validator.validate(request=_request(), result=_result(_request(), (receipt,)), workspace=workspace)
    assert report.verdict == "blocked"
    with pytest.raises(ValueError):
        await validator.validate(request=_request("other"), result=_result(_request("other")), workspace=workspace)
