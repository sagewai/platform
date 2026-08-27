# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""GitHub issue, pull request, merge-gate, and presentation acceptance tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest

from sagewai.work import (
    GateDecision,
    WorkEvent,
    WorkEventType,
    WorkRecord,
    WorkStore,
)
from sagewai.work.profiles.software.github import (
    CatalogGitHubClient,
    GitHubIssue,
    GitHubIssueLifecycle,
    GitHubMergeRejectedError,
    GitHubMergeResult,
    GitHubPullRequest,
    GitHubPullRequestState,
    WorktreeBranchPublisher,
)
from tests.db.conftest import dialect_engine  # noqa: F401

PROJECT_ID = "project-a"
ISSUE_URL = "https://github.com/octocat/hello-world/issues/42"
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class FakeSoftwareLifecycle:
    def __init__(self, store: WorkStore, *, degraded: bool = False) -> None:
        self.store = store
        self.degraded = degraded
        self.starts = []
        self.resumes = 0

    async def start(self, *, work_item, contract, assumptions=()):
        self.starts.append((work_item, contract, assumptions))
        for event_type, payload in (
            (WorkEventType.WORK_CREATED, work_item.model_dump(mode="json")),
            (WorkEventType.CONTRACT_ACCEPTED, contract.model_dump(mode="json")),
        ):
            events = await self.store.read_events(
                work_item.id,
                project_id=work_item.project_id,
            )
            await self.store.append_event(
                WorkEvent(
                    id=str(uuid.uuid4()),
                    project_id=work_item.project_id,
                    work_id=work_item.id,
                    sequence=len(events) + 1,
                    event_type=event_type,
                    actor_type="fake_software_lifecycle",
                    actor_ref=None,
                    payload_json=payload,
                    created_at=NOW,
                )
            )
        if self.degraded:
            events = await self.store.read_events(
                work_item.id,
                project_id=work_item.project_id,
            )
            await self.store.append_event(
                WorkEvent(
                    id=str(uuid.uuid4()),
                    project_id=work_item.project_id,
                    work_id=work_item.id,
                    sequence=len(events) + 1,
                    event_type=WorkEventType.CONTROL_DEGRADED,
                    actor_type="fake_software_lifecycle",
                    actor_ref=None,
                    payload_json={
                        "failed_preconditions": ["github-authority"],
                        "evidence_refs": ["check://github-authority"],
                    },
                    created_at=NOW,
                )
            )
        record = WorkRecord(
            work_id=work_item.id,
            project_id=work_item.project_id,
            source_ref=work_item.source_ref,
            profile=work_item.profile,
            status="READY_TO_MERGE",
            contract_version=contract.version,
            active_run_id="review-1",
            pending_gate=None,
            profile_context={"base_sha": contract.profile_context["base_sha"]},
            created_at=NOW,
            updated_at=NOW,
        )
        await self.store.save_work(record)
        return record

    async def resume(self, work_id: str, *, project_id: str):
        self.resumes += 1
        record = await self.store.load_work(work_id, project_id=project_id)
        if record is None:
            raise KeyError(work_id)
        return record


class FakeGitHub:
    def __init__(self) -> None:
        self.issue = GitHubIssue(
            project_id=PROJECT_ID,
            owner="octocat",
            repo="hello-world",
            number=42,
            url=ISSUE_URL,
            title="Fix the deterministic target",
            body="The deterministic verification command passes.",
            default_branch="main",
        )
        self.pull_requests = []
        self.pull_request_reads = []
        self.pull_request_searches = []
        self.merges = []
        self.comments = []
        self.merged_sha = None
        self.fail_after_merge_once = False
        self.fail_create_once = False
        self.fail_after_create_once = False
        self.fail_comment_once = False
        self.merge_rejection = None
        self.readback_sha = None
        self.remote_pull_request = None

    async def fetch_issue(self, issue_url: str) -> GitHubIssue:
        assert issue_url == ISSUE_URL
        return self.issue

    async def find_open_pull_request(
        self,
        *,
        issue: GitHubIssue,
        head: str,
        base: str,
    ) -> GitHubPullRequest | None:
        self.pull_request_searches.append((issue, head, base))
        return self.remote_pull_request

    async def create_pull_request(
        self,
        *,
        issue: GitHubIssue,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> GitHubPullRequest:
        if self.fail_create_once:
            self.fail_create_once = False
            raise RuntimeError("GitHub unavailable")
        self.pull_requests.append(
            {
                "issue": issue,
                "title": title,
                "head": head,
                "base": base,
                "body": body,
            }
        )
        pull_request = GitHubPullRequest(
            project_id=issue.project_id,
            owner=issue.owner,
            repo=issue.repo,
            number=7,
            url="https://github.com/octocat/hello-world/pull/7",
            head=head,
            base=base,
        )
        self.remote_pull_request = pull_request
        if self.fail_after_create_once:
            self.fail_after_create_once = False
            raise RuntimeError("connection lost after pull request creation")
        return pull_request

    async def merge_pull_request(
        self,
        pull_request: GitHubPullRequest,
        *,
        expected_head_sha: str,
    ) -> GitHubMergeResult:
        if self.merge_rejection is not None:
            raise GitHubMergeRejectedError(self.merge_rejection)
        self.merges.append(
            {
                "pull_request": pull_request,
                "expected_head_sha": expected_head_sha,
            }
        )
        self.merged_sha = "c" * 40
        result = GitHubMergeResult(
            project_id=pull_request.project_id,
            pull_request_number=pull_request.number,
            merged_sha=self.merged_sha,
        )
        if self.fail_after_merge_once:
            self.fail_after_merge_once = False
            raise RuntimeError("connection lost after merge")
        return result

    async def get_pull_request(
        self,
        pull_request: GitHubPullRequest,
    ) -> GitHubPullRequestState:
        self.pull_request_reads.append(pull_request)
        return GitHubPullRequestState(
            project_id=pull_request.project_id,
            pull_request_number=pull_request.number,
            merged=self.merged_sha is not None,
            merge_commit_sha=self.readback_sha or self.merged_sha,
        )

    async def comment_issue(self, issue_url: str, body: str) -> None:
        if self.fail_comment_once:
            self.fail_comment_once = False
            raise RuntimeError("GitHub comment unavailable")
        self.comments.append((issue_url, body))


class FakeBranchPublisher:
    def __init__(self) -> None:
        self.validations = []
        self.calls = []
        self.fail_validation_call = None

    async def validate_target(
        self,
        *,
        owner: str,
        repo: str,
        base_sha: str,
        default_branch: str,
    ) -> None:
        self.validations.append((owner, repo, base_sha, default_branch))
        if len(self.validations) == self.fail_validation_call:
            raise ValueError("requested base does not match GitHub default branch")

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
        self.calls.append(
            {
                "project_id": project_id,
                "work_id": work_id,
                "base_sha": base_sha,
                "expected_sha": expected_sha,
                "branch": branch,
                "commit_message": commit_message,
            }
        )
        return "b" * 40


@pytest.fixture
async def store(dialect_engine) -> WorkStore:  # noqa: F811
    result = WorkStore(engine=dialect_engine)
    await result.init()
    return result


def _flow(
    store: WorkStore,
    *,
    decision: GateDecision = GateDecision.REQUIRE_APPROVAL,
    degraded: bool = False,
):
    software = FakeSoftwareLifecycle(store, degraded=degraded)
    github = FakeGitHub()
    publisher = FakeBranchPublisher()
    flow = GitHubIssueLifecycle(
        work_store=store,
        software_lifecycle=software,
        github=github,
        branch_publisher=publisher,
        merge_policy=lambda _request: decision,
    )
    return flow, software, github, publisher


@pytest.mark.asyncio
async def test_issue_to_pr_requires_gate_then_records_merged_sha(
    store: WorkStore,
) -> None:
    flow, software, github, publisher = _flow(store)

    gated = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )

    assert gated.status == "READY_TO_MERGE"
    assert gated.pending_gate == f"merge:{gated.work_id}:7"
    assert github.merges == []
    assert len(software.starts) == 1
    work_item, contract, assumptions = software.starts[0]
    assert work_item.source == "github"
    assert work_item.source_ref == ISSUE_URL
    assert work_item.project_id == PROJECT_ID
    assert contract.goal == github.issue.title
    assert contract.acceptance_criteria == (github.issue.body,)
    assert assumptions == ()
    assert publisher.calls[0]["project_id"] == PROJECT_ID
    assert publisher.calls[0]["branch"] == f"sagewai/{gated.work_id}"
    assert publisher.validations == [
        ("octocat", "hello-world", "a" * 40, "main"),
        ("octocat", "hello-world", "a" * 40, "main"),
    ]
    assert github.pull_requests[0]["base"] == "main"
    assert github.comments == [
        (
            ISSUE_URL,
            f"Sagewai: approval required — approve merge of PR #7 (gate {gated.pending_gate}).",
        )
    ]

    delivered = await flow.approve(
        gated.work_id,
        project_id=PROJECT_ID,
        gate_id=gated.pending_gate,
        actor_ref="operator:arda",
    )

    assert delivered.profile_context["github"]["project_id"] == PROJECT_ID
    assert delivered.status == "READY_TO_DELIVER"
    assert delivered.status != "COMPLETE"
    assert delivered.pending_gate is None
    assert delivered.profile_context["github"]["merged_sha"] == "c" * 40
    assert len(github.merges) == 1
    assert github.merges[0]["expected_head_sha"] == "b" * 40
    assert len(publisher.validations) == 2
    assert await store.pending_attention(project_id=PROJECT_ID) == ()

    events = await store.read_events(gated.work_id, project_id=PROJECT_ID)
    requested = next(event for event in events if event.event_type is WorkEventType.GATE_REQUESTED)
    assert requested.payload_json["action"]["action"] == "merge"
    assert requested.payload_json["action"]["risk"] == "medium"
    assert requested.payload_json["action"]["reversibility"] == "irreversible"
    decided = next(event for event in events if event.event_type is WorkEventType.GATE_DECIDED)
    assert decided.actor_ref == "operator:arda"
    assert decided.payload_json["decision"] == "allow"
    merged = next(
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "merge"
    )
    assert merged.payload_json["merged_sha"] == "c" * 40
    assert all(event.event_type is not WorkEventType.WORK_COMPLETED for event in events)


@pytest.mark.asyncio
async def test_remote_pr_failure_resumes_from_recorded_branch_without_rerunning_software(
    store: WorkStore,
) -> None:
    flow, software, github, publisher = _flow(store)
    github.fail_create_once = True

    with pytest.raises(RuntimeError, match="GitHub unavailable"):
        await flow.start(
            issue_url=ISSUE_URL,
            project_id=PROJECT_ID,
            base_sha="a" * 40,
        )

    work_id = software.starts[0][0].id
    events = await store.read_events(work_id, project_id=PROJECT_ID)
    publications = [
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "branch_published"
    ]
    assert len(publications) == 1

    gated = await flow.resume(work_id, project_id=PROJECT_ID)

    assert gated.status == "READY_TO_MERGE"
    assert len(publisher.calls) == 1
    assert len(github.pull_requests) == 1
    assert len(software.starts) == 1
    assert software.resumes == 0


@pytest.mark.asyncio
async def test_resume_recovers_pull_request_created_before_event_persistence(
    store: WorkStore,
) -> None:
    flow, software, github, _ = _flow(store)
    github.fail_after_create_once = True

    with pytest.raises(RuntimeError, match="connection lost after pull request creation"):
        await flow.start(
            issue_url=ISSUE_URL,
            project_id=PROJECT_ID,
            base_sha="a" * 40,
        )

    work_id = software.starts[0][0].id
    events = await store.read_events(work_id, project_id=PROJECT_ID)
    assert not any(
        event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "pull_request"
        for event in events
    )

    gated = await flow.resume(work_id, project_id=PROJECT_ID)

    assert gated.status == "READY_TO_MERGE"
    assert len(github.pull_requests) == 1
    assert len(github.pull_request_searches) == 2
    assert len(software.starts) == 1
    assert software.resumes == 0
    events = await store.read_events(work_id, project_id=PROJECT_ID)
    pull_requests = [
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "pull_request"
    ]
    assert len(pull_requests) == 1


@pytest.mark.asyncio
async def test_resume_rebuilds_pr_projection_and_does_not_duplicate_merge_event(
    store: WorkStore,
    monkeypatch,
) -> None:
    flow, software, _, _ = _flow(store)
    original_save = store.save_work
    failed = False

    async def fail_after_pr_event(record):
        nonlocal failed
        if not failed and record.status == "READY_TO_MERGE" and "github" in record.profile_context:
            failed = True
            raise RuntimeError("projection write interrupted")
        await original_save(record)

    monkeypatch.setattr(store, "save_work", fail_after_pr_event)
    with pytest.raises(RuntimeError, match="projection write interrupted"):
        await flow.start(
            issue_url=ISSUE_URL,
            project_id=PROJECT_ID,
            base_sha="a" * 40,
        )
    monkeypatch.setattr(store, "save_work", original_save)

    gated = await flow.resume(software.starts[0][0].id, project_id=PROJECT_ID)
    assert gated.status == "READY_TO_MERGE"
    assert gated.profile_context["github"]["pull_request_number"] == 7
    delivered = await flow.approve(
        gated.work_id,
        project_id=PROJECT_ID,
        gate_id=gated.pending_gate,
        actor_ref="operator:arda",
    )
    assert delivered.status == "READY_TO_DELIVER"

    resumed = await flow.resume(delivered.work_id, project_id=PROJECT_ID)
    assert resumed.status == "READY_TO_DELIVER"
    events = await store.read_events(delivered.work_id, project_id=PROJECT_ID)
    merges = [
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "merge"
    ]
    assert len(merges) == 1


@pytest.mark.asyncio
async def test_retry_approval_recovers_decision_recorded_before_projection(
    store: WorkStore,
    monkeypatch,
) -> None:
    flow, _, github, _ = _flow(store)
    gated = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )
    original_save = store.save_work
    failed = False

    async def fail_after_gate_decision(record):
        nonlocal failed
        if not failed and record.status == "MERGING":
            failed = True
            raise RuntimeError("projection write interrupted")
        await original_save(record)

    monkeypatch.setattr(store, "save_work", fail_after_gate_decision)
    with pytest.raises(RuntimeError, match="projection write interrupted"):
        await flow.approve(
            gated.work_id,
            project_id=PROJECT_ID,
            gate_id=gated.pending_gate,
            actor_ref="operator:arda",
        )
    monkeypatch.setattr(store, "save_work", original_save)

    delivered = await flow.approve(
        gated.work_id,
        project_id=PROJECT_ID,
        gate_id=gated.pending_gate,
        actor_ref="operator:arda",
    )

    assert delivered.status == "READY_TO_DELIVER"
    assert len(github.merges) == 1


@pytest.mark.asyncio
async def test_control_degraded_freezes_start_resume_and_approved_merge(
    store: WorkStore,
) -> None:
    frozen, software, github, publisher = _flow(store, degraded=True)
    record = await frozen.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )
    resumed = await frozen.resume(record.work_id, project_id=PROJECT_ID)

    assert record.status == resumed.status == "READY_TO_MERGE"
    assert publisher.calls == []
    assert github.pull_requests == []
    assert github.merges == []
    assert software.resumes == 0
    control_comments = [body for _, body in github.comments if "control degraded" in body]
    assert len(control_comments) == 1

    gated_flow, _, gated_github, gated_publisher = _flow(store)
    gated = await gated_flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )
    events = await store.read_events(gated.work_id, project_id=PROJECT_ID)
    await store.append_event(
        WorkEvent(
            id=str(uuid.uuid4()),
            project_id=PROJECT_ID,
            work_id=gated.work_id,
            sequence=events[-1].sequence + 1,
            event_type=WorkEventType.CONTROL_DEGRADED,
            actor_type="test",
            actor_ref=None,
            payload_json={
                "failed_preconditions": ["github-authority"],
                "evidence_refs": ["check://github-authority"],
            },
            created_at=NOW,
        )
    )

    approved = await gated_flow.approve(
        gated.work_id,
        project_id=PROJECT_ID,
        gate_id=gated.pending_gate,
        actor_ref="operator:arda",
    )
    again = await gated_flow.resume(gated.work_id, project_id=PROJECT_ID)

    assert approved.status == again.status == "MERGING"
    assert len(gated_publisher.calls) == 1
    assert len(gated_github.pull_requests) == 1
    assert gated_github.merges == []
    control_comments = [body for _, body in gated_github.comments if "control degraded" in body]
    assert len(control_comments) == 1


@pytest.mark.asyncio
async def test_pending_comment_failure_is_retried_on_resume(store: WorkStore) -> None:
    flow, _, github, _ = _flow(store)
    github.fail_comment_once = True

    with pytest.raises(RuntimeError, match="GitHub comment unavailable"):
        await flow.start(
            issue_url=ISSUE_URL,
            project_id=PROJECT_ID,
            base_sha="a" * 40,
        )

    records = await store.pending_attention(project_id=PROJECT_ID)
    assert len(records) == 1
    gated = await flow.resume(records[0].work_id, project_id=PROJECT_ID)
    again = await flow.resume(records[0].work_id, project_id=PROJECT_ID)

    assert gated.status == "READY_TO_MERGE"
    assert again.status == "READY_TO_MERGE"
    assert len(github.comments) == 1
    assert "approval required" in github.comments[0][1]


@pytest.mark.asyncio
async def test_github_rejected_merge_blocks_with_specific_pending_question(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store)
    gated = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )
    github.merge_rejection = "Head branch was modified"
    github.fail_comment_once = True

    with pytest.raises(RuntimeError, match="GitHub comment unavailable"):
        await flow.approve(
            gated.work_id,
            project_id=PROJECT_ID,
            gate_id=gated.pending_gate,
            actor_ref="operator:arda",
        )
    blocked = await flow.resume(gated.work_id, project_id=PROJECT_ID)

    assert blocked.status == "WORK_BLOCKED"
    assert github.merges == []
    pending = await store.pending_attention(project_id=PROJECT_ID)
    blocker = next(item for item in pending if item.kind.value == "WORK_BLOCKED")
    assert "Head branch was modified" in blocker.summary
    assert "stop the work" in blocker.summary
    assert any("Head branch was modified" in body for _, body in github.comments)


@pytest.mark.asyncio
async def test_target_movement_degrades_control_and_resume_restores_it(
    store: WorkStore,
) -> None:
    flow, software, github, publisher = _flow(store)
    publisher.fail_validation_call = 2

    frozen = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )

    assert frozen.status == "READY_TO_MERGE"
    assert publisher.calls == []
    assert github.pull_requests == []
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert [item.kind.value for item in pending] == ["CONTROL_DEGRADED"]
    assert pending[0].attention_id == "github-target"
    assert any("requested base does not match GitHub default branch" in body for _, body in github.comments)

    publisher.fail_validation_call = None
    resumed = await flow.resume(frozen.work_id, project_id=PROJECT_ID)

    assert resumed.status == "READY_TO_MERGE"
    assert resumed.pending_gate == f"merge:{frozen.work_id}:7"
    assert len(software.starts) == 1
    events = await store.read_events(frozen.work_id, project_id=PROJECT_ID)
    degraded = next(
        event for event in events if event.event_type is WorkEventType.CONTROL_DEGRADED
    )
    assert degraded.payload_json["frozen_action_ids"] == [
        "publish_branch",
        "create_pull_request",
        "merge",
    ]
    assert "frozen_actions" not in degraded.payload_json
    assert any(
        event.event_type is WorkEventType.CONTROL_DEGRADED
        for event in events
    )
    assert any(
        event.event_type is WorkEventType.CONTROL_RESTORED
        for event in events
    )


@pytest.mark.asyncio
async def test_merge_response_sha_must_match_github_readback(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store, decision=GateDecision.ALLOW)
    github.readback_sha = "d" * 40

    with pytest.raises(RuntimeError, match="merge response SHA conflicts"):
        await flow.start(
            issue_url=ISSUE_URL,
            project_id=PROJECT_ID,
            base_sha="a" * 40,
        )

    assert len(github.merges) == 1


@pytest.mark.asyncio
async def test_allow_policy_merges_without_requesting_operator_approval(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store, decision=GateDecision.ALLOW)

    delivered = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )

    assert delivered.status == "READY_TO_DELIVER"
    assert len(github.merges) == 1
    events = await store.read_events(delivered.work_id, project_id=PROJECT_ID)
    assert all(event.event_type is not WorkEventType.GATE_REQUESTED for event in events)
    decision = next(event for event in events if event.event_type is WorkEventType.GATE_DECIDED)
    assert decision.payload_json["decision"] == "allow"


@pytest.mark.parametrize(
    "origin",
    (
        "https://github.com/octocat/hello-world.git",
        "ssh://git@github.com/octocat/hello-world.git",
    ),
)
@pytest.mark.asyncio
async def test_branch_publisher_accepts_supported_github_origins(
    origin,
    monkeypatch,
    tmp_path,
) -> None:
    calls = []
    outputs = iter((f"{origin}\n", f"{'a' * 40}\trefs/heads/main\n"))

    async def fake_subprocess(**kwargs):
        calls.append(kwargs["argv"])
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": next(outputs), "stderr": ""},
        )()

    monkeypatch.setattr(
        "sagewai.work.profiles.software.github.run_worker_subprocess",
        fake_subprocess,
    )
    publisher = WorktreeBranchPublisher(
        worktree_manager=object(),
        repository=tmp_path,
    )

    await publisher.validate_target(
        owner="octocat",
        repo="hello-world",
        base_sha="a" * 40,
        default_branch="main",
    )

    assert calls == [
        ("git", "remote", "get-url", "origin"),
        ("git", "ls-remote", "--exit-code", "origin", "refs/heads/main"),
    ]


@pytest.mark.asyncio
async def test_branch_publisher_rejects_unrelated_github_origin(monkeypatch, tmp_path) -> None:
    calls = []

    async def fake_subprocess(**kwargs):
        calls.append(kwargs["argv"])
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": "git@github.com:other/repo.git\n", "stderr": ""},
        )()

    monkeypatch.setattr(
        "sagewai.work.profiles.software.github.run_worker_subprocess",
        fake_subprocess,
    )
    publisher = WorktreeBranchPublisher(
        worktree_manager=object(),
        repository=tmp_path,
    )

    with pytest.raises(ValueError, match="does not match issue repository"):
        await publisher.validate_target(
            owner="octocat",
            repo="hello-world",
            base_sha="a" * 40,
            default_branch="main",
        )

    assert calls == [("git", "remote", "get-url", "origin")]


@pytest.mark.asyncio
async def test_branch_publisher_rejects_base_not_at_default_branch(monkeypatch, tmp_path) -> None:
    outputs = iter(("git@github.com:octocat/hello-world.git\n", f"{'b' * 40}\trefs/heads/main\n"))

    async def fake_subprocess(**_kwargs):
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": next(outputs), "stderr": ""},
        )()

    monkeypatch.setattr(
        "sagewai.work.profiles.software.github.run_worker_subprocess",
        fake_subprocess,
    )
    publisher = WorktreeBranchPublisher(
        worktree_manager=object(),
        repository=tmp_path,
    )

    with pytest.raises(ValueError, match="requested base does not match GitHub default branch"):
        await publisher.validate_target(
            owner="octocat",
            repo="hello-world",
            base_sha="a" * 40,
            default_branch="main",
        )


@pytest.mark.asyncio
async def test_merge_policy_denial_blocks_without_merge_side_effect(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store, decision=GateDecision.DENY)

    record = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )

    assert record.status == "WORK_BLOCKED"
    assert github.merges == []
    assert any("work blocked" in body.lower() for _, body in github.comments)
    events = await store.read_events(record.work_id, project_id=PROJECT_ID)
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert blocker.payload_json["reason"] == "merge_policy_denied"


@pytest.mark.asyncio
async def test_resume_recovers_merge_completed_before_event_persistence(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store)
    gated = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )
    github.fail_after_merge_once = True

    with pytest.raises(RuntimeError, match="connection lost after merge"):
        await flow.approve(
            gated.work_id,
            project_id=PROJECT_ID,
            gate_id=gated.pending_gate,
            actor_ref="operator:arda",
        )

    interrupted = await store.load_work(gated.work_id, project_id=PROJECT_ID)
    assert interrupted is not None
    assert interrupted.status == "MERGING"
    assert github.merged_sha == "c" * 40

    delivered = await flow.resume(gated.work_id, project_id=PROJECT_ID)

    assert delivered.status == "READY_TO_DELIVER"
    assert delivered.profile_context["github"]["merged_sha"] == "c" * 40
    assert len(github.merges) == 1
    events = await store.read_events(gated.work_id, project_id=PROJECT_ID)
    merge_events = [
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "merge"
    ]
    assert len(merge_events) == 1
    assert merge_events[0].payload_json["merged_sha"] == "c" * 40


@pytest.mark.asyncio
async def test_pending_attention_is_presented_as_concise_issue_comments(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store)
    await store.save_work(
        WorkRecord(
            work_id="work-1",
            project_id=PROJECT_ID,
            source_ref=ISSUE_URL,
            profile="software",
            status="WORK_BLOCKED",
            contract_version=1,
            active_run_id=None,
            pending_gate=None,
            profile_context={"base_sha": "a" * 40},
            created_at=NOW,
            updated_at=NOW,
        )
    )
    await store.append_event(
        WorkEvent(
            id="blocked-event",
            project_id=PROJECT_ID,
            work_id="work-1",
            sequence=1,
            event_type=WorkEventType.WORK_BLOCKED,
            actor_type="test",
            actor_ref=None,
            payload_json={
                "reason": "operator_input_required",
                "decision_request": "Choose the target branch.",
            },
            created_at=NOW,
        )
    )
    await store.append_event(
        WorkEvent(
            id="degraded-event",
            project_id=PROJECT_ID,
            work_id="work-1",
            sequence=2,
            event_type=WorkEventType.CONTROL_DEGRADED,
            actor_type="test",
            actor_ref=None,
            payload_json={
                "failed_preconditions": ["observability"],
                "evidence_refs": ["check://stale"],
            },
            created_at=NOW,
        )
    )

    await flow.present_pending("work-1", project_id=PROJECT_ID)

    bodies = [body for _, body in github.comments]
    assert "Sagewai: work blocked — Choose the target branch." in bodies
    assert "Sagewai: control degraded — observability. Evidence: check://stale." in bodies


@pytest.mark.asyncio
async def test_catalog_client_types_github_merge_conflict() -> None:
    async def github_callable(_payload):
        request = httpx.Request(
            "PUT",
            "https://api.github.com/repos/octocat/hello-world/pulls/7/merge",
        )
        response = httpx.Response(
            409,
            request=request,
            json={"message": "Head branch was modified"},
        )
        raise httpx.HTTPStatusError(
            "merge conflict",
            request=request,
            response=response,
        )

    client = CatalogGitHubClient(
        project_id=PROJECT_ID,
        github_callable=github_callable,
    )
    pull_request = GitHubPullRequest(
        project_id=PROJECT_ID,
        owner="octocat",
        repo="hello-world",
        number=7,
        url="https://github.com/octocat/hello-world/pull/7",
        head="sagewai/work-1",
        base="main",
    )

    with pytest.raises(GitHubMergeRejectedError, match="Head branch was modified"):
        await client.merge_pull_request(pull_request, expected_head_sha="e" * 40)


@pytest.mark.asyncio
async def test_catalog_client_adapts_existing_github_callable() -> None:
    calls = []

    async def github_callable(payload):
        calls.append(dict(payload))
        operation = payload["_operation"]
        if operation == "get_repo":
            return {"default_branch": "main"}
        if operation == "get_issue":
            return {
                "number": 42,
                "html_url": ISSUE_URL,
                "title": "Fix target",
                "body": "Acceptance",
            }
        if operation == "find_pull_requests":
            return [
                {
                    "number": 7,
                    "html_url": "https://github.com/octocat/hello-world/pull/7",
                }
            ]
        if operation == "create_pull_request":
            return {
                "number": 7,
                "html_url": "https://github.com/octocat/hello-world/pull/7",
            }
        if operation == "get_pull_request":
            return {
                "number": 7,
                "html_url": "https://github.com/octocat/hello-world/pull/7",
                "state": "closed",
                "merged": True,
                "merge_commit_sha": "d" * 40,
            }
        if operation == "merge_pull_request":
            return {"sha": "d" * 40, "merged": True, "message": "merged"}
        if operation == "create_comment":
            return {"id": 9, "html_url": f"{ISSUE_URL}#issuecomment-9", "body": payload["body"]}
        raise AssertionError(operation)

    client = CatalogGitHubClient(
        project_id=PROJECT_ID,
        github_callable=github_callable,
    )
    issue = await client.fetch_issue(ISSUE_URL)
    found = await client.find_open_pull_request(
        issue=issue,
        head="sagewai/work-1",
        base="main",
    )
    pull_request = await client.create_pull_request(
        issue=issue,
        title=issue.title,
        head="sagewai/work-1",
        base=issue.default_branch,
        body="Closes #42",
    )
    state = await client.get_pull_request(pull_request)
    await client.comment_issue(ISSUE_URL, "Pending approval")
    merge = await client.merge_pull_request(pull_request, expected_head_sha="e" * 40)

    assert issue.default_branch == "main"
    assert found is not None
    assert found.number == 7
    assert calls[2]["head"] == "octocat:sagewai/work-1"
    assert pull_request.number == 7
    assert merge.merged_sha == "d" * 40
    assert calls[-1]["sha"] == "e" * 40
    assert state.merged is True
    assert state.merge_commit_sha == "d" * 40
    assert [call["_operation"] for call in calls] == [
        "get_repo",
        "get_issue",
        "find_pull_requests",
        "create_pull_request",
        "get_pull_request",
        "create_comment",
        "merge_pull_request",
    ]
