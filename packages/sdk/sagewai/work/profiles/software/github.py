# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""GitHub issue intake, pull-request publication, and policy-gated merge."""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from sagewai.fleet.execution import run_worker_subprocess
from sagewai.work.completion import evaluate_completion, validate_verification_result
from sagewai.work.contract import AcceptanceCriterion, WorkContract
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import (
    SUPERSEDED,
    ActionRequest,
    CompletionEvaluation,
    CriterionVerification,
    GateDecision,
    PendingAttention,
    PendingAttentionKind,
    Reversibility,
    VerificationResult,
    WorkItem,
    WorkRecord,
)
from sagewai.work.profiles.software.lifecycle import expected_result_sha
from sagewai.work.profiles.software.models import (
    SoftwareContractContext,
    SoftwareRepositoryOutcome,
)
from sagewai.work.profiles.software.scm import SoftwareWorktreeManager
from sagewai.work.store import WorkStore

_ISSUE_PATH = re.compile(
    r"^/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/"
    r"issues/(?P<number>[1-9][0-9]*)/?$"
)
_PULL_PATH = re.compile(
    r"^/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/"
    r"pull/(?P<number>[1-9][0-9]*)/?$"
)
_COMMENT_FRAGMENT = re.compile(r"^issuecomment-(?P<comment_id>[1-9][0-9]*)$")


class GitHubMergeRejectedError(RuntimeError):
    """GitHub deterministically refused an otherwise authorized merge."""


class BaseMovedError(ValueError):
    """The default branch moved away from the pinned base SHA."""

    def __init__(self, *, expected: str, found: str) -> None:
        super().__init__(
            "requested base does not match GitHub default branch: "
            f"expected {expected}, found {found}"
        )
        self.expected = expected
        self.found = found


class GitHubIssue(BaseModel):
    """One GitHub issue normalized for software Work intake."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    owner: str
    repo: str
    number: int
    url: str
    title: str
    body: str
    default_branch: str


class GitHubPullRequest(BaseModel):
    """One pull request created for a canonical WorkItem."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    owner: str
    repo: str
    number: int
    url: str
    head: str
    head_sha: str
    base: str


class GitHubComment(BaseModel):
    """One issue comment, identified so it can be deleted again."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    id: int
    url: str
    body: str


class GitHubPullRequestState(BaseModel):
    """Current merge state read directly from GitHub."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    pull_request_number: int
    merged: bool
    merge_commit_sha: str | None


class GitHubMergeResult(BaseModel):
    """The immutable SHA returned by a successful GitHub merge."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    pull_request_number: int
    merged_sha: str


class GitHubWorkContext(BaseModel):
    """GitHub-specific current projection nested under profile context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    owner: str
    repo: str
    issue_number: int
    issue_url: str
    default_branch: str
    branch: str
    branch_sha: str
    pull_request_number: int
    pull_request_url: str
    merged_sha: str | None = None


class GitHubClient(Protocol):
    """Small lifecycle-facing boundary over the existing GitHub tool."""

    async def list_labeled_issues(
        self,
        *,
        owner: str,
        repo: str,
        label: str,
    ) -> tuple[GitHubIssue, ...]: ...

    async def fetch_issue(self, issue_url: str) -> GitHubIssue: ...

    async def find_open_pull_request(
        self,
        *,
        issue: GitHubIssue,
        head: str,
        base: str,
    ) -> GitHubPullRequest | None: ...

    async def create_pull_request(
        self,
        *,
        issue: GitHubIssue,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> GitHubPullRequest: ...

    async def get_pull_request(
        self,
        pull_request: GitHubPullRequest,
    ) -> GitHubPullRequestState: ...

    async def merge_pull_request(
        self,
        pull_request: GitHubPullRequest,
        *,
        expected_head_sha: str,
    ) -> GitHubMergeResult: ...

    async def comment_issue(self, issue_url: str, body: str) -> GitHubComment: ...

    async def delete_comment(self, comment_url: str) -> None: ...

    async def create_issue(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        labels: tuple[str, ...],
    ) -> GitHubIssue: ...


class GitHubScope(Protocol):
    """Anything that names the project whose GitHub credentials a client should use."""

    project_id: str


GitHubFactory = Callable[[GitHubScope], GitHubClient]


class GitBranchPublisher(Protocol):
    """Publish the reviewed local workspace to one Git branch."""

    async def validate_target(
        self,
        *,
        owner: str,
        repo: str,
        base_sha: str,
        default_branch: str,
    ) -> None:
        """Implementations raise BaseMovedError when the default branch head differs from the pinned base and plain ValueError for every other failure."""
        ...

    async def publish(
        self,
        *,
        project_id: str,
        work_id: str,
        base_sha: str,
        expected_sha: str,
        branch: str,
        commit_message: str,
    ) -> str: ...


class SoftwareLifecyclePort(Protocol):
    """The PR 4 lifecycle surface reused by GitHub intake."""

    async def start(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        assumptions: tuple = (),
    ) -> WorkRecord: ...

    async def resume(
        self,
        work_id: str,
        *,
        project_id: str,
    ) -> WorkRecord: ...


class CatalogGitHubClient:
    """Adapt the existing project-scoped catalog callable to Work models."""

    def __init__(
        self,
        *,
        project_id: str,
        github_callable: Callable[
            [dict[str, Any]],
            Awaitable[Any],
        ],
    ) -> None:
        self._project_id = project_id
        self._call = github_callable

    async def list_labeled_issues(
        self,
        *,
        owner: str,
        repo: str,
        label: str,
    ) -> tuple[GitHubIssue, ...]:
        repository = await self._call({"_operation": "get_repo", "owner": owner, "repo": repo})
        results = await self._call(
            {
                "_operation": "list_issues",
                "owner": owner,
                "repo": repo,
                "labels": label,
                "state": "open",
                "sort": "created",
                "direction": "asc",
                "per_page": 100,
            }
        )
        return tuple(
            GitHubIssue(
                project_id=self._project_id,
                owner=owner,
                repo=repo,
                number=int(result["number"]),
                url=str(result["html_url"]),
                title=str(result["title"]),
                body=str(result.get("body") or ""),
                default_branch=str(repository["default_branch"]),
            )
            for result in results
            if "pull_request" not in result
        )

    async def fetch_issue(self, issue_url: str) -> GitHubIssue:
        owner, repo, number = _parse_issue_url(issue_url)
        repository = await self._call({"_operation": "get_repo", "owner": owner, "repo": repo})
        issue = await self._call(
            {
                "_operation": "get_issue",
                "owner": owner,
                "repo": repo,
                "number": number,
            }
        )
        return GitHubIssue(
            project_id=self._project_id,
            owner=owner,
            repo=repo,
            number=int(issue["number"]),
            url=str(issue["html_url"]),
            title=str(issue["title"]),
            body=str(issue.get("body") or ""),
            default_branch=str(repository["default_branch"]),
        )

    async def create_issue(
        self, *, owner: str, repo: str, title: str, body: str, labels: tuple[str, ...]
    ) -> GitHubIssue:
        repository = await self._call({"_operation": "get_repo", "owner": owner, "repo": repo})
        issue = await self._call(
            {
                "_operation": "create_issue",
                "owner": owner,
                "repo": repo,
                "title": title,
                "body": body,
                "labels": list(labels),
            }
        )
        return GitHubIssue(
            project_id=self._project_id,
            owner=owner,
            repo=repo,
            number=int(issue["number"]),
            url=str(issue["html_url"]),
            title=title,
            body=body,
            default_branch=str(repository["default_branch"]),
        )

    async def find_open_pull_request(
        self,
        *,
        issue: GitHubIssue,
        head: str,
        base: str,
    ) -> GitHubPullRequest | None:
        self._validate_project(issue.project_id)
        result = await self._call(
            {
                "_operation": "find_pull_requests",
                "owner": issue.owner,
                "repo": issue.repo,
                "head": f"{issue.owner}:{head}",
                "base": base,
                "state": "open",
            }
        )
        if not result:
            return None
        pull_request = result[0]
        remote_head = str(pull_request["head"]["ref"])
        remote_base = str(pull_request["base"]["ref"])
        if (remote_head, remote_base) != (head, base):
            raise ValueError(
                "GitHub pull request search result does not match requested head/base"
            )
        return GitHubPullRequest(
            project_id=self._project_id,
            owner=issue.owner,
            repo=issue.repo,
            number=int(pull_request["number"]),
            url=str(pull_request["html_url"]),
            head_sha=str(pull_request["head"]["sha"]),
            head=head,
            base=base,
        )

    async def create_pull_request(
        self,
        *,
        issue: GitHubIssue,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> GitHubPullRequest:
        self._validate_project(issue.project_id)
        result = await self._call(
            {
                "_operation": "create_pull_request",
                "owner": issue.owner,
                "repo": issue.repo,
                "title": title,
                "head": head,
                "base": base,
                "body": body,
            }
        )
        return GitHubPullRequest(
            project_id=self._project_id,
            owner=issue.owner,
            repo=issue.repo,
            number=int(result["number"]),
            url=str(result["html_url"]),
            head_sha=str(result["head"]["sha"]),
            head=head,
            base=base,
        )

    async def get_pull_request(
        self,
        pull_request: GitHubPullRequest,
    ) -> GitHubPullRequestState:
        self._validate_project(pull_request.project_id)
        result = await self._call(
            {
                "_operation": "get_pull_request",
                "owner": pull_request.owner,
                "repo": pull_request.repo,
                "number": pull_request.number,
            }
        )
        return GitHubPullRequestState(
            project_id=self._project_id,
            pull_request_number=pull_request.number,
            merged=bool(result["merged"]),
            merge_commit_sha=result.get("merge_commit_sha"),
        )

    async def merge_pull_request(
        self,
        pull_request: GitHubPullRequest,
        *,
        expected_head_sha: str,
    ) -> GitHubMergeResult:
        self._validate_project(pull_request.project_id)
        try:
            result = await self._call(
                {
                    "_operation": "merge_pull_request",
                    "owner": pull_request.owner,
                    "repo": pull_request.repo,
                    "number": pull_request.number,
                    "sha": expected_head_sha,
                }
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {405, 409}:
                raise
            raise GitHubMergeRejectedError(str(exc.response.json()["message"])) from exc
        return GitHubMergeResult(
            project_id=self._project_id,
            pull_request_number=pull_request.number,
            merged_sha=str(result["sha"]),
        )

    async def comment_issue(self, issue_url: str, body: str) -> GitHubComment:
        owner, repo, number = _parse_issue_url(issue_url)
        comment = await self._call(
            {
                "_operation": "create_comment",
                "owner": owner,
                "repo": repo,
                "number": number,
                "body": body,
            }
        )
        return GitHubComment(
            project_id=self._project_id,
            id=int(comment["id"]),
            url=str(comment["html_url"]),
            body=str(comment["body"]),
        )

    async def delete_comment(self, comment_url: str) -> None:
        owner, repo, comment_id = parse_comment_url(comment_url)
        await self._call(
            {
                "_operation": "delete_comment",
                "owner": owner,
                "repo": repo,
                "comment_id": comment_id,
            }
        )

    def _validate_project(self, project_id: str) -> None:
        if project_id != self._project_id:
            raise ValueError("GitHub input belongs to a different project")


class WorktreeBranchPublisher:
    """Publish through the PR 3 worktree manager and worker subprocess path."""

    def __init__(
        self,
        *,
        worktree_manager: SoftwareWorktreeManager,
        repository: Path,
    ) -> None:
        self._worktree_manager = worktree_manager
        self._repository = repository.resolve()

    async def validate_target(
        self,
        *,
        owner: str,
        repo: str,
        base_sha: str,
        default_branch: str,
    ) -> None:
        """Prove the local origin and GitHub base are the requested issue target."""
        origin = await run_worker_subprocess(
            argv=("git", "remote", "get-url", "origin"),
            cwd=self._repository,
        )
        if origin.returncode != 0:
            raise ValueError(f"cannot read Git origin: {origin.stderr.strip()}")
        actual_owner, actual_repo = github_remote_repository(origin.stdout.strip())
        if (actual_owner.casefold(), actual_repo.casefold()) != (
            owner.casefold(),
            repo.casefold(),
        ):
            raise ValueError(
                "local Git origin does not match issue repository: "
                f"expected {owner}/{repo}, found {actual_owner}/{actual_repo}"
            )

        remote = await run_worker_subprocess(
            argv=(
                "git",
                "ls-remote",
                "--exit-code",
                "origin",
                f"refs/heads/{default_branch}",
            ),
            cwd=self._repository,
        )
        if remote.returncode != 0:
            raise ValueError(
                f"cannot read GitHub default branch {default_branch}: {remote.stderr.strip()}"
            )
        fields = remote.stdout.split()
        if len(fields) < 2:
            raise ValueError(f"GitHub default branch {default_branch} returned no commit")
        remote_sha = fields[0]
        if remote_sha != base_sha:
            raise BaseMovedError(expected=base_sha, found=remote_sha)

    async def publish(
        self,
        *,
        project_id: str,
        work_id: str,
        base_sha: str,
        expected_sha: str,
        branch: str,
        commit_message: str,
    ) -> str:
        workspace = await self._worktree_manager.resume(
            repository=self._repository,
            project_id=project_id,
            work_id=work_id,
            attempt_id="workspace",
            base_sha=base_sha,
            expected_sha=expected_sha,
            publish_commit_message=commit_message,
        )
        return await self._worktree_manager.publish_branch(
            workspace,
            branch=branch,
            commit_message=commit_message,
        )


def require_merge_approval(_request: ActionRequest) -> GateDecision:
    """Default merge policy: an operator must explicitly approve."""

    return GateDecision.REQUIRE_APPROVAL


def _merge_post_check_detail(
    state: GitHubPullRequestState,
    merge_result: GitHubMergeResult | None,
    merge_event: WorkEvent | None,
) -> str | None:
    """The reason merged_sha_read_back failed, or None when the merge is confirmed."""
    if not state.merged:
        return "GitHub did not report the pull request as merged"
    if state.merge_commit_sha is None:
        return "GitHub did not report the merged commit SHA"
    if merge_result is not None and merge_result.merged_sha != state.merge_commit_sha:
        return "merge response SHA conflicts with GitHub read-back"
    if (
        merge_event is not None
        and str(merge_event.payload_json["merged_sha"]) != state.merge_commit_sha
    ):
        return "canonical merged SHA conflicts with GitHub state"
    return None


class GitHubIssueLifecycle:
    """Extend the PR 4 lifecycle through pull request and merge delivery."""

    def __init__(
        self,
        *,
        work_store: WorkStore,
        software_lifecycle: SoftwareLifecyclePort,
        github: GitHubClient,
        branch_publisher: GitBranchPublisher,
        repository_outcome: SoftwareRepositoryOutcome,
        execution_route: str | None = None,
        fleet_org_id: str | None = None,
        merge_policy: Callable[[ActionRequest], GateDecision] = require_merge_approval,
        task_id: str | None = None,
    ) -> None:
        self._work_store = work_store
        self._software_lifecycle = software_lifecycle
        self._github = github
        self._branch_publisher = branch_publisher
        if repository_outcome is SoftwareRepositoryOutcome.VERIFIED_COMMIT:
            raise ValueError("GitHub lifecycle requires a pull-request or merged outcome")
        self._repository_outcome = repository_outcome
        self._execution_route = execution_route
        self._fleet_org_id = fleet_org_id
        self._merge_policy = merge_policy
        self._task_id = task_id

    async def start(
        self,
        *,
        issue_url: str,
        project_id: str,
        base_sha: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> WorkRecord:
        """Start the Work for one issue; extra evidence joins the issue on the contract."""
        issue = await self._github.fetch_issue(issue_url)
        return await self._start_issue(
            issue=issue,
            project_id=project_id,
            base_sha=base_sha,
            evidence_refs=evidence_refs,
        )

    async def intake_labeled(
        self,
        *,
        owner: str,
        repo: str,
        label: str,
        project_id: str,
        base_sha: str,
    ) -> WorkRecord | None:
        """Start the oldest labeled issue that has no canonical Work yet."""
        issues = await self._github.list_labeled_issues(
            owner=owner,
            repo=repo,
            label=label,
        )
        for issue in issues:
            existing = await self._work_store.find_work_by_source_ref(
                issue.url,
                project_id=project_id,
            )
            if existing is not None:
                continue
            return await self._start_issue(
                issue=issue,
                project_id=project_id,
                base_sha=base_sha,
            )
        return None

    async def _start_issue(
        self,
        *,
        issue: GitHubIssue,
        project_id: str,
        base_sha: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> WorkRecord:
        """Create canonical Work for one fetched issue and run through the merge gate."""
        if issue.project_id != project_id:
            raise ValueError("GitHub issue belongs to a different project")
        await self._branch_publisher.validate_target(
            owner=issue.owner,
            repo=issue.repo,
            base_sha=base_sha,
            default_branch=issue.default_branch,
        )

        work_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        description = issue.body or issue.title
        work_item = WorkItem(
            id=work_id,
            project_id=project_id,
            profile="software",
            source="github",
            source_ref=issue.url,
            title=issue.title,
            description=description,
            target_systems=("repository", "github"),
            created_at=now,
        )
        contract_id = str(uuid.uuid4())
        repository_criterion_id = f"{contract_id}:repository"
        contract = WorkContract(
            id=contract_id,
            project_id=project_id,
            work_id=work_id,
            version=1,
            goal=issue.title,
            allowed_scope=(".",),
            acceptance_criteria=(
                AcceptanceCriterion(
                    id=repository_criterion_id,
                    project_id=project_id,
                    statement="produce the accepted repository outcome",
                    verification_kind="profile",
                ),
            ),
            constraints=(),
            non_goals=(),
            evidence_refs=(issue.url, *evidence_refs),
            assumption_ids=(),
            risk="low",
            design_required=False,
            profile_context=SoftwareContractContext(
                project_id=project_id,
                base_sha=base_sha,
                repository_outcome=self._repository_outcome,
                repository_criterion_id=repository_criterion_id,
                delivery=None,
                execution_route=self._execution_route,
                fleet_org_id=self._fleet_org_id,
                task_id=self._task_id,
            ).model_dump(mode="json"),
        )
        record = await self._software_lifecycle.start(
            work_item=work_item,
            contract=contract,
        )
        if record.status != "READY_TO_MERGE":
            await self.present_pending(work_id, project_id=project_id)
            return record
        events = await self._events(work_id, project_id)
        work_item, contract = self._canonical_inputs(events)
        return await self._advance(
            work_item=work_item,
            contract=contract,
            issue=issue,
            record=record,
        )

    async def resume(
        self,
        work_id: str,
        *,
        project_id: str,
    ) -> WorkRecord:
        """Resume lifecycle or post-review delivery from canonical state."""
        record = await self._work_store.load_work(work_id, project_id=project_id)
        if record is None:
            raise KeyError(work_id)
        if record.status in {
            "READY_TO_DELIVER",
            "RELEASING",
            "STAGING",
            "PRODUCTION_CANARY",
            "PRODUCTION_ROLLOUT",
            "SOAKING",
            "ROLLING_BACK",
            "COMPLETE",
            SUPERSEDED,
        }:
            return record
        if record.status == "WORK_BLOCKED":
            await self.present_pending(work_id, project_id=project_id)
            return record

        events = await self._events(work_id, project_id)
        if record.status == "READY_TO_MERGE" and record.pending_gate is not None:
            gate_id = record.pending_gate
            decided = self._gate_event(events, WorkEventType.GATE_DECIDED, gate_id)
            if decided is None:
                await self.present_pending(work_id, project_id=project_id)
                return record
            record = await self._set_record(
                record,
                status="MERGING",
                pending_gate=None,
            )

        if record.status not in {"READY_TO_MERGE", "MERGING", "BASE_MOVED"}:
            record = await self._software_lifecycle.resume(
                work_id,
                project_id=project_id,
            )
            if record.status != "READY_TO_MERGE":
                await self.present_pending(work_id, project_id=project_id)
                return record
            events = await self._events(work_id, project_id)
        work_item, contract = self._canonical_inputs(events)

        source_ref = work_item.source_ref
        if source_ref is None or not is_github_issue_url(source_ref):
            raise ValueError("WorkItem is not sourced from a GitHub issue")
        issue = await self._github.fetch_issue(source_ref)
        return await self._advance(
            work_item=work_item,
            contract=contract,
            issue=issue,
            record=record,
        )

    async def approve(
        self,
        work_id: str,
        *,
        project_id: str,
        gate_id: str,
        actor_ref: str,
    ) -> WorkRecord:
        """Record explicit approval for one pending merge gate, then advance."""
        record = await self._work_store.load_work(work_id, project_id=project_id)
        if record is None:
            raise KeyError(work_id)
        if record.pending_gate != gate_id:
            raise ValueError(f"gate is not pending: {gate_id}")

        events = await self._events(work_id, project_id)
        requested = self._gate_event(
            events,
            WorkEventType.GATE_REQUESTED,
            gate_id,
        )
        if requested is None:
            raise ValueError(f"gate request is missing: {gate_id}")
        decided = self._gate_event(events, WorkEventType.GATE_DECIDED, gate_id)
        if decided is not None:
            return await self.resume(work_id, project_id=project_id)

        await self._append(
            work_id=work_id,
            project_id=project_id,
            event_type=WorkEventType.GATE_DECIDED,
            payload={
                "gate_id": gate_id,
                "decision": GateDecision.ALLOW.value,
                "action": requested.payload_json["action"],
            },
            actor_ref=actor_ref,
        )
        await self._set_record(
            record,
            status="MERGING",
            pending_gate=None,
        )
        return await self.resume(work_id, project_id=project_id)

    async def present_pending(
        self,
        work_id: str,
        *,
        project_id: str,
    ) -> None:
        """Reflect canonical pending attention on the source GitHub issue."""
        events = await self._events(work_id, project_id)
        presented = {
            str(event.payload_json["attention_key"])
            for event in events
            if event.event_type is WorkEventType.EXECUTION_RECORDED
            and event.payload_json.get("action") == "github_pending_attention_presented"
        }
        pending = await self._work_store.pending_attention(project_id=project_id)
        for item in pending:
            if item.work_id != work_id or item.source_ref is None:
                continue
            if not is_github_issue_url(item.source_ref):
                continue
            attention_key = _attention_key(item)
            if attention_key in presented:
                continue
            await self._github.comment_issue(
                item.source_ref,
                _attention_comment(item),
            )
            receipt: dict[str, object] = {
                "action": "github_pending_attention_presented",
                "attention_id": item.attention_id,
                "attention_key": attention_key,
                "kind": item.kind.value,
                "source_ref": item.source_ref,
            }
            if item.severity is not None:
                receipt["severity"] = item.severity
            await self._append(
                work_id=work_id,
                project_id=project_id,
                event_type=WorkEventType.EXECUTION_RECORDED,
                payload=receipt,
                actor_ref="github",
            )
            presented.add(attention_key)

    async def _advance(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        issue: GitHubIssue,
        record: WorkRecord,
    ) -> WorkRecord:
        software = SoftwareContractContext.model_validate(contract.profile_context)
        software.validate_contract(contract)
        pending = await self._work_store.pending_attention(
            project_id=work_item.project_id,
        )
        critical_incidents = tuple(
            item
            for item in pending
            if item.work_id == work_item.id
            and item.kind is PendingAttentionKind.EXTERNAL_OUTCOME_INCIDENT
            and item.severity == "critical"
        )
        if critical_incidents:
            await self.present_pending(
                work_item.id,
                project_id=issue.project_id,
            )
            return record
        controls = tuple(
            item
            for item in pending
            if item.work_id == work_item.id and item.kind is PendingAttentionKind.CONTROL_DEGRADED
        )
        target_control_degraded = any(
            item.attention_id == "github-target" for item in controls
        )
        other_controls = tuple(
            item for item in controls if item.attention_id != "github-target"
        )
        if other_controls:
            await self.present_pending(
                work_item.id,
                project_id=issue.project_id,
            )
            return record

        events = await self._events(work_item.id, work_item.project_id)
        cycle_start = self._delivery_cycle_start(events)
        target_base_sha = self._target_base_sha(
            events,
            initial_base_sha=software.base_sha,
            cycle_start=cycle_start,
        )
        pull_request = self._pull_request(events, after_sequence=cycle_start)
        if pull_request is None:
            publication = self._branch_publication(
                events,
                after_sequence=cycle_start,
            )
            if publication is None:
                try:
                    await self._branch_publisher.validate_target(
                        owner=issue.owner,
                        repo=issue.repo,
                        base_sha=target_base_sha,
                        default_branch=issue.default_branch,
                    )
                except BaseMovedError as exc:
                    return await self._hold_base_moved(
                        work_item,
                        record,
                        phase="publish",
                        expected=exc.expected,
                        found=exc.found,
                    )
                except ValueError as exc:
                    if not target_control_degraded:
                        event = await self._append(
                            work_id=work_item.id,
                            project_id=work_item.project_id,
                            event_type=WorkEventType.CONTROL_DEGRADED,
                            payload={
                                "failed_preconditions": ["github-target"],
                                "evidence_refs": [issue.url],
                                "details": str(exc),
                                "frozen_action_ids": [
                                    "publish_branch",
                                    "create_pull_request",
                                    "merge",
                                ],
                            },
                            actor_ref="control",
                        )
                        events.append(event)
                    await self.present_pending(
                        work_item.id,
                        project_id=issue.project_id,
                    )
                    return record
                if target_control_degraded:
                    event = await self._append(
                        work_id=work_item.id,
                        project_id=work_item.project_id,
                        event_type=WorkEventType.CONTROL_RESTORED,
                        payload={
                            "precondition_ids": ["github-target"],
                            "evidence_refs": [issue.url],
                        },
                        actor_ref="control",
                    )
                    events.append(event)
                branch = f"sagewai/{work_item.id}"
                expected_sha = expected_result_sha(events, software.base_sha)
                branch_sha = await self._branch_publisher.publish(
                    project_id=issue.project_id,
                    work_id=work_item.id,
                    base_sha=software.base_sha,
                    expected_sha=expected_sha,
                    branch=branch,
                    commit_message=f"sagewai work {work_item.id}",
                )
                event = await self._append(
                    work_id=work_item.id,
                    project_id=work_item.project_id,
                    event_type=WorkEventType.STAGE_COMPLETED,
                    payload={
                        "stage": "branch_published",
                        "branch": branch,
                        "branch_sha": branch_sha,
                    },
                    actor_ref="github",
                )
                events.append(event)
            else:
                branch, branch_sha = publication

            pull_request = await self._github.find_open_pull_request(
                issue=issue,
                head=branch,
                base=issue.default_branch,
            )
            if pull_request is None:
                pull_request = await self._github.create_pull_request(
                    issue=issue,
                    title=issue.title,
                    head=branch,
                    base=issue.default_branch,
                    body=f"Closes #{issue.number}",
                )
            self._validate_pull_request(
                pull_request,
                work_item=work_item,
                issue=issue,
                expected_head=branch,
                expected_head_sha=branch_sha,
            )
            event = await self._append(
                work_id=work_item.id,
                project_id=work_item.project_id,
                event_type=WorkEventType.STAGE_COMPLETED,
                payload={
                    "stage": "pull_request",
                    "branch_sha": branch_sha,
                    "pull_request": pull_request.model_dump(mode="json"),
                },
                actor_ref="github",
            )
            events.append(event)

        pull_request_event = self._pull_request_event(
            events,
            after_sequence=cycle_start,
        )
        self._validate_pull_request(
            pull_request,
            work_item=work_item,
            issue=issue,
            expected_head=f"sagewai/{work_item.id}",
            expected_head_sha=str(pull_request_event.payload_json["branch_sha"]),
        )
        context = GitHubWorkContext(
            project_id=issue.project_id,
            owner=issue.owner,
            repo=issue.repo,
            issue_number=issue.number,
            issue_url=issue.url,
            default_branch=issue.default_branch,
            branch=pull_request.head,
            branch_sha=str(pull_request_event.payload_json["branch_sha"]),
            pull_request_number=pull_request.number,
            pull_request_url=pull_request.url,
        )
        context_payload = context.model_dump(mode="json")
        if record.profile_context.get("github") != context_payload:
            profile_context = dict(record.profile_context)
            profile_context["github"] = context_payload
            record = await self._set_record(
                record,
                status=record.status,
                pending_gate=record.pending_gate,
                profile_context=profile_context,
            )

        github_context = GitHubWorkContext.model_validate(
            record.profile_context["github"]
        )

        if software.repository_outcome is SoftwareRepositoryOutcome.PULL_REQUEST:
            return await self._finish_repository_outcome(
                work_item=work_item,
                contract=contract,
                software=software,
                record=record,
                pull_request=pull_request,
                result_sha=github_context.branch_sha,
            )

        gate_id = f"merge:{work_item.id}:{pull_request.number}"
        decided = self._gate_event(
            events,
            WorkEventType.GATE_DECIDED,
            gate_id,
        )
        requested = self._gate_event(
            events,
            WorkEventType.GATE_REQUESTED,
            gate_id,
        )
        action = ActionRequest(
            project_id=work_item.project_id,
            action="merge",
            work_id=work_item.id,
            risk="medium",
            reversibility=Reversibility.COMPENSATABLE,
            scope=pull_request.url,
            evidence_refs=self._merge_evidence(
                contract,
                events,
                pull_request.url,
            ),
            rollback="revert_pull_request",
            post_check="merged_sha_read_back",
        )

        if decided is None and requested is None:
            decision = GateDecision(self._merge_policy(action))
            if decision is GateDecision.REQUIRE_APPROVAL:
                await self._append(
                    work_id=work_item.id,
                    project_id=work_item.project_id,
                    event_type=WorkEventType.GATE_REQUESTED,
                    payload={
                        "gate_id": gate_id,
                        "question": (f"Approve merge of PR #{pull_request.number}."),
                        "action": action.model_dump(mode="json"),
                        "evidence_refs": list(action.evidence_refs),
                    },
                    actor_ref="policy",
                )
                record = await self._set_record(
                    record,
                    status="READY_TO_MERGE",
                    pending_gate=gate_id,
                )
                await self.present_pending(
                    work_item.id,
                    project_id=issue.project_id,
                )
                return record
            decided = await self._append(
                work_id=work_item.id,
                project_id=work_item.project_id,
                event_type=WorkEventType.GATE_DECIDED,
                payload={
                    "gate_id": gate_id,
                    "decision": decision.value,
                    "action": action.model_dump(mode="json"),
                },
                actor_ref="policy",
            )
            events.append(decided)

        if decided is None:
            record = await self._set_record(
                record,
                status="READY_TO_MERGE",
                pending_gate=gate_id,
            )
            await self.present_pending(work_item.id, project_id=issue.project_id)
            return record
        if decided.payload_json["decision"] == GateDecision.DENY.value:
            await self._append(
                work_id=work_item.id,
                project_id=work_item.project_id,
                event_type=WorkEventType.WORK_BLOCKED,
                payload={
                    "reason": "merge_policy_denied",
                    "decision_request": "Revise merge policy or stop the work.",
                    "evidence_refs": list(action.evidence_refs),
                },
                actor_ref="policy",
            )
            record = await self._set_record(
                record,
                status="WORK_BLOCKED",
                pending_gate=None,
            )
            await self.present_pending(
                work_item.id,
                project_id=issue.project_id,
            )
            return record

        if record.status != "MERGING":
            record = await self._set_record(
                record,
                status="MERGING",
                pending_gate=None,
            )

        merge_event = self._merge_event(
            events,
            pull_request.number,
            after_sequence=cycle_start,
        )
        state = await self._read_pull_request_state(work_item, pull_request)
        if merge_event is not None and not state.merged:
            state = await self._read_pull_request_state(work_item, pull_request)
            if not state.merged:
                raise RuntimeError("canonical merge event conflicts with GitHub state")

        merge_result: GitHubMergeResult | None = None
        if not state.merged:
            try:
                await self._branch_publisher.validate_target(
                    owner=issue.owner,
                    repo=issue.repo,
                    base_sha=target_base_sha,
                    default_branch=issue.default_branch,
                )
            except BaseMovedError as exc:
                return await self._hold_base_moved(
                    work_item,
                    record,
                    phase="merge",
                    expected=exc.expected,
                    found=exc.found,
                )
            try:
                merge_result = await self._github.merge_pull_request(
                    pull_request,
                    expected_head_sha=github_context.branch_sha,
                )
            except GitHubMergeRejectedError as exc:
                await self._append(
                    work_id=work_item.id,
                    project_id=work_item.project_id,
                    event_type=WorkEventType.WORK_BLOCKED,
                    payload={
                        "reason": "merge_rejected",
                        "decision_request": (
                            f"GitHub rejected the merge: {exc}. Resolve the rejection or stop "
                            "the work."
                        ),
                        "evidence_refs": list(action.evidence_refs),
                    },
                    actor_ref="github",
                )
                record = await self._set_record(
                    record,
                    status="WORK_BLOCKED",
                    pending_gate=None,
                )
                await self.present_pending(work_item.id, project_id=issue.project_id)
                return record
            if (
                merge_result.project_id != work_item.project_id
                or merge_result.pull_request_number != pull_request.number
            ):
                raise ValueError("merge result belongs to a different WorkItem")
            state = await self._read_pull_request_state(work_item, pull_request)

        if not state.merged or state.merge_commit_sha is None:
            # GitHub can report merged=False briefly after a successful merge; read once
            # more before asking for operator attention.
            state = await self._read_pull_request_state(work_item, pull_request)
        detail = _merge_post_check_detail(state, merge_result, merge_event)
        action_id = f"merge:{work_item.id}:{pull_request.number}"
        observed_merged_sha = state.merge_commit_sha if state.merged else None
        observation_payload = {
            "check": "merged_sha_read_back",
            "action_id": action_id,
            "passed": detail is None,
            "detail": detail,
            "merged_sha": observed_merged_sha,
            "evidence_refs": [pull_request.url],
        }
        previous_observation = next(
            (
                event
                for event in reversed(events)
                if event.event_type is WorkEventType.OBSERVATION_RECORDED
                and event.sequence > cycle_start
                and event.payload_json.get("check") == "merged_sha_read_back"
                and event.payload_json.get("action_id") == action_id
            ),
            None,
        )
        if (
            previous_observation is None
            or previous_observation.payload_json != observation_payload
        ):
            await self._append(
                work_id=work_item.id,
                project_id=work_item.project_id,
                event_type=WorkEventType.OBSERVATION_RECORDED,
                payload=observation_payload,
                actor_ref="github",
            )
        if detail is not None:
            await self._append(
                work_id=work_item.id,
                project_id=work_item.project_id,
                event_type=WorkEventType.WORK_BLOCKED,
                payload={
                    "reason": "merge_post_check_failed",
                    "decision_request": (
                        (
                            f"{detail}. Allow the recorded rollback ({action.rollback}) "
                            "or resolve the pull request on GitHub."
                        )
                        if observed_merged_sha is not None
                        else f"{detail}. Resolve the pull request on GitHub."
                    ),
                    "merged_sha": observed_merged_sha,
                    "issue_url": issue.url,
                    "evidence_refs": list(action.evidence_refs),
                },
                actor_ref="github",
            )
            record = await self._set_record(
                record,
                status="WORK_BLOCKED",
                pending_gate=None,
            )
            await self.present_pending(work_item.id, project_id=issue.project_id)
            return record
        if merge_event is None:
            await self._append(
                work_id=work_item.id,
                project_id=work_item.project_id,
                event_type=WorkEventType.STAGE_COMPLETED,
                payload={
                    "stage": "merge",
                    "pull_request_number": pull_request.number,
                    "merged_sha": state.merge_commit_sha,
                },
                actor_ref="github",
            )

        github_context = github_context.model_copy(
            update={"merged_sha": state.merge_commit_sha}
        )
        profile_context = dict(record.profile_context)
        profile_context["github"] = github_context.model_dump(mode="json")
        record = await self._set_record(
            record,
            status="MERGING",
            pending_gate=None,
            profile_context=profile_context,
        )
        return await self._finish_repository_outcome(
            work_item=work_item,
            contract=contract,
            software=software,
            record=record,
            pull_request=pull_request,
            result_sha=state.merge_commit_sha,
        )

    async def _read_pull_request_state(
        self,
        work_item: WorkItem,
        pull_request: GitHubPullRequest,
    ) -> GitHubPullRequestState:
        state = await self._github.get_pull_request(pull_request)
        if (
            state.project_id != work_item.project_id
            or state.pull_request_number != pull_request.number
        ):
            raise ValueError("pull request state belongs to a different WorkItem")
        return state

    async def _finish_repository_outcome(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        software: SoftwareContractContext,
        record: WorkRecord,
        pull_request: GitHubPullRequest,
        result_sha: str,
    ) -> WorkRecord:
        evidence_refs = (pull_request.url, f"git://{result_sha}")
        expected_result = VerificationResult(
            project_id=work_item.project_id,
            contract_id=contract.id,
            attempt_id=(
                f"{work_item.id}:repository:{software.repository_outcome.value}:"
                f"{pull_request.number}"
            ),
            stage="repository",
            passed=True,
            criterion_results=(
                CriterionVerification(
                    project_id=work_item.project_id,
                    contract_id=contract.id,
                    criterion_id=software.repository_criterion_id,
                    passed=True,
                    evidence_refs=evidence_refs,
                ),
            ),
            evidence_refs=evidence_refs,
            profile_context={
                "repository_outcome": software.repository_outcome.value,
                "pull_request_number": pull_request.number,
                "pull_request_url": pull_request.url,
                "result_sha": result_sha,
            },
        )
        validate_verification_result(
            contract,
            (software.repository_criterion_id,),
            expected_result,
        )

        events = await self._events(work_item.id, work_item.project_id)
        result_event = next(
            (
                event
                for event in reversed(events)
                if event.event_type is WorkEventType.VERIFICATION_RECORDED
                and event.payload_json.get("contract_id") == contract.id
                and event.payload_json.get("attempt_id") == expected_result.attempt_id
            ),
            None,
        )
        if result_event is None:
            await self._append(
                work_id=work_item.id,
                project_id=work_item.project_id,
                event_type=WorkEventType.VERIFICATION_RECORDED,
                payload=expected_result.model_dump(mode="json"),
                actor_ref="github",
            )
        else:
            recorded_result = VerificationResult.model_validate(
                result_event.payload_json
            )
            validate_verification_result(
                contract,
                (software.repository_criterion_id,),
                recorded_result,
            )
            if recorded_result != expected_result:
                raise RuntimeError("repository outcome conflicts with canonical evidence")

        if software.delivery is not None:
            return await self._set_record(
                record,
                status="READY_TO_DELIVER",
                pending_gate=None,
            )

        events = await self._events(work_item.id, work_item.project_id)
        verification_results = tuple(
            VerificationResult.model_validate(event.payload_json)
            for event in events
            if event.event_type is WorkEventType.VERIFICATION_RECORDED
            and event.payload_json.get("contract_id") == contract.id
        )
        try:
            evaluation = evaluate_completion(
                work=work_item,
                contract=contract,
                verification_results=verification_results,
                evaluated_at=datetime.now(timezone.utc),
            )
        except ValueError as exc:
            return await self._block_completion(
                work_item=work_item,
                contract=contract,
                record=record,
                reason="completion_evidence_invalid",
                payload={
                    "violations": [str(exc)],
                    "decision_request": (
                        "Repair the current-contract verification evidence."
                    ),
                },
            )
        if not evaluation.passed:
            return await self._block_completion(
                work_item=work_item,
                contract=contract,
                record=record,
                reason="completion_criteria_failed",
                payload={
                    "failed_criterion_ids": [
                        result.criterion_id
                        for result in evaluation.criterion_results
                        if not result.passed
                    ],
                    "decision_request": "Repair the failed acceptance criteria.",
                },
            )

        completed_event = next(
            (
                event
                for event in reversed(events)
                if event.event_type is WorkEventType.WORK_COMPLETED
                and event.payload_json.get("contract_id") == contract.id
            ),
            None,
        )
        if completed_event is None:
            await self._append(
                work_id=work_item.id,
                project_id=work_item.project_id,
                event_type=WorkEventType.WORK_COMPLETED,
                payload=evaluation.model_dump(mode="json"),
                actor_ref="github",
            )
        else:
            recorded_evaluation = CompletionEvaluation.model_validate(
                completed_event.payload_json
            )
            if recorded_evaluation.model_dump(exclude={"evaluated_at"}) != (
                evaluation.model_dump(exclude={"evaluated_at"})
            ):
                raise RuntimeError("completion conflicts with canonical evidence")
        return await self._set_record(
            record,
            status="COMPLETE",
            pending_gate=None,
        )

    async def _hold_base_moved(
        self,
        work_item: WorkItem,
        record: WorkRecord,
        *,
        phase: str,
        expected: str,
        found: str,
    ) -> WorkRecord:
        payload = {
            "phase": phase,
            "expected_base": expected,
            "found_base": found,
        }
        events = await self._events(work_item.id, work_item.project_id)
        latest = next(
            (
                event
                for event in reversed(events)
                if event.event_type is WorkEventType.BASE_MOVED
            ),
            None,
        )
        if latest is None or latest.payload_json != payload:
            await self._append(
                work_id=work_item.id,
                project_id=work_item.project_id,
                event_type=WorkEventType.BASE_MOVED,
                payload=payload,
                actor_ref="github",
            )
        record = await self._set_record(
            record,
            status="BASE_MOVED",
            pending_gate=None,
        )
        await self.present_pending(work_item.id, project_id=work_item.project_id)
        return record

    async def _block_completion(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        record: WorkRecord,
        reason: str,
        payload: dict[str, Any],
    ) -> WorkRecord:
        events = await self._events(work_item.id, work_item.project_id)
        if not any(
            event.event_type is WorkEventType.WORK_BLOCKED
            and event.payload_json.get("contract_id") == contract.id
            and event.payload_json.get("reason") == reason
            for event in events
        ):
            await self._append(
                work_id=work_item.id,
                project_id=work_item.project_id,
                event_type=WorkEventType.WORK_BLOCKED,
                payload={
                    "contract_id": contract.id,
                    "reason": reason,
                    **payload,
                },
                actor_ref="github",
            )
        updated = await self._set_record(
            record,
            status="WORK_BLOCKED",
            pending_gate=None,
        )
        await self.present_pending(
            work_item.id,
            project_id=work_item.project_id,
        )
        return updated

    @staticmethod
    def _validate_pull_request(
        pull_request: GitHubPullRequest,
        *,
        work_item: WorkItem,
        issue: GitHubIssue,
        expected_head_sha: str,
        expected_head: str,
    ) -> None:
        if pull_request.project_id != work_item.project_id:
            raise ValueError("pull request belongs to a different project")
        if pull_request.owner != issue.owner or pull_request.repo != issue.repo:
            raise ValueError("pull request belongs to a different repository")
        if pull_request.head_sha != expected_head_sha:
            raise ValueError("pull request head SHA does not match published commit")
        if pull_request.head != expected_head or pull_request.base != issue.default_branch:
            raise ValueError("pull request targets unexpected branches")

    async def _events(
        self,
        work_id: str,
        project_id: str | None,
    ) -> list[WorkEvent]:
        return await self._work_store.read_events(
            work_id,
            project_id=project_id,
        )

    async def _append(
        self,
        *,
        work_id: str,
        project_id: str | None,
        event_type: WorkEventType,
        payload: dict[str, Any],
        actor_ref: str | None,
    ) -> WorkEvent:
        events = await self._events(work_id, project_id)
        event = WorkEvent(
            id=str(uuid.uuid4()),
            project_id=project_id,
            work_id=work_id,
            sequence=events[-1].sequence + 1 if events else 1,
            event_type=event_type,
            actor_type="github_lifecycle",
            actor_ref=actor_ref,
            payload_json=payload,
            created_at=datetime.now(timezone.utc),
        )
        await self._work_store.append_event(event)
        return event

    async def _set_record(
        self,
        record: WorkRecord,
        *,
        status: str,
        pending_gate: str | None,
        profile_context: dict[str, Any] | None = None,
    ) -> WorkRecord:
        updated = record.model_copy(
            update={
                "status": status,
                "pending_gate": pending_gate,
                "profile_context": (
                    record.profile_context if profile_context is None else profile_context
                ),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await self._work_store.save_work(updated)
        return updated

    @staticmethod
    def _canonical_inputs(
        events: list[WorkEvent],
    ) -> tuple[WorkItem, WorkContract]:
        created = next(event for event in events if event.event_type is WorkEventType.WORK_CREATED)
        accepted = next(
            event
            for event in reversed(events)
            if event.event_type is WorkEventType.CONTRACT_ACCEPTED
        )
        return (
            WorkItem.model_validate(created.payload_json),
            WorkContract.model_validate(accepted.payload_json),
        )

    @staticmethod
    def _branch_publication(
        events: list[WorkEvent],
        *,
        after_sequence: int = 0,
    ) -> tuple[str, str] | None:
        event = next(
            (
                item
                for item in reversed(events)
                if item.sequence > after_sequence
                if item.event_type is WorkEventType.STAGE_COMPLETED
                and item.payload_json.get("stage") == "branch_published"
            ),
            None,
        )
        if event is None:
            return None
        return str(event.payload_json["branch"]), str(event.payload_json["branch_sha"])

    @staticmethod
    def _pull_request_event(
        events: list[WorkEvent],
        *,
        after_sequence: int = 0,
    ) -> WorkEvent:
        return next(
            event
            for event in reversed(events)
            if event.sequence > after_sequence
            if event.event_type is WorkEventType.STAGE_COMPLETED
            and event.payload_json.get("stage") == "pull_request"
        )

    @classmethod
    def _pull_request(
        cls,
        events: list[WorkEvent],
        *,
        after_sequence: int = 0,
    ) -> GitHubPullRequest | None:
        try:
            event = cls._pull_request_event(events, after_sequence=after_sequence)
        except StopIteration:
            return None
        return GitHubPullRequest.model_validate(event.payload_json["pull_request"])

    @staticmethod
    def _merge_event(
        events: list[WorkEvent],
        pull_request_number: int,
        *,
        after_sequence: int = 0,
    ) -> WorkEvent | None:
        return next(
            (
                event
                for event in reversed(events)
                if event.sequence > after_sequence
                if event.event_type is WorkEventType.STAGE_COMPLETED
                and event.payload_json.get("stage") == "merge"
                and event.payload_json.get("pull_request_number") == pull_request_number
            ),
            None,
        )

    @staticmethod
    def _delivery_cycle_start(events: list[WorkEvent]) -> int:
        return next(
            (
                event.sequence
                for event in reversed(events)
                if event.event_type is WorkEventType.TRIAGE_CREATED
            ),
            0,
        )

    @staticmethod
    def _target_base_sha(
        events: list[WorkEvent],
        *,
        initial_base_sha: str,
        cycle_start: int,
    ) -> str:
        if cycle_start == 0:
            return initial_base_sha
        merge_event = next(
            (
                event
                for event in reversed(events)
                if event.sequence < cycle_start
                and event.event_type is WorkEventType.STAGE_COMPLETED
                and event.payload_json.get("stage") == "merge"
            ),
            None,
        )
        if merge_event is None:
            raise ValueError("delivery repair requires a prior merged SHA")
        return str(merge_event.payload_json["merged_sha"])

    @staticmethod
    def _gate_event(
        events: list[WorkEvent],
        event_type: WorkEventType,
        gate_id: str,
    ) -> WorkEvent | None:
        return next(
            (
                event
                for event in reversed(events)
                if event.event_type is event_type and event.payload_json.get("gate_id") == gate_id
            ),
            None,
        )

    @staticmethod
    def _merge_evidence(
        contract: WorkContract,
        events: list[WorkEvent],
        pull_request_url: str,
    ) -> tuple[str, ...]:
        refs = [*contract.evidence_refs, pull_request_url]
        for event_type in (
            WorkEventType.VERIFICATION_RECORDED,
            WorkEventType.REVIEW_RECORDED,
        ):
            event = next(
                (item for item in reversed(events) if item.event_type is event_type),
                None,
            )
            if event is not None:
                refs.extend(str(ref) for ref in event.payload_json.get("evidence_refs", ()))
        return tuple(dict.fromkeys(refs))


def is_github_issue_url(value: str) -> bool:
    """Return whether VALUE is one canonical github.com issue URL."""

    try:
        _parse_issue_url(value)
    except ValueError:
        return False
    return True


def github_remote_repository(value: str) -> tuple[str, str]:
    from urllib.parse import urlsplit

    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "ssh"} or parsed.hostname != "github.com":
            raise ValueError(f"Git origin is not a GitHub repository: {value}")
        path = parsed.path.lstrip("/")
    parts = path.removesuffix(".git").rstrip("/").split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Git origin is not a GitHub repository: {value}")
    return parts[0], parts[1]


def parse_pull_request_url(value: str) -> tuple[str, str, int]:
    """Split one canonical github.com pull request URL into owner, repo, and number."""
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    match = _PULL_PATH.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or match is None:
        raise ValueError(f"not a GitHub pull request URL: {value}")
    return match.group("owner"), match.group("repo"), int(match.group("number"))


def parse_comment_url(value: str) -> tuple[str, str, int]:
    """Split one issue-comment permalink into owner, repo, and comment id."""
    from urllib.parse import urlsplit

    owner, repo, _number = _parse_issue_url(value)
    match = _COMMENT_FRAGMENT.fullmatch(urlsplit(value).fragment)
    if match is None:
        raise ValueError(f"not a GitHub issue comment URL: {value}")
    return owner, repo, int(match.group("comment_id"))


def is_github_comment_url(value: str) -> bool:
    """Return whether VALUE is one canonical github.com issue-comment permalink."""
    try:
        parse_comment_url(value)
    except ValueError:
        return False
    return True


def _parse_issue_url(value: str) -> tuple[str, str, int]:
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        raise ValueError(f"not a GitHub issue URL: {value}")
    match = _ISSUE_PATH.fullmatch(parsed.path)
    if match is None:
        raise ValueError(f"not a GitHub issue URL: {value}")
    return (
        match.group("owner"),
        match.group("repo"),
        int(match.group("number")),
    )


def _attention_key(item: PendingAttention) -> str:
    key = f"{item.kind.value}:{item.attention_id}:{item.created_at.isoformat()}"
    if item.kind is PendingAttentionKind.EXTERNAL_OUTCOME_INCIDENT:
        return f"{key}:{item.severity}"
    return key


def _attention_comment(item: PendingAttention) -> str:
    summary = item.summary.rstrip(".")
    if item.kind is PendingAttentionKind.GATE_REQUESTED:
        summary = summary[:1].lower() + summary[1:]
        return f"Sagewai: approval required — {summary} (gate {item.attention_id})."
    if item.kind is PendingAttentionKind.WORK_BLOCKED:
        return f"Sagewai: work blocked — {summary}."
    if item.kind is PendingAttentionKind.EXTERNAL_OUTCOME_INCIDENT:
        evidence = ", ".join(item.evidence_refs)
        suffix = f" Evidence: {evidence}." if evidence else ""
        return f"Sagewai: production incident — {summary}.{suffix}"
    control_summary = item.attention_id
    if summary != control_summary:
        control_summary = f"{control_summary}: {summary}"
    evidence = ", ".join(item.evidence_refs)
    suffix = f" Evidence: {evidence}." if evidence else ""
    return f"Sagewai: control degraded — {control_summary}.{suffix}"


__all__ = [
    "BaseMovedError",
    "CatalogGitHubClient",
    "GitBranchPublisher",
    "GitHubClient",
    "GitHubComment",
    "GitHubFactory",
    "GitHubIssue",
    "GitHubIssueLifecycle",
    "GitHubMergeRejectedError",
    "GitHubMergeResult",
    "GitHubPullRequest",
    "GitHubPullRequestState",
    "GitHubScope",
    "GitHubWorkContext",
    "WorktreeBranchPublisher",
    "github_remote_repository",
    "is_github_comment_url",
    "is_github_issue_url",
    "parse_comment_url",
    "parse_pull_request_url",
    "require_merge_approval",
]
