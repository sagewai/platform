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

from pydantic import BaseModel, ConfigDict

from sagewai.work.contract import WorkContract
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import (
    ActionRequest,
    GateDecision,
    PendingAttention,
    PendingAttentionKind,
    Reversibility,
    WorkItem,
    WorkRecord,
)
from sagewai.work.profiles.software.models import SoftwareContractContext
from sagewai.work.profiles.software.scm import SoftwareWorktreeManager
from sagewai.work.store import WorkStore

_ISSUE_PATH = re.compile(
    r"^/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/"
    r"issues/(?P<number>[1-9][0-9]*)/?$"
)


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
    base: str


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

    async def fetch_issue(self, issue_url: str) -> GitHubIssue: ...

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
    ) -> GitHubMergeResult: ...

    async def comment_issue(self, issue_url: str, body: str) -> None: ...


class GitBranchPublisher(Protocol):
    """Publish the reviewed local workspace to one Git branch."""

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
            Awaitable[dict[str, Any]],
        ],
    ) -> None:
        self._project_id = project_id
        self._call = github_callable

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
    ) -> GitHubMergeResult:
        self._validate_project(pull_request.project_id)
        result = await self._call(
            {
                "_operation": "merge_pull_request",
                "owner": pull_request.owner,
                "repo": pull_request.repo,
                "number": pull_request.number,
            }
        )
        if result["merged"] is not True:
            raise RuntimeError(str(result["message"]))
        return GitHubMergeResult(
            project_id=self._project_id,
            pull_request_number=pull_request.number,
            merged_sha=str(result["sha"]),
        )

    async def comment_issue(self, issue_url: str, body: str) -> None:
        owner, repo, number = _parse_issue_url(issue_url)
        await self._call(
            {
                "_operation": "create_comment",
                "owner": owner,
                "repo": repo,
                "number": number,
                "body": body,
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
        )
        return await self._worktree_manager.publish_branch(
            workspace,
            branch=branch,
            commit_message=commit_message,
        )


def require_merge_approval(_request: ActionRequest) -> GateDecision:
    """Default merge policy: an operator must explicitly approve."""

    return GateDecision.REQUIRE_APPROVAL


class GitHubIssueLifecycle:
    """Extend the PR 4 lifecycle through pull request and merge delivery."""

    def __init__(
        self,
        *,
        work_store: WorkStore,
        software_lifecycle: SoftwareLifecyclePort,
        github: GitHubClient,
        branch_publisher: GitBranchPublisher,
        merge_policy: Callable[[ActionRequest], GateDecision] = require_merge_approval,
    ) -> None:
        self._work_store = work_store
        self._software_lifecycle = software_lifecycle
        self._github = github
        self._branch_publisher = branch_publisher
        self._merge_policy = merge_policy

    async def start(
        self,
        *,
        issue_url: str,
        project_id: str,
        base_sha: str,
    ) -> WorkRecord:
        """Fetch one issue, create canonical Work, and run through the merge gate."""
        issue = await self._github.fetch_issue(issue_url)
        if issue.project_id != project_id:
            raise ValueError("GitHub issue belongs to a different project")

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
        contract = WorkContract(
            id=str(uuid.uuid4()),
            project_id=project_id,
            work_id=work_id,
            version=1,
            goal=issue.title,
            allowed_scope=(".",),
            acceptance_criteria=(description,),
            constraints=(),
            non_goals=(),
            evidence_refs=(issue.url,),
            assumption_ids=(),
            risk="low",
            design_required=False,
            profile_context=SoftwareContractContext(
                base_sha=base_sha,
            ).model_dump(mode="json"),
        )
        record = await self._software_lifecycle.start(
            work_item=work_item,
            contract=contract,
        )
        if record.status != "READY_TO_MERGE":
            await self.present_pending(work_id, project_id=project_id)
            return record
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
        if record.status in {"READY_TO_DELIVER", "WORK_BLOCKED", "GATE_PENDING"}:
            return record

        events = await self._events(work_id, project_id)
        work_item, contract = self._canonical_inputs(events)
        if record.status not in {"READY_TO_MERGE", "MERGE_APPROVED"}:
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
        if self._gate_event(events, WorkEventType.GATE_DECIDED, gate_id) is not None:
            raise ValueError(f"gate is already decided: {gate_id}")

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
            status="MERGE_APPROVED",
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
        pending = await self._work_store.pending_attention(project_id=project_id)
        for item in pending:
            if item.work_id != work_id or item.source_ref is None:
                continue
            if not is_github_issue_url(item.source_ref):
                continue
            await self._github.comment_issue(
                item.source_ref,
                _attention_comment(item),
            )

    async def _advance(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        issue: GitHubIssue,
        record: WorkRecord,
    ) -> WorkRecord:
        pending = await self._work_store.pending_attention(
            project_id=work_item.project_id,
        )
        controls = tuple(
            item
            for item in pending
            if item.work_id == work_item.id and item.kind is PendingAttentionKind.CONTROL_DEGRADED
        )
        if controls:
            await self.present_pending(
                work_item.id,
                project_id=issue.project_id,
            )
            return record

        events = await self._events(work_item.id, work_item.project_id)
        pull_request = self._pull_request(events)
        if pull_request is None:
            software = SoftwareContractContext.model_validate(contract.profile_context)
            branch = f"sagewai/{work_item.id}"
            expected_sha = self._expected_sha(events, software.base_sha)
            branch_sha = await self._branch_publisher.publish(
                project_id=issue.project_id,
                work_id=work_item.id,
                base_sha=software.base_sha,
                expected_sha=expected_sha,
                branch=branch,
                commit_message=f"sagewai work {work_item.id}",
            )
            pull_request = await self._github.create_pull_request(
                issue=issue,
                title=issue.title,
                head=branch,
                base=issue.default_branch,
                body=f"Closes #{issue.number}",
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
            context = GitHubWorkContext(
                project_id=issue.project_id,
                owner=issue.owner,
                repo=issue.repo,
                issue_number=issue.number,
                issue_url=issue.url,
                default_branch=issue.default_branch,
                branch=branch,
                branch_sha=branch_sha,
                pull_request_number=pull_request.number,
                pull_request_url=pull_request.url,
            )
            profile_context = dict(record.profile_context)
            profile_context["github"] = context.model_dump(mode="json")
            record = await self._set_record(
                record,
                status="READY_TO_MERGE",
                pending_gate=None,
                profile_context=profile_context,
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
            risk=contract.risk,
            reversibility=Reversibility.IRREVERSIBLE,
            scope=pull_request.url,
            evidence_refs=self._merge_evidence(
                contract,
                events,
                pull_request.url,
            ),
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
                    status="GATE_PENDING",
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
            return await self._set_record(
                record,
                status="GATE_PENDING",
                pending_gate=gate_id,
            )
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

        state = await self._github.get_pull_request(pull_request)
        if (
            state.project_id != work_item.project_id
            or state.pull_request_number != pull_request.number
        ):
            raise ValueError("pull request state belongs to a different WorkItem")

        if not state.merged:
            merge_result = await self._github.merge_pull_request(pull_request)
            if (
                merge_result.project_id != work_item.project_id
                or merge_result.pull_request_number != pull_request.number
            ):
                raise ValueError("merge result belongs to a different WorkItem")
            state = await self._github.get_pull_request(pull_request)
            if (
                state.project_id != work_item.project_id
                or state.pull_request_number != pull_request.number
            ):
                raise ValueError("pull request state belongs to a different WorkItem")

        if not state.merged:
            raise RuntimeError("GitHub did not report the pull request as merged")
        if state.merge_commit_sha is None:
            raise RuntimeError("GitHub did not report the merged commit SHA")
        merge = GitHubMergeResult(
            project_id=work_item.project_id,
            pull_request_number=pull_request.number,
            merged_sha=state.merge_commit_sha,
        )
        await self._append(
            work_id=work_item.id,
            project_id=work_item.project_id,
            event_type=WorkEventType.STAGE_COMPLETED,
            payload={
                "stage": "merge",
                "pull_request_number": pull_request.number,
                "merged_sha": merge.merged_sha,
            },
            actor_ref="github",
        )
        github_context = GitHubWorkContext.model_validate(
            record.profile_context["github"]
        ).model_copy(update={"merged_sha": merge.merged_sha})
        profile_context = dict(record.profile_context)
        profile_context["github"] = github_context.model_dump(mode="json")
        return await self._set_record(
            record,
            status="READY_TO_DELIVER",
            pending_gate=None,
            profile_context=profile_context,
        )

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
    def _expected_sha(events: list[WorkEvent], base_sha: str) -> str:
        return next(
            (
                str(event.payload_json["current_sha"])
                for event in reversed(events)
                if event.event_type is WorkEventType.STAGE_COMPLETED
                and event.payload_json.get("stage") in {"implement", "repair"}
            ),
            base_sha,
        )

    @staticmethod
    def _pull_request(
        events: list[WorkEvent],
    ) -> GitHubPullRequest | None:
        return next(
            (
                GitHubPullRequest.model_validate(event.payload_json["pull_request"])
                for event in reversed(events)
                if event.event_type is WorkEventType.STAGE_COMPLETED
                and event.payload_json.get("stage") == "pull_request"
            ),
            None,
        )

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


def _attention_comment(item: PendingAttention) -> str:
    summary = item.summary.rstrip(".")
    if item.kind is PendingAttentionKind.GATE_REQUESTED:
        summary = summary[:1].lower() + summary[1:]
        return f"Sagewai: approval required — {summary} (gate {item.attention_id})."
    if item.kind is PendingAttentionKind.WORK_BLOCKED:
        return f"Sagewai: work blocked — {summary}."
    evidence = ", ".join(item.evidence_refs)
    suffix = f" Evidence: {evidence}." if evidence else ""
    return f"Sagewai: control degraded — {item.attention_id}.{suffix}"


__all__ = [
    "CatalogGitHubClient",
    "GitBranchPublisher",
    "GitHubClient",
    "GitHubIssue",
    "GitHubIssueLifecycle",
    "GitHubMergeResult",
    "GitHubPullRequest",
    "GitHubPullRequestState",
    "GitHubWorkContext",
    "WorktreeBranchPublisher",
    "is_github_issue_url",
    "require_merge_approval",
]
