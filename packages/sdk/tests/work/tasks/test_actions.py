# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Rollback recipes are executed by the coordinator, never chosen by a model."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sagewai.work.models import ActionRequest, GateDecision, Reversibility
from sagewai.work.profiles.software.scm import SoftwareWorktreeManager
from sagewai.work.tasks.actions import (
    RollbackExecutor,
    RollbackRefusedError,
    deliver_action,
    deliver_action_id,
    rollback_action,
    rollback_action_id,
)
from sagewai.work.tasks.decisions import resolve_gate
from sagewai.work.tasks.models import GateMode, ReportTarget, SoftwareTarget
from tests.work.tasks.test_software_kernel import RecordingGitHub

PROJECT = "project-a"
PULL_URL = "https://github.com/octocat/hello-world/pull/7"
ISSUE_URL = "https://github.com/octocat/hello-world/issues/42"
COMMENT_URL = f"{ISSUE_URL}#issuecomment-991"


def _git(cwd: Path, *argv: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(cwd), *argv),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def merged_repository(tmp_path: Path) -> tuple[Path, str]:
    """A clone of an origin whose main branch ends in a real merge commit."""
    origin = tmp_path / "origin.git"
    subprocess.run(("git", "init", "-q", "--bare", "-b", "main", str(origin)), check=True)
    repository = tmp_path / "repository"
    subprocess.run(("git", "clone", "-q", str(origin), str(repository)), check=True)
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    (repository / "source.txt").write_text("base\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "base")
    _git(repository, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(repository, "checkout", "-q", "-b", "feature")
    (repository / "source.txt").write_text("base\nfeature\n")
    _git(repository, "commit", "-qam", "feature")
    _git(repository, "checkout", "-q", "main")
    _git(repository, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")
    _git(repository, "push", "-q", "origin", "HEAD:refs/heads/main")
    return repository, _git(repository, "rev-parse", "HEAD")


def _target(repository: Path) -> SoftwareTarget:
    return SoftwareTarget(
        repository_path=str(repository),
        owner="octocat",
        repo="hello-world",
        default_branch="main",
        verification_image="sha256:" + "a" * 64,
    )


class _Task:
    """Only what the executor reads: a project and, for a revert, a software target."""

    id = "t1"
    project_id = PROJECT

    def __init__(self, target: object | None = None) -> None:
        self.target = target


def _merge_action() -> ActionRequest:
    return ActionRequest(
        project_id=PROJECT,
        action="merge",
        work_id="w1",
        risk="medium",
        reversibility=Reversibility.COMPENSATABLE,
        scope=PULL_URL,
        evidence_refs=(ISSUE_URL,),
        rollback="revert_pull_request",
        post_check="merged_sha_read_back",
    )


def _deliver_action(scope: str) -> ActionRequest:
    return ActionRequest(
        project_id=PROJECT,
        action="deliver",
        work_id="w1",
        risk="medium",
        reversibility=Reversibility.COMPENSATABLE,
        scope=scope,
        evidence_refs=(),
        rollback="delete_comment",
        post_check="comment_read_back",
    )


def test_action_ids_are_stable_keys() -> None:
    assert deliver_action_id("w1", sink_version=2) == "deliver:w1:2"
    assert rollback_action_id(_merge_action()) == "revert:w1:7"
    assert rollback_action_id(_deliver_action(COMMENT_URL)) == "delete_comment:w1:991"


def test_deliver_action_records_sink_risk_reversibility_and_post_check() -> None:
    with_rollback = deliver_action(
        PROJECT,
        work_id="w1",
        scope=COMMENT_URL,
        evidence_refs=("artifact://report",),
        rollback="delete_comment",
    )
    without_rollback = deliver_action(
        PROJECT,
        work_id="w1",
        scope="console://stdout",
        evidence_refs=("artifact://report",),
        rollback=None,
    )

    assert with_rollback.risk == "medium"
    assert with_rollback.reversibility is Reversibility.COMPENSATABLE
    assert with_rollback.post_check == "comment_read_back"
    assert without_rollback.risk == "low"
    assert without_rollback.reversibility is Reversibility.SNAPSHOT_REVERSIBLE
    assert without_rollback.post_check == "artifact_read_back"


def test_a_comment_rollback_without_a_comment_url_is_irreversible_and_gates() -> None:
    known = rollback_action(_deliver_action(COMMENT_URL))
    unknown = rollback_action(_deliver_action(ISSUE_URL))

    assert known.reversibility is Reversibility.COMPENSATABLE
    assert unknown.reversibility is Reversibility.IRREVERSIBLE
    assert resolve_gate(GateMode.BY_REVERSIBILITY, unknown) is GateDecision.REQUIRE_APPROVAL


def test_an_unknown_recipe_is_refused() -> None:
    with pytest.raises(RollbackRefusedError):
        rollback_action(_merge_action().model_copy(update={"rollback": "delete_the_repo"}))


@pytest.mark.asyncio
async def test_revert_reverts_the_merge_opens_merges_and_reads_it_back(
    merged_repository: tuple[Path, str],
    tmp_path: Path,
) -> None:
    repository, merged_sha = merged_repository
    github = RecordingGitHub()
    github.labeled_issues = (github.issue,)
    worktrees = SoftwareWorktreeManager(root=tmp_path / "worktrees")
    executor = RollbackExecutor(github_factory=lambda _scope: github, worktrees=worktrees)

    result, observation = await executor.run(
        _Task(_target(repository)),
        action=_merge_action(),
        action_id="revert:w1:7",
        merged_sha=merged_sha,
        issue_url=ISSUE_URL,
    )

    branch = github.pull_requests[0]["head"]
    assert branch == f"sagewai/revert-7-{merged_sha[:12]}"
    reverted = _git(repository, "show", f"origin/{branch}:source.txt")
    assert reverted == "base"
    assert github.merges[0]["expected_head_sha"] == github.remote_pull_request.head_sha
    assert result.status == "succeeded"
    assert observation["check"] == "merged_sha_read_back"
    assert observation["passed"] is True
    assert f"git://{github.merged_sha}" in result.evidence_refs
    assert not any((tmp_path / "worktrees").rglob("source.txt"))  # the worktree was released


@pytest.mark.asyncio
async def test_revert_readback_mismatch_fails_the_post_check(
    merged_repository: tuple[Path, str],
    tmp_path: Path,
) -> None:
    repository, merged_sha = merged_repository
    github = RecordingGitHub()
    github.labeled_issues = (github.issue,)
    github.readback_sha = "d" * 40
    executor = RollbackExecutor(
        github_factory=lambda _scope: github,
        worktrees=SoftwareWorktreeManager(root=tmp_path / "worktrees"),
    )

    result, observation = await executor.run(
        _Task(_target(repository)),
        action=_merge_action(),
        action_id="revert:w1:7",
        merged_sha=merged_sha,
        issue_url=ISSUE_URL,
    )

    assert result.status == "failed"
    assert observation["check"] == "merged_sha_read_back"
    assert observation["passed"] is False


@pytest.mark.asyncio
async def test_a_revert_without_a_merged_sha_is_refused(
    merged_repository: tuple[Path, str],
    tmp_path: Path,
) -> None:
    repository, _merged_sha = merged_repository
    executor = RollbackExecutor(
        github_factory=lambda _scope: RecordingGitHub(),
        worktrees=SoftwareWorktreeManager(root=tmp_path / "worktrees"),
    )

    with pytest.raises(RollbackRefusedError, match="no merged commit"):
        await executor.run(
            _Task(_target(repository)),
            action=_merge_action(),
            action_id="revert:w1:7",
            merged_sha=None,
            issue_url=ISSUE_URL,
        )


@pytest.mark.asyncio
async def test_a_revert_for_a_report_task_is_refused(
    merged_repository: tuple[Path, str],
    tmp_path: Path,
) -> None:
    _repository, merged_sha = merged_repository
    executor = RollbackExecutor(
        github_factory=lambda _scope: RecordingGitHub(),
        worktrees=SoftwareWorktreeManager(root=tmp_path / "worktrees"),
    )

    with pytest.raises(RollbackRefusedError, match="needs a software repository"):
        await executor.run(
            _Task(ReportTarget(required_sections=("Summary",))),
            action=_merge_action(),
            action_id="revert:w1:7",
            merged_sha=merged_sha,
            issue_url=ISSUE_URL,
        )


@pytest.mark.asyncio
async def test_delete_comment_removes_the_delivered_comment(tmp_path: Path) -> None:
    github = RecordingGitHub()
    executor = RollbackExecutor(
        github_factory=lambda _scope: github,
        worktrees=SoftwareWorktreeManager(root=tmp_path / "worktrees"),
    )

    result, observation = await executor.run(
        _Task(),
        action=_deliver_action(COMMENT_URL),
        action_id="delete_comment:w1:991",
    )

    assert github.deleted_comments == [COMMENT_URL]
    assert (result.status, observation["check"]) == ("succeeded", "comment_deleted")
