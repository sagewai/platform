# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pinned Git worktrees for the software Work profile."""

from __future__ import annotations

from pathlib import Path

from sagewai.fleet.execution import WorkerProcessResult, run_worker_subprocess
from sagewai.home import sagewai_home
from sagewai.work.profiles.software.models import (
    SoftwareWorkspace,
    WorkspaceStaleError,
)


class SoftwareWorktreeManager:
    """Create or resume one detached worktree per execution attempt."""

    def __init__(self, *, root: Path | None = None) -> None:
        self._root = (root or sagewai_home() / "worktrees").resolve()

    async def prepare(
        self,
        *,
        repository: Path,
        project_id: str,
        work_id: str,
        attempt_id: str,
        base_sha: str,
    ) -> SoftwareWorkspace:
        for label, value in (
            ("project_id", project_id),
            ("work_id", work_id),
            ("attempt_id", attempt_id),
        ):
            _validate_component(label, value)
        repository = repository.resolve()
        path = self._root / project_id / work_id / attempt_id

        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            result = await _git(
                repository,
                "worktree",
                "add",
                "--detach",
                str(path),
                base_sha,
            )
            if result.returncode != 0:
                raise WorkspaceStaleError(result.stderr)

        workspace = SoftwareWorkspace(
            ref=f"workspace://{attempt_id}",
            project_id=project_id,
            work_id=work_id,
            attempt_id=attempt_id,
            repository=repository,
            path=path,
            base_sha=base_sha,
            initial_sha=base_sha,
        )
        await self.assert_current(workspace, expected_sha=base_sha)
        return workspace

    async def assert_current(
        self,
        workspace: SoftwareWorkspace,
        *,
        expected_sha: str,
    ) -> None:
        result = await _git(workspace.path, "rev-parse", "HEAD")
        if result.returncode != 0:
            raise WorkspaceStaleError(result.stderr)
        actual_sha = result.stdout.strip()
        if actual_sha != expected_sha:
            raise WorkspaceStaleError(
                f"workspace HEAD moved: expected {expected_sha}, found {actual_sha}"
            )


def _validate_component(label: str, value: str) -> None:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError(f"{label} is not a safe path component")


async def _git(cwd: Path, *args: str) -> WorkerProcessResult:
    return await run_worker_subprocess(
        argv=("git", *args),
        cwd=cwd,
        output_limit=100_000,
    )
