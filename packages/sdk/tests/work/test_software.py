# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Software profile contexts, pinned worktrees, and deterministic diff checks."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import sagewai.work as work
from sagewai.work import ActionResult, ActionScope, OperatorResult, WorkRequest
from sagewai.work.profiles.software import (
    SoftwareAttemptContext,
    SoftwareCapsuleContext,
    SoftwareContractContext,
    SoftwareResultValidator,
    SoftwareWorkspace,
    SoftwareWorkspaceControlCheck,
    SoftwareWorktreeManager,
    WorkspaceStaleError,
    WorktreeBranchPublisher,
    software_workspace_precondition,
)

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("base\n")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "base")
    return repository, _git(repository, "rev-parse", "HEAD")


def _result(*, action_results: tuple[ActionResult, ...] = ()) -> OperatorResult:
    return OperatorResult(
        project_id="project-a",
        work_id="work-1",
        run_id="run-1",
        status="passed",
        summary="done",
        evidence_refs=("command://fake",),
        artifact_refs=(),
        changes=(),
        verification=("git diff",),
        risks=(),
        action_results=action_results,
        profile_context={},
    )


def test_software_profile_contexts_round_trip_at_profile_boundary() -> None:
    contract = SoftwareContractContext(base_sha="a" * 40)
    capsule = SoftwareCapsuleContext(
        base_sha="a" * 40,
        current_sha="b" * 40,
        repo_instructions=("AGENTS.md",),
        verification_commands=("just smoke",),
    )
    attempt = SoftwareAttemptContext(base_sha="a" * 40, result_sha=None)

    assert SoftwareContractContext.model_validate(contract.model_dump()) == contract
    assert SoftwareCapsuleContext.model_validate(capsule.model_dump()) == capsule
    assert SoftwareAttemptContext.model_validate(attempt.model_dump()) == attempt
    assert not any(name.startswith("Software") for name in work.__all__)


@pytest.mark.asyncio
async def test_workspace_control_check_fails_when_worktree_is_missing(tmp_path: Path) -> None:
    expected_sha = "a" * 40
    precondition = software_workspace_precondition(project_id="project-a")
    workspace = SoftwareWorkspace(
        ref="workspace://workspace",
        project_id="project-a",
        work_id="work-1",
        attempt_id="workspace",
        repository=tmp_path / "repository",
        path=tmp_path / "missing",
        base_sha=expected_sha,
        initial_sha=expected_sha,
    )
    result = await SoftwareWorkspaceControlCheck().evaluate(
        SimpleNamespace(
            request=SimpleNamespace(project_id="project-a", work_id="work-1"),
            precondition=precondition,
            capsule=SimpleNamespace(
                profile_context=SoftwareCapsuleContext(
                    base_sha=expected_sha,
                    current_sha=expected_sha,
                    repo_instructions=(),
                    verification_commands=("just smoke",),
                ).model_dump(mode="json")
            ),
            workspace=workspace,
        )
    )

    assert result.passed is False
    assert result.evidence_refs == ("workspace://workspace",)
    assert result.detail == "recorded workspace does not exist"


@pytest.mark.asyncio
async def test_worktree_is_pinned_retryable_and_detects_unexpected_head_movement(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    manager = SoftwareWorktreeManager(root=tmp_path / "worktrees")
    workspace = await manager.prepare(
        repository=repository,
        project_id="project-a",
        work_id="work-1",
        attempt_id="attempt-1",
        base_sha=base_sha,
    )
    (workspace.path / "partial.txt").write_text("survives a killed runtime\n")

    resumed = await manager.prepare(
        repository=repository,
        project_id="project-a",
        work_id="work-1",
        attempt_id="attempt-1",
        base_sha=base_sha,
    )

    assert resumed.path == tmp_path / "worktrees" / "project-a" / "work-1" / "attempt-1"
    assert resumed.initial_sha == base_sha
    assert (resumed.path / "partial.txt").read_text() == "survives a killed runtime\n"

    (resumed.path / "README.md").write_text("moved\n")
    _git(resumed.path, "add", "README.md")
    _git(resumed.path, "commit", "-m", "unexpected head")
    with pytest.raises(WorkspaceStaleError, match="HEAD moved"):
        await manager.assert_current(resumed, expected_sha=base_sha)


@pytest.mark.asyncio
async def test_worktree_publishes_reviewed_state_to_isolated_git_remote(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(("git", "init", "--bare", "-q", str(remote)), check=True)
    _git(repository, "remote", "add", "origin", str(remote))
    manager = SoftwareWorktreeManager(root=tmp_path / "worktrees")
    workspace = await manager.prepare(
        repository=repository,
        project_id="project-a",
        work_id="work-1",
        attempt_id="workspace",
        base_sha=base_sha,
    )
    (workspace.path / "target.txt").write_text("reviewed\n")

    publisher = WorktreeBranchPublisher(
        worktree_manager=manager,
        repository=repository,
    )
    result_sha = await publisher.publish(
        project_id="project-a",
        work_id="work-1",
        base_sha=base_sha,
        expected_sha=base_sha,
        branch="sagewai/work-1",
        commit_message="feat: implement work-1",
    )
    recovered_sha = await publisher.publish(
        project_id="project-a",
        work_id="work-1",
        base_sha=base_sha,
        expected_sha=base_sha,
        branch="sagewai/work-1",
        commit_message="feat: implement work-1",
    )

    assert result_sha != base_sha
    assert recovered_sha == result_sha
    assert _git(remote, "rev-parse", "refs/heads/sagewai/work-1") == result_sha
    assert _git(workspace.path, "status", "--short") == ""


@pytest.mark.asyncio
async def test_publish_retry_pushes_existing_local_commit_after_remote_recovers(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    remote = tmp_path / "temporarily-missing.git"
    _git(repository, "remote", "add", "origin", str(remote))
    manager = SoftwareWorktreeManager(root=tmp_path / "worktrees")
    workspace = await manager.prepare(
        repository=repository,
        project_id="project-a",
        work_id="work-1",
        attempt_id="workspace",
        base_sha=base_sha,
    )
    (workspace.path / "target.txt").write_text("reviewed\n")
    publisher = WorktreeBranchPublisher(
        worktree_manager=manager,
        repository=repository,
    )

    with pytest.raises(WorkspaceStaleError):
        await publisher.publish(
            project_id="project-a",
            work_id="work-1",
            base_sha=base_sha,
            expected_sha=base_sha,
            branch="sagewai/work-1",
            commit_message="feat: implement work-1",
        )

    committed_sha = _git(workspace.path, "rev-parse", "HEAD")
    subprocess.run(("git", "init", "--bare", "-q", str(remote)), check=True)
    recovered_sha = await publisher.publish(
        project_id="project-a",
        work_id="work-1",
        base_sha=base_sha,
        expected_sha=base_sha,
        branch="sagewai/work-1",
        commit_message="feat: implement work-1",
    )

    assert recovered_sha == committed_sha
    assert _git(remote, "rev-parse", "refs/heads/sagewai/work-1") == committed_sha


@pytest.mark.asyncio
async def test_post_run_validator_rejects_scope_and_undeclared_effects(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    manager = SoftwareWorktreeManager(root=tmp_path / "worktrees")
    workspace = await manager.prepare(
        repository=repository,
        project_id="project-a",
        work_id="work-1",
        attempt_id="attempt-1",
        base_sha=base_sha,
    )
    (workspace.path / "outside.txt").write_text("undeclared\n")
    request = WorkRequest(
        project_id="project-a",
        work_id="work-1",
        run_id="run-1",
        stage="implement",
        action_scope=ActionScope(
            objective="Change only the SDK Work package",
            allowed_targets=("packages/sdk/sagewai/work",),
            forbidden_targets=("outside.txt",),
            max_files_changed=1,
            max_diff_lines=10,
            allowed_capabilities=("filesystem.write",),
        ),
        action_intents=(),
        control_preconditions=(),
    )

    report = await SoftwareResultValidator().validate(
        request=request,
        result=_result(),
        workspace=workspace,
    )

    assert report.verdict == "blocked"
    assert "outside.txt is outside allowed targets" in report.scope_violations
    assert "outside.txt is forbidden" in report.scope_violations
    assert "undeclared change: outside.txt" in report.scope_violations
    assert report.changed_files == 1
    assert report.diff_lines == 1
