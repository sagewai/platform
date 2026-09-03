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

import hashlib
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import sagewai.work as work
from sagewai.artifacts import LocalArtifactStore
from sagewai.sandbox import NetworkPolicy, SandboxLifetime, ToolResult
from sagewai.work import (
    ActionIntent,
    ActionResult,
    ActionScope,
    OperatorResult,
    Reversibility,
    WorkItem,
    WorkRequest,
)
from sagewai.work.knowledge import KnowledgeStore
from sagewai.work.profiles.software import (
    SandboxedVerificationRunner,
    SoftwareAttemptContext,
    SoftwareCapsuleContext,
    SoftwareContractContext,
    SoftwareReadOnlyResultValidator,
    SoftwareResultValidator,
    SoftwareVerificationCheck,
    SoftwareVerifier,
    SoftwareWorkspace,
    SoftwareWorkspaceControlCheck,
    SoftwareWorktreeManager,
    VerificationIsolationError,
    WorkspaceStaleError,
    WorktreeBranchPublisher,
    software_workspace_precondition,
    workspace_diff,
)
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.fakes_verification import LocalVerificationRunner

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class _RecordingSandboxHandle:
    def __init__(self, backend) -> None:
        self._backend = backend
        self.calls = []
        self.stopped = False

    async def exec(self, tool_call):
        self.calls.append(tool_call)
        snapshot = self._backend.start_kwargs["workdir_mount"]
        assert not (snapshot / "host-secret.txt").exists()
        assert (snapshot / "README.md").read_text() == "changed\n"
        assert (snapshot / "new.txt").read_text() == "new\n"
        (snapshot / "verification-generated.txt").write_text("discard me\n")
        return ToolResult(
            call_id=tool_call.call_id,
            ok=True,
            exit_code=0,
            stdout="sandboxed\n",
        )

    async def stop(self, *, timeout: float = 10.0) -> None:
        del timeout
        assert self._backend.start_kwargs["workdir_mount"].exists()
        self.stopped = True


class _RecordingSandboxBackend:
    name = "recording"

    def __init__(self) -> None:
        self.start_kwargs = None
        self.handle = _RecordingSandboxHandle(self)
        self.closed = False

    async def start(self, **kwargs):
        self.start_kwargs = kwargs
        return self.handle

    async def close(self) -> None:
        self.closed = True


class _FailingSandboxBackend:
    name = "failing"

    def __init__(self) -> None:
        self.closed = False
        self.start_calls = 0

    async def start(self, **kwargs):
        del kwargs
        self.start_calls += 1
        raise RuntimeError("sandbox unavailable")

    async def close(self) -> None:
        self.closed = True


class _StaticSandboxHandle:
    def __init__(self, result: ToolResult, *, stop_error: Exception | None = None) -> None:
        self.result = result
        self.stop_error = stop_error
        self.calls = []
        self.stop_calls = 0

    async def exec(self, tool_call):
        self.calls.append(tool_call)
        return self.result.model_copy(update={"call_id": tool_call.call_id})

    async def stop(self, *, timeout: float = 10.0) -> None:
        del timeout
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error


class _StaticSandboxBackend:
    name = "static"

    def __init__(
        self,
        result: ToolResult,
        *,
        stop_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.handle = _StaticSandboxHandle(result, stop_error=stop_error)
        self.close_error = close_error
        self.close_calls = 0

    async def start(self, **kwargs):
        del kwargs
        return self.handle

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


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


def _verification_contract() -> work.WorkContract:
    return work.WorkContract(
        id="contract-1",
        project_id="project-a",
        work_id="work-1",
        version=1,
        goal="Verify the software change",
        allowed_scope=("README.md",),
        acceptance_criteria=(
            work.AcceptanceCriterion(
                id="criterion-repository",
                project_id="project-a",
                statement="repository outcome is verified",
                verification_kind="deterministic",
            ),
            work.AcceptanceCriterion(
                id="criterion-execution",
                project_id="project-a",
                statement="verification commands pass",
                verification_kind="deterministic",
            ),
            work.AcceptanceCriterion(
                id="criterion-profile",
                project_id="project-a",
                statement="profile action succeeds",
                verification_kind="profile",
            ),
            work.AcceptanceCriterion(
                id="criterion-policy",
                project_id="project-a",
                statement="policy authorizes completion",
                verification_kind="policy",
            ),
        ),
        constraints=(),
        non_goals=(),
        evidence_refs=("issue://1",),
        assumption_ids=(),
        risk="low",
        design_required=False,
        profile_context=SoftwareContractContext(
            project_id="project-a",
            base_sha="a" * 40,
            repository_outcome="verified_commit",
            repository_criterion_id="criterion-repository",
        ).model_dump(mode="json"),
    )


def _result(
    *,
    action_results: tuple[ActionResult, ...] = (),
    output_tokens: int | None = None,
) -> OperatorResult:
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
        output_tokens=output_tokens,
        profile_context={},
    )


def test_software_profile_contexts_round_trip_at_profile_boundary() -> None:
    contract = SoftwareContractContext(
        project_id="project-a",
        base_sha="a" * 40,
        repository_outcome="verified_commit",
        repository_criterion_id="criterion-repository",
    )
    capsule = SoftwareCapsuleContext(
        project_id="project-a",
        base_sha="a" * 40,
        current_sha="b" * 40,
        repo_instructions=("AGENTS.md",),
        verification_commands=("just smoke",),
    )
    attempt = SoftwareAttemptContext(
        project_id="project-a", base_sha="a" * 40, result_sha=None
    )

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
                    project_id="project-a",
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
async def test_workspace_control_check_rejects_foreign_project_capsule(
    tmp_path: Path,
) -> None:
    expected_sha = "a" * 40
    workspace = SoftwareWorkspace(
        ref="workspace://workspace",
        project_id="project-a",
        work_id="work-1",
        attempt_id="workspace",
        repository=tmp_path,
        path=tmp_path,
        base_sha=expected_sha,
        initial_sha=expected_sha,
    )
    result = await SoftwareWorkspaceControlCheck().evaluate(
        SimpleNamespace(
            request=SimpleNamespace(project_id="project-a", work_id="work-1"),
            precondition=software_workspace_precondition(project_id="project-a"),
            capsule=SimpleNamespace(
                profile_context=SoftwareCapsuleContext(
                    project_id="project-b",
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
    assert result.detail == "capsule belongs to a different project"


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
async def test_restore_uncommitted_resets_tracked_cleans_untracked_keeps_ignored_and_refuses_moved_head(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    (repository / ".gitignore").write_text("ignored.txt\n")
    _git(repository, "add", ".gitignore")
    _git(repository, "commit", "-m", "ignore test artifacts")
    base_sha = _git(repository, "rev-parse", "HEAD")
    manager = SoftwareWorktreeManager(root=tmp_path / "worktrees")
    workspace = await manager.prepare(
        repository=repository,
        project_id="project-a",
        work_id="work-1",
        attempt_id="attempt-1",
        base_sha=base_sha,
    )
    (workspace.path / "README.md").write_text("dirty\n")
    (workspace.path / "untracked.txt").write_text("remove\n")
    (workspace.path / "ignored.txt").write_text("keep\n")

    dirty_diff, dirty_files = await workspace_diff(workspace)
    await manager.restore_uncommitted(workspace, expected_sha=base_sha)
    clean_diff, clean_files = await workspace_diff(workspace)

    assert "dirty" in dirty_diff
    assert dirty_files == ("README.md", "untracked.txt")
    assert (workspace.path / "README.md").read_text() == "base\n"
    assert not (workspace.path / "untracked.txt").exists()
    assert (workspace.path / "ignored.txt").read_text() == "keep\n"
    assert clean_diff == ""
    assert clean_files == ()

    (workspace.path / "README.md").write_text("committed\n")
    _git(workspace.path, "add", "README.md")
    _git(workspace.path, "commit", "-m", "move head")
    (workspace.path / "README.md").write_text("dirty after move\n")
    (workspace.path / "after-move.txt").write_text("survives\n")

    with pytest.raises(WorkspaceStaleError, match="workspace HEAD moved"):
        await manager.restore_uncommitted(workspace, expected_sha=base_sha)

    assert (workspace.path / "README.md").read_text() == "dirty after move\n"
    assert (workspace.path / "after-move.txt").read_text() == "survives\n"


@pytest.mark.asyncio
async def test_reviewed_diff_is_canonical_before_and_after_committing_untracked_file(
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
    (workspace.path / "target.txt").write_text("reviewed\n")
    reviewed_diff, reviewed_files = await workspace_diff(workspace)
    reviewed_digest = f"sha256:{hashlib.sha256(reviewed_diff.encode()).hexdigest()}"

    result_sha = await manager.commit_reviewed(
        workspace,
        expected_sha=base_sha,
        expected_diff_digest=reviewed_digest,
        commit_message="sagewai work work-1",
    )
    committed_diff, committed_files = await workspace_diff(workspace)

    assert result_sha != base_sha
    assert committed_diff == reviewed_diff
    assert committed_files == reviewed_files == ("target.txt",)

@pytest.mark.asyncio
async def test_workspace_diff_captures_content_beyond_preview_limit(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    workspace = await SoftwareWorktreeManager(root=tmp_path / "worktrees").prepare(
        repository=repository,
        project_id="project-a",
        work_id="work-1",
        attempt_id="attempt-1",
        base_sha=base_sha,
    )
    tail_marker = "reviewed-tail-marker"
    (workspace.path / "target.txt").write_text(f"{'x' * 100_100}{tail_marker}\n")

    reviewed_diff, reviewed_files = await workspace_diff(workspace)

    assert len(reviewed_diff) > 100_000
    assert tail_marker in reviewed_diff
    assert reviewed_files == ("target.txt",)



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
            project_id="project-a",
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


@pytest.mark.asyncio
async def test_post_run_validator_accepts_recursive_directory_target(
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
    nested = workspace.path / "test-apps" / "adaptive-intelligence-platform"
    nested.mkdir(parents=True)
    (nested / "index.html").write_text("<!doctype html>\n")
    target = "test-apps/adaptive-intelligence-platform/**"
    request = WorkRequest(
        project_id="project-a",
        work_id="work-1",
        run_id="run-1",
        stage="implement",
        action_scope=ActionScope(
            project_id="project-a",
            objective="Create the bounded browser application",
            allowed_targets=(target,),
            allowed_capabilities=("filesystem.write",),
        ),
        action_intents=(
            ActionIntent(
                project_id="project-a",
                action_id="action-1",
                capability="filesystem.write",
                target=target,
                expected_effect="Create the application files",
                scope={"allowed_targets": [target]},
                risk="low",
                reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
                required_permission="workspace.write",
                evidence_refs=("contract://1",),
            ),
        ),
        control_preconditions=(),
    )
    result = _result().model_copy(
        update={
            "action_results": (
                ActionResult(
                    project_id="project-a",
                    action_id="action-1",
                    status="succeeded",
                    external_ref=None,
                    evidence_refs=("test-apps/adaptive-intelligence-platform/index.html",),
                    started_at=NOW,
                    completed_at=NOW,
                ),
            )
        }
    )

    report = await SoftwareResultValidator().validate(
        request=request,
        result=result,
        workspace=workspace,
    )

    assert report.verdict == "pass"
    assert report.scope_violations == ()

    sibling = workspace.path / "test-apps" / "adaptive-intelligence-platform-old"
    sibling.mkdir(parents=True)
    (sibling / "index.html").write_text("<!doctype html>\n")
    (workspace.path / "outside.txt").write_text("outside\n")

    report = await SoftwareResultValidator().validate(
        request=request,
        result=result,
        workspace=workspace,
    )

    assert report.verdict == "blocked"
    assert (
        "test-apps/adaptive-intelligence-platform-old/index.html "
        "is outside allowed targets"
    ) in report.scope_violations
    assert "outside.txt is outside allowed targets" in report.scope_violations
    assert not any(
        violation.startswith("test-apps/adaptive-intelligence-platform/index.html ")
        for violation in report.scope_violations
    )


@pytest.mark.asyncio
async def test_result_validator_does_not_run_worktree_git_filters(tmp_path: Path) -> None:
    repository, base_sha = _repository(tmp_path)
    (repository / ".gitattributes").write_text("*.txt filter=escape\n")
    (repository / "tracked.txt").write_text("base\n")
    _git(repository, "add", ".gitattributes", "tracked.txt")
    _git(repository, "commit", "-m", "declare filter")
    base_sha = _git(repository, "rev-parse", "HEAD")
    marker = tmp_path / "validator-filter-executed"
    _git(
        repository,
        "config",
        "filter.escape.clean",
        f"sh -c 'touch {shlex.quote(str(marker))}; cat'",
    )
    (repository / "tracked.txt").write_text("operator output\n")
    workspace = SoftwareWorkspace(
        ref="workspace://attempt-1",
        project_id="project-a",
        work_id="work-1",
        attempt_id="attempt-1",
        repository=repository,
        path=repository,
        base_sha=base_sha,
        initial_sha=base_sha,
    )
    request = WorkRequest(
        project_id="project-a",
        work_id="work-1",
        run_id="run-1",
        stage="implement",
        action_scope=ActionScope(
            project_id="project-a",
            objective="Change tracked.txt",
            allowed_targets=("tracked.txt",),
        ),
        action_intents=(),
        control_preconditions=(),
    )

    report = await SoftwareResultValidator().validate(
        request=request,
        result=_result(),
        workspace=workspace,
    )

    assert report.changed_files == 1
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "validator",
    [SoftwareResultValidator(), SoftwareReadOnlyResultValidator()],
)
async def test_result_validator_records_runtime_output_tokens(
    validator,
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    workspace = SoftwareWorkspace(
        ref="workspace://attempt-1",
        project_id="project-a",
        work_id="work-1",
        attempt_id="attempt-1",
        repository=repository,
        path=repository,
        base_sha=base_sha,
        initial_sha=base_sha,
    )
    request = WorkRequest(
        project_id="project-a",
        work_id="work-1",
        run_id="run-1",
        stage="review",
        action_scope=ActionScope(
            project_id="project-a", objective="Review the implementation", allowed_targets=()
        ),
        action_intents=(),
        control_preconditions=(),
    )

    report = await validator.validate(
        request=request,
        result=_result(output_tokens=73),
        workspace=workspace,
    )

    assert report.output_tokens == 73


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "image",
    [
        "example.invalid/verifier@sha256:" + "a" * 64,
        "sha256:" + "a" * 64,
    ],
)
async def test_verification_runs_in_disposable_networkless_sandbox(
    tmp_path: Path,
    image: str,
) -> None:
    repository, base_sha = _repository(tmp_path)
    (repository / ".gitattributes").write_text("*.md filter=escape\n")
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-m", "declare filter")
    base_sha = _git(repository, "rev-parse", "HEAD")
    host_marker = tmp_path / "host-filter-executed"
    _git(
        repository,
        "config",
        "filter.escape.clean",
        f"sh -c 'touch {shlex.quote(str(host_marker))}; cat'",
    )
    (repository / "README.md").write_text("changed\n")
    (repository / "new.txt").write_text("new\n")
    (repository / ".git" / "info" / "exclude").write_text("host-secret.txt\n")
    (repository / "host-secret.txt").write_text("outside verification scope\n")
    workspace = SoftwareWorkspace(
        ref="workspace://verify",
        project_id="project-a",
        work_id="work-1",
        attempt_id="verify",
        repository=repository,
        path=repository,
        base_sha=base_sha,
        initial_sha=base_sha,
    )
    backend = _RecordingSandboxBackend()
    command = ("python", "-c", "print('quoted value')")
    runner = SandboxedVerificationRunner(
        image=image,
        backend_factory=lambda: backend,
    )

    results = await runner.run(
        project_id="project-a",
        work_id="work-1",
        attempt_id="attempt-1",
        workspace=workspace,
        commands=(command,),
        timeout=30,
    )

    assert len(results) == 1
    assert results[0].returncode == 0
    assert results[0].stdout == "sandboxed\n"
    assert backend.start_kwargs["project_id"] == "project-a"
    assert backend.start_kwargs["run_id"] == "verify-work-1-attempt-1"
    assert backend.start_kwargs["env"] == {}
    assert backend.start_kwargs["network_policy"] is NetworkPolicy.NONE
    assert backend.start_kwargs["lifetime"] is SandboxLifetime.PER_RUN
    assert backend.start_kwargs["image_digest"] == "sha256:" + "a" * 64
    assert backend.start_kwargs["user"] == f"{os.getuid()}:{os.getgid()}"
    snapshot = backend.start_kwargs["workdir_mount"]
    assert not snapshot.exists()
    assert backend.handle.calls[0].args == {"command": shlex.join(command)}
    assert backend.handle.stopped is True
    assert backend.closed is True
    assert not (repository / "verification-generated.txt").exists()
    assert not host_marker.exists()
    assert (repository / "host-secret.txt").read_text() == "outside verification scope\n"


@pytest.mark.asyncio
async def test_verification_sandbox_unavailability_fails_closed(tmp_path: Path) -> None:
    repository, base_sha = _repository(tmp_path)
    workspace = SoftwareWorkspace(
        ref="workspace://verify",
        project_id="project-a",
        work_id="work-1",
        attempt_id="verify",
        repository=repository,
        path=repository,
        base_sha=base_sha,
        initial_sha=base_sha,
    )
    backend = _FailingSandboxBackend()
    runner = SandboxedVerificationRunner(
        image="example.invalid/verifier@sha256:" + "a" * 64,
        backend_factory=lambda: backend,
    )
    escaped = repository / "host-command-ran"
    command = (
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(escaped)!r}).write_text('escaped')",
    )

    with pytest.raises(VerificationIsolationError, match="failed to start"):
        await runner.run(
            project_id="project-a",
            work_id="work-1",
            attempt_id="attempt-1",
            workspace=workspace,
            commands=(command,),
            timeout=30,
        )

    assert backend.start_calls == 1
    assert backend.closed is True
    assert not escaped.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "stop_error", "close_error", "expected_error", "expected_returncode"),
    [
        (
            ToolResult(call_id="unused", ok=False, error="sandbox response timeout"),
            None,
            None,
            "execution receipt",
            None,
        ),
        (
            ToolResult(call_id="unused", ok=False, error="timeout after 30s"),
            None,
            None,
            None,
            124,
        ),
        (ToolResult(call_id="unused", ok=False, exit_code=0), None, None, "receipt", None),
        (ToolResult(call_id="unused", ok=False, exit_code=1), None, None, None, 1),
        (
            ToolResult(call_id="unused", ok=True, exit_code=0),
            RuntimeError("stop failed"),
            None,
            "cleanup failed",
            None,
        ),
        (
            ToolResult(call_id="unused", ok=True, exit_code=0),
            None,
            RuntimeError("close failed"),
            "cleanup failed",
            None,
        ),
    ],
)
async def test_verification_distinguishes_command_failure_from_control_loss(
    tmp_path: Path,
    result: ToolResult,
    stop_error: Exception | None,
    close_error: Exception | None,
    expected_error: str | None,
    expected_returncode: int | None,
) -> None:
    repository, base_sha = _repository(tmp_path)
    workspace = SoftwareWorkspace(
        ref="workspace://verify",
        project_id="project-a",
        work_id="work-1",
        attempt_id="verify",
        repository=repository,
        path=repository,
        base_sha=base_sha,
        initial_sha=base_sha,
    )
    backend = _StaticSandboxBackend(
        result,
        stop_error=stop_error,
        close_error=close_error,
    )
    runner = SandboxedVerificationRunner(
        image="example.invalid/verifier@sha256:" + "a" * 64,
        backend_factory=lambda: backend,
    )
    run = runner.run(
        project_id="project-a",
        work_id="work-1",
        attempt_id="attempt-1",
        workspace=workspace,
        commands=(("true",),),
        timeout=30,
    )

    if expected_error is not None:
        with pytest.raises(VerificationIsolationError, match=expected_error):
            await run
    else:
        processes = await run
        assert processes[0].returncode == expected_returncode

        assert processes[0].timed_out is (expected_returncode == 124)
    assert backend.handle.stop_calls == 1
    assert backend.close_calls == 1

@pytest.mark.asyncio
async def test_docker_handle_reports_failed_force_delete() -> None:
    from sagewai.sandbox.docker_backend import DockerSandboxHandle

    class FailingContainer:
        def __init__(self) -> None:
            self.delete_calls = 0

        async def stop(self, *, timeout: int) -> None:
            del timeout
            raise RuntimeError("stop failed")

        async def delete(self, *, force: bool) -> None:
            assert force is True
            self.delete_calls += 1
            raise RuntimeError("delete failed")

    container = FailingContainer()
    handle = DockerSandboxHandle(
        client=object(),
        container=container,
        image="example.invalid/verifier",
        image_digest="sha256:" + "a" * 64,
        sandbox_id="verification-test",
        docker_bin="docker",
    )

    with pytest.raises(RuntimeError, match="failed to delete sandbox"):
        await handle.stop()

    assert container.delete_calls == 1



@pytest.mark.asyncio
async def test_verification_rejects_untracked_embedded_repository(tmp_path: Path) -> None:
    repository, base_sha = _repository(tmp_path)
    nested = repository / "nested"
    nested.mkdir()
    _git(nested, "init")
    (nested / ".gitignore").write_text("secret.txt\n")
    (nested / "secret.txt").write_text("must not enter verification\n")
    workspace = SoftwareWorkspace(
        ref="workspace://verify",
        project_id="project-a",
        work_id="work-1",
        attempt_id="verify",
        repository=repository,
        path=repository,
        base_sha=base_sha,
        initial_sha=base_sha,
    )
    backend = _RecordingSandboxBackend()
    runner = SandboxedVerificationRunner(
        image="example.invalid/verifier@sha256:" + "a" * 64,
        backend_factory=lambda: backend,
    )

    with pytest.raises(VerificationIsolationError, match="untracked directory"):
        await runner.run(
            project_id="project-a",
            work_id="work-1",
            attempt_id="attempt-1",
            workspace=workspace,
            commands=(("true",),),
            timeout=30,
        )

    assert backend.start_kwargs is None
    assert backend.closed is True


def test_verification_requires_digest_pinned_image() -> None:
    with pytest.raises(ValueError, match="digest-pinned"):
        SandboxedVerificationRunner(image="example.invalid/verifier:latest")


@pytest.mark.asyncio
async def test_large_verification_output_is_deduplicated_artifact_evidence(
    dialect_engine,  # noqa: F811
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    workspace = SoftwareWorkspace(
        ref="workspace://verify",
        project_id="project-a",
        work_id="work-1",
        attempt_id="verify",
        repository=repository,
        path=repository,
        base_sha=base_sha,
        initial_sha=base_sha,
    )
    work_item = WorkItem(
        id="work-1",
        project_id="project-a",
        profile="software",
        source="local",
        source_ref=None,
        title="Verify output",
        description="Store large verification output",
        created_at=NOW,
    )
    knowledge_store = KnowledgeStore(engine=dialect_engine)
    await knowledge_store.init()
    object_root = tmp_path / "objects"
    verifier = SoftwareVerifier(
        knowledge_store=knowledge_store,
        runner=LocalVerificationRunner(),
        artifact_store=LocalArtifactStore(root=object_root),
    )
    command = f"{sys.executable} -c \"import sys; sys.stdout.write('x' * 5001)\""

    result = await verifier.verify(
        run_id="work-1:verify:1",
        work_item=work_item,
        contract=_verification_contract(),
        criterion_ids=("criterion-execution",),
        attempt_id="attempt-1",
        workspace=workspace,
        commands=(command, command),
    )

    checks = tuple(
        SoftwareVerificationCheck.model_validate(check)
        for check in result.profile_context["checks"]
    )
    assert result.passed is True
    assert result.project_id == "project-a"
    assert all(check.project_id == "project-a" for check in checks)
    assert result.contract_id == "contract-1"
    assert result.stage == "verification"
    assert tuple(item.criterion_id for item in result.criterion_results) == ("criterion-execution",)
    assert result.criterion_results[0].evidence_refs == result.evidence_refs
    assert checks[0].artifact_ref is not None
    assert checks[1].artifact_ref == checks[0].artifact_ref
    items = [
        await knowledge_store.get(item_id, project_id="project-a")
        for item_id in result.evidence_refs
    ]
    assert all(item is not None for item in items)
    assert all(item.project_id == "project-a" for item in items if item is not None)
    assert all(item.work_id == "work-1" for item in items if item is not None)
    assert all(item.created_by == "software.verifier" for item in items if item is not None)
    assert all(
        item.artifact_refs == (checks[0].artifact_ref,) for item in items if item is not None
    )
    assert all("x" * 100 not in item.statement for item in items if item is not None)
    stored_files = [path for path in object_root.rglob("*") if path.is_file()]
    resolved = LocalArtifactStore(root=object_root).resolve(
        checks[0].artifact_ref, project_id="project-a"
    )
    assert resolved in stored_files
    assert len({(path.stat().st_dev, path.stat().st_ino) for path in stored_files}) == 1
    assert (
        LocalArtifactStore(root=object_root).read(checks[0].artifact_ref, project_id="project-a")
        == ("stdout:\n" + "x" * 5001 + "\nstderr:\n").encode()
    )


@pytest.mark.asyncio
async def test_small_verification_output_remains_inline(
    dialect_engine,  # noqa: F811
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    workspace = SoftwareWorkspace(
        ref="workspace://verify",
        project_id="project-a",
        work_id="work-1",
        attempt_id="verify",
        repository=repository,
        path=repository,
        base_sha=base_sha,
        initial_sha=base_sha,
    )
    work_item = WorkItem(
        id="work-1",
        project_id="project-a",
        profile="software",
        source="local",
        source_ref=None,
        title="Verify output",
        description="Keep small verification output inline",
        created_at=NOW,
    )
    knowledge_store = KnowledgeStore(engine=dialect_engine)
    await knowledge_store.init()
    verifier = SoftwareVerifier(
        knowledge_store=knowledge_store,
        runner=LocalVerificationRunner(),
        artifact_store=LocalArtifactStore(root=tmp_path / "objects"),
    )
    command = f"{sys.executable} -c \"print('small-output')\""

    result = await verifier.verify(
        run_id="work-1:verify:2",
        work_item=work_item,
        contract=_verification_contract(),
        criterion_ids=("criterion-execution",),
        attempt_id="attempt-1",
        workspace=workspace,
        commands=(command,),
    )

    check = SoftwareVerificationCheck.model_validate(result.profile_context["checks"][0])
    item = await knowledge_store.get(result.evidence_refs[0], project_id="project-a")
    assert check.artifact_ref is None
    assert item is not None
    assert item.artifact_refs == ()
    assert "stdout:\nsmall-output" in item.statement
    assert not (tmp_path / "objects").exists()
    for mismatched_id in ("criterion-repository", "criterion-profile", "criterion-policy"):
        with pytest.raises(ValueError, match="criterion subset"):
            await verifier.verify(
        run_id="work-1:verify:3",
                work_item=work_item,
                contract=_verification_contract(),
                criterion_ids=(mismatched_id,),
                attempt_id="attempt-1",
                workspace=workspace,
                commands=(command,),
            )
