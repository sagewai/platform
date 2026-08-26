# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Software profile contexts, worktrees, and deterministic result checks."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict

from sagewai.fleet.execution import run_worker_subprocess
from sagewai.home import sagewai_home
from sagewai.work.models import OperatorDisciplineReport
from sagewai.work.runtime import OperatorResult, WorkRequest


class SoftwareContractContext(BaseModel):
    """Software-specific immutable contract state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_sha: str


class SoftwareCapsuleContext(BaseModel):
    """Software-specific context compiled for an operator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_sha: str
    current_sha: str
    repo_instructions: tuple[str, ...]
    verification_commands: tuple[str, ...]


class SoftwareAttemptContext(BaseModel):
    """Software-specific execution receipt state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_sha: str
    result_sha: str | None


class SoftwareWorkspace(BaseModel):
    """One isolated worktree pinned to an attempt's base revision."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    ref: str
    project_id: str
    work_id: str
    attempt_id: str
    repository: Path
    path: Path
    base_sha: str
    initial_sha: str


class WorkspaceStaleError(RuntimeError):
    """The pinned workspace no longer has the expected Git state."""


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


class SoftwareResultValidator:
    """Validate actual Git effects against the declared ActionScope and intents."""

    async def validate(
        self,
        *,
        request: WorkRequest,
        result: OperatorResult,
        workspace: SoftwareWorkspace,
    ) -> OperatorDisciplineReport:
        if (
            request.project_id != workspace.project_id
            or request.work_id != workspace.work_id
            or result.project_id != request.project_id
            or result.work_id != request.work_id
            or result.run_id != request.run_id
        ):
            raise ValueError("result validation inputs belong to different work")

        changed_files, diff_lines = await _git_changes(workspace)
        scope_violations: list[str] = []
        scope = request.action_scope

        for changed_file in changed_files:
            if scope.allowed_targets and not any(
                _within_target(changed_file, target) for target in scope.allowed_targets
            ):
                scope_violations.append(f"{changed_file} is outside allowed targets")
            if any(_within_target(changed_file, target) for target in scope.forbidden_targets):
                scope_violations.append(f"{changed_file} is forbidden")
            if not any(
                _intent_declares_file(intent.target, intent.scope, changed_file)
                for intent in request.action_intents
            ):
                scope_violations.append(f"undeclared change: {changed_file}")

        if scope.max_files_changed is not None and len(changed_files) > scope.max_files_changed:
            scope_violations.append(
                f"{len(changed_files)} changed files exceeds {scope.max_files_changed}"
            )
        if scope.max_diff_lines is not None and diff_lines > scope.max_diff_lines:
            scope_violations.append(f"{diff_lines} diff lines exceeds {scope.max_diff_lines}")

        declared_action_ids = {intent.action_id for intent in request.action_intents}
        receipt_ids = {receipt.action_id for receipt in result.action_results}
        for action_id in sorted(receipt_ids - declared_action_ids):
            scope_violations.append(f"undeclared action result: {action_id}")
        for action_id in sorted(declared_action_ids - receipt_ids):
            scope_violations.append(f"missing action result: {action_id}")

        verdict = "blocked" if scope_violations else "pass"
        return OperatorDisciplineReport(
            project_id=request.project_id,
            work_id=request.work_id,
            run_id=request.run_id,
            unsupported_claims=(),
            scope_violations=tuple(scope_violations),
            permission_violations=(),
            risk_mismatches=(),
            unnecessary_changes=(),
            output_tokens=None,
            changed_files=len(changed_files),
            diff_lines=diff_lines,
            verdict=verdict,
        )


def _validate_component(label: str, value: str) -> None:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError(f"{label} is not a safe path component")


async def _git(cwd: Path, *args: str):
    return await run_worker_subprocess(
        argv=("git", *args),
        cwd=cwd,
        output_limit=100_000,
    )


async def _git_changes(workspace: SoftwareWorkspace) -> tuple[tuple[str, ...], int]:
    tracked = await _git(workspace.path, "diff", "--numstat", workspace.base_sha, "--")
    if tracked.returncode != 0:
        raise WorkspaceStaleError(tracked.stderr)
    untracked = await _git(
        workspace.path,
        "ls-files",
        "--others",
        "--exclude-standard",
    )
    if untracked.returncode != 0:
        raise WorkspaceStaleError(untracked.stderr)

    files: set[str] = set()
    diff_lines = 0
    for line in tracked.stdout.splitlines():
        added, deleted, changed_file = line.split("\t", 2)
        files.add(changed_file)
        if added != "-":
            diff_lines += int(added)
        if deleted != "-":
            diff_lines += int(deleted)
    for changed_file in untracked.stdout.splitlines():
        files.add(changed_file)
        try:
            diff_lines += len((workspace.path / changed_file).read_text().splitlines())
        except UnicodeDecodeError:
            pass
    return tuple(sorted(files)), diff_lines


def _normalized_target(value: str) -> str:
    return str(PurePosixPath(value)).rstrip("/")


def _within_target(changed_file: str, target: str) -> bool:
    target = _normalized_target(target)
    changed_file = _normalized_target(changed_file)
    return changed_file == target or changed_file.startswith(f"{target}/")


def _intent_declares_file(target: str, scope: dict, changed_file: str) -> bool:
    if _within_target(changed_file, target):
        return True
    allowed = scope.get("allowed_targets", ())
    return isinstance(allowed, (list, tuple)) and any(
        isinstance(value, str) and _within_target(changed_file, value) for value in allowed
    )
