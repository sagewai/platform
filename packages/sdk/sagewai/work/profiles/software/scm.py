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

from difflib import unified_diff
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

    async def resume(
        self,
        *,
        repository: Path,
        project_id: str,
        work_id: str,
        attempt_id: str,
        base_sha: str,
        expected_sha: str,
    ) -> SoftwareWorkspace:
        """Resume the deterministic worktree only if its recorded HEAD is intact."""
        for label, value in (
            ("project_id", project_id),
            ("work_id", work_id),
            ("attempt_id", attempt_id),
        ):
            _validate_component(label, value)
        workspace = SoftwareWorkspace(
            ref=f"workspace://{attempt_id}",
            project_id=project_id,
            work_id=work_id,
            attempt_id=attempt_id,
            repository=repository.resolve(),
            path=self._root / project_id / work_id / attempt_id,
            base_sha=base_sha,
            initial_sha=base_sha,
        )
        if not workspace.path.exists():
            raise WorkspaceStaleError("recorded workspace does not exist")
        await self.assert_current(workspace, expected_sha=expected_sha)
        return workspace

    async def current_sha(self, workspace: SoftwareWorkspace) -> str:
        """Read the current Git HEAD from the isolated workspace."""
        result = await _git(workspace.path, "rev-parse", "HEAD")
        if result.returncode != 0:
            raise WorkspaceStaleError(result.stderr)
        return result.stdout.strip()

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

    async def publish_branch(
        self,
        workspace: SoftwareWorkspace,
        *,
        branch: str,
        commit_message: str,
    ) -> str:
        """Commit the reviewed workspace state and push one named branch."""
        valid = await _git(workspace.path, "check-ref-format", "--branch", branch)
        if valid.returncode != 0:
            raise ValueError(f"invalid Git branch: {branch}")

        status = await _git(workspace.path, "status", "--porcelain")
        if status.returncode != 0:
            raise WorkspaceStaleError(status.stderr)
        if status.stdout:
            added = await _git(workspace.path, "add", "--all")
            if added.returncode != 0:
                raise WorkspaceStaleError(added.stderr)
            committed = await _git(
                workspace.path,
                "commit",
                "--message",
                commit_message,
            )
            if committed.returncode != 0:
                raise WorkspaceStaleError(committed.stderr)

        result_sha = await self.current_sha(workspace)
        pushed = await _git(
            workspace.path,
            "push",
            "origin",
            f"HEAD:refs/heads/{branch}",
        )
        if pushed.returncode != 0:
            raise WorkspaceStaleError(pushed.stderr)
        return result_sha


async def workspace_diff(
    workspace: SoftwareWorkspace,
) -> tuple[str, tuple[str, ...]]:
    """Return the complete tracked/untracked review diff and changed paths."""
    tracked = await _git(
        workspace.path,
        "diff",
        "--no-ext-diff",
        workspace.base_sha,
        "--",
    )
    if tracked.returncode != 0:
        raise WorkspaceStaleError(tracked.stderr)
    tracked_names = await _git(
        workspace.path,
        "diff",
        "--name-only",
        workspace.base_sha,
        "--",
    )
    if tracked_names.returncode != 0:
        raise WorkspaceStaleError(tracked_names.stderr)
    untracked = await _git(
        workspace.path,
        "ls-files",
        "--others",
        "--exclude-standard",
    )
    if untracked.returncode != 0:
        raise WorkspaceStaleError(untracked.stderr)

    paths = set(tracked_names.stdout.splitlines())
    untracked_paths = tuple(path for path in untracked.stdout.splitlines() if path)
    paths.update(untracked_paths)
    parts = [tracked.stdout]
    for relative in untracked_paths:
        path = workspace.path / relative
        try:
            lines = path.read_text().splitlines(keepends=True)
        except UnicodeDecodeError:
            parts.append(f"Binary file b/{relative} added\n")
            continue
        parts.extend(
            unified_diff(
                (),
                lines,
                fromfile="/dev/null",
                tofile=f"b/{relative}",
            )
        )
    return "".join(parts), tuple(sorted(paths))


def _validate_component(label: str, value: str) -> None:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError(f"{label} is not a safe path component")


async def _git(cwd: Path, *args: str) -> WorkerProcessResult:
    return await run_worker_subprocess(
        argv=("git", *args),
        cwd=cwd,
        output_limit=100_000,
    )
