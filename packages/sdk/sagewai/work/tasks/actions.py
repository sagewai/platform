# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Action records and the rollback recipes the coordinator executes (spec section 8.8)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from sagewai.fleet.execution import run_worker_subprocess
from sagewai.work.models import ActionRequest, ActionResult, Reversibility
from sagewai.work.profiles.software.github import (
    GitHubFactory,
    is_github_comment_url,
    parse_comment_url,
    parse_pull_request_url,
)
from sagewai.work.profiles.software.scm import (
    SoftwareWorktreeManager,
    fetch_default_branch_head,
)
from sagewai.work.tasks.models import SoftwareTarget, Task

_POST_CHECKS = {
    "revert_pull_request": "merged_sha_read_back",
    "delete_comment": "comment_deleted",
}


class RollbackRefusedError(ValueError):
    """The declared rollback recipe is unknown or cannot be executed as recorded."""


class DeliveryReceipt(BaseModel):
    """One sink's declared action, its receipt, and its post-check (spec section 8.8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ActionRequest
    result: ActionResult
    observation: dict[str, Any]


def deliver_action(
    project_id: str,
    *,
    work_id: str,
    scope: str,
    evidence_refs: tuple[str, ...],
    rollback: str | None,
) -> ActionRequest:
    """The action record for one report delivery (spec section 12 step 4)."""
    return ActionRequest(
        project_id=project_id,
        action="deliver",
        work_id=work_id,
        risk="medium" if rollback else "low",
        reversibility=(
            Reversibility.COMPENSATABLE if rollback else Reversibility.SNAPSHOT_REVERSIBLE
        ),
        scope=scope,
        evidence_refs=evidence_refs,
        rollback=rollback,
        post_check="comment_read_back" if rollback else "artifact_read_back",
    )


def deliver_action_id(work_id: str, *, sink_version: int) -> str:
    return f"deliver:{work_id}:{sink_version}"


def rollback_action(action: ActionRequest) -> ActionRequest:
    """The action record for executing ACTION's declared rollback recipe."""
    recipe = action.rollback
    if recipe not in _POST_CHECKS:
        raise RollbackRefusedError(f"unknown rollback recipe: {recipe!r}")
    return ActionRequest(
        project_id=action.project_id,
        action=recipe,
        work_id=action.work_id,
        risk=action.risk,
        reversibility=_reversibility(recipe, action.scope),
        scope=action.scope,
        evidence_refs=action.evidence_refs,
        post_check=_POST_CHECKS[recipe],
    )


def rollback_action_id(action: ActionRequest) -> str:
    """A stable key derived from the target the recipe acts on."""
    if action.rollback == "revert_pull_request":
        try:
            _owner, _repo, number = parse_pull_request_url(action.scope)
        except ValueError as exc:
            raise RollbackRefusedError(str(exc)) from exc
        return f"revert:{action.work_id}:{number}"
    if action.rollback == "delete_comment" and is_github_comment_url(action.scope):
        _owner, _repo, comment_id = parse_comment_url(action.scope)
        return f"delete_comment:{action.work_id}:{comment_id}"
    raise RollbackRefusedError(f"no stable key for rollback {action.rollback!r} on {action.scope}")


def _reversibility(recipe: str, scope: str) -> Reversibility:
    """A comment rollback needs the comment's own permalink; without it nothing undoes it."""
    if recipe == "delete_comment" and not is_github_comment_url(scope):
        return Reversibility.IRREVERSIBLE
    return Reversibility.COMPENSATABLE


class RollbackExecutor:
    """Run one declared rollback recipe and return its receipt and post-check."""

    def __init__(
        self,
        *,
        github_factory: GitHubFactory,
        worktrees: SoftwareWorktreeManager | None = None,
    ) -> None:
        self._github_factory = github_factory
        self._worktrees = worktrees or SoftwareWorktreeManager()

    async def run(
        self,
        scope: Task,
        *,
        action: ActionRequest,
        action_id: str,
        merged_sha: str | None = None,
        issue_url: str | None = None,
    ) -> tuple[ActionResult, dict[str, Any]]:
        """Run the action being rolled back; dispatch uses the action's rollback recipe."""
        started = datetime.now(timezone.utc)
        if action.rollback == "revert_pull_request":
            external_ref, evidence, observation = await self._revert_merge(
                scope, action, merged_sha, issue_url
            )
        elif action.rollback == "delete_comment":
            external_ref, evidence, observation = await self._delete_comment(scope, action)
        else:
            raise RollbackRefusedError(f"unknown rollback recipe: {action.rollback!r}")
        return (
            ActionResult(
                project_id=scope.project_id,
                action_id=action_id,
                status="succeeded" if observation["passed"] else "failed",
                external_ref=external_ref,
                evidence_refs=evidence,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            ),
            {"action_id": action_id, **observation},
        )

    async def _revert_merge(
        self,
        scope: Task,
        action: ActionRequest,
        merged_sha: str | None,
        issue_url: str | None,
    ) -> tuple[str, tuple[str, ...], dict[str, Any]]:
        """Section 8.8: undo the merge with Git, then open and merge the revert (decision 1)."""
        if merged_sha is None:
            raise RollbackRefusedError("the merge recorded no merged commit; nothing to revert")
        if issue_url is None:
            raise RollbackRefusedError("the revert pull request needs the Work's issue")
        target = scope.target
        if not isinstance(target, SoftwareTarget):
            raise RollbackRefusedError("the merge rollback needs a software repository")
        repository_path = target.repository_path
        _owner, _repo, number = parse_pull_request_url(action.scope)
        repository = Path(repository_path)
        try:
            head = await fetch_default_branch_head(repository, target.default_branch)
        except ValueError as exc:
            raise RollbackRefusedError(str(exc)) from exc
        branch = f"sagewai/revert-{number}-{merged_sha[:12]}"
        workspace = await self._worktrees.prepare(
            repository=repository,
            project_id=scope.project_id,
            work_id=f"revert-{action.work_id}-{number}",
            attempt_id=merged_sha[:12],
            base_sha=head,
        )
        try:
            reverted = await run_worker_subprocess(
                argv=("git", "revert", "--no-edit", "-m", "1", merged_sha),
                cwd=workspace.path,
            )
            if reverted.returncode != 0:
                raise RollbackRefusedError(
                    f"git revert of {merged_sha} failed: {reverted.stderr.strip()}"
                )
            await self._worktrees.publish_branch(
                workspace,
                branch=branch,
                commit_message=f"Revert pull request #{number}",
            )
        finally:
            await self._worktrees.release(workspace)

        github = self._github_factory(scope)
        issue = await github.fetch_issue(issue_url)
        revert = await github.create_pull_request(
            issue=issue,
            title=f"Revert pull request #{number}",
            head=branch,
            base=target.default_branch,
            body=(
                f"Reverts {action.scope}.\n\n"
                f"The merge post-check on {merged_sha} failed and an operator allowed the "
                "recorded rollback recipe."
            ),
        )
        merge = await github.merge_pull_request(revert, expected_head_sha=revert.head_sha)
        state = await github.get_pull_request(revert)
        passed = state.merged and state.merge_commit_sha == merge.merged_sha
        return (
            revert.url,
            (action.scope, revert.url, f"git://{merge.merged_sha}"),
            {
                "check": "merged_sha_read_back",
                "passed": passed,
                "detail": (
                    f"revert pull request #{revert.number} reported merged={state.merged} "
                    f"at {state.merge_commit_sha}"
                ),
                "evidence_refs": [revert.url],
            },
        )

    async def _delete_comment(
        self,
        scope: Task,
        action: ActionRequest,
    ) -> tuple[str, tuple[str, ...], dict[str, Any]]:
        if not is_github_comment_url(action.scope):
            raise RollbackRefusedError(f"no comment permalink to delete: {action.scope}")
        await self._github_factory(scope).delete_comment(action.scope)
        return (
            action.scope,
            (action.scope,),
            {
                "check": "comment_deleted",
                "passed": True,
                "detail": f"deleted {action.scope}",
                "evidence_refs": [action.scope],
            },
        )


__all__ = [
    "DeliveryReceipt",
    "RollbackExecutor",
    "RollbackRefusedError",
    "deliver_action",
    "deliver_action_id",
    "rollback_action",
    "rollback_action_id",
]
