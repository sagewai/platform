# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Scratch workspaces for planning and report stages that need no repository."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from sagewai.home import sagewai_home
from sagewai.work.models import OperatorDisciplineReport
from sagewai.work.runtime import OperatorResult, WorkRequest, Workspace

_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class ScratchWorkspace(BaseModel):
    """A plain directory that satisfies the Workspace protocol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str
    project_id: str
    work_id: str
    attempt_id: str
    path: Path


def _component(label: str, value: str) -> str:
    if _COMPONENT_RE.match(value) is None or ".." in value:
        raise ValueError(f"invalid {label}: {value!r}")
    return value


class ScratchWorkspaceManager:
    """Create one directory per project, work, and attempt under the Sagewai home."""

    def __init__(self, *, root: Path | None = None) -> None:
        self._root = (root or sagewai_home() / "scratch").resolve()

    async def prepare(self, *, project_id: str, work_id: str, attempt_id: str) -> ScratchWorkspace:
        path = (
            self._root
            / _component("project", project_id)
            / _component("work", work_id)
            / _component("attempt", attempt_id)
        ).resolve()
        if self._root not in path.parents:
            raise ValueError("scratch workspace escapes its root")
        path.mkdir(parents=True, exist_ok=True)
        return ScratchWorkspace(
            ref=f"scratch://{project_id}/{work_id}/{attempt_id}",
            project_id=project_id,
            work_id=work_id,
            attempt_id=attempt_id,
            path=path,
        )


class ScratchResultValidator:
    """Read-only discipline for scratch stages: no action receipts, matching identity."""

    async def validate(
        self, *, request: WorkRequest, result: OperatorResult, workspace: Workspace | None
    ) -> OperatorDisciplineReport:
        if not isinstance(workspace, ScratchWorkspace):
            raise ValueError("scratch validation requires a scratch workspace")
        if request.project_id != workspace.project_id or request.work_id != workspace.work_id:
            raise ValueError("validation inputs belong to different work")
        violations = ("read-only stage returned action results",) if result.action_results else ()
        return OperatorDisciplineReport(
            project_id=request.project_id,
            work_id=request.work_id,
            run_id=request.run_id,
            unsupported_claims=(),
            scope_violations=violations,
            permission_violations=(),
            risk_mismatches=(),
            unnecessary_changes=(),
            output_tokens=result.output_tokens,
            changed_files=0,
            diff_lines=0,
            verdict="blocked" if violations else "pass",
        )


__all__ = ["ScratchResultValidator", "ScratchWorkspace", "ScratchWorkspaceManager"]
