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
    GitHubMergeResult,
    GitHubPullRequest,
    GitHubPullRequestState,
)
from tests.db.conftest import dialect_engine  # noqa: F401

PROJECT_ID = "project-a"
ISSUE_URL = "https://github.com/octocat/hello-world/issues/42"
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class FakeSoftwareLifecycle:
    def __init__(self, store: WorkStore) -> None:
        self.store = store
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
        self.merges = []
        self.comments = []
        self.merged_sha = None
        self.fail_after_merge_once = False

    async def fetch_issue(self, issue_url: str) -> GitHubIssue:
        assert issue_url == ISSUE_URL
        return self.issue

    async def create_pull_request(
        self,
        *,
        issue: GitHubIssue,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> GitHubPullRequest:
        self.pull_requests.append(
            {
                "issue": issue,
                "title": title,
                "head": head,
                "base": base,
                "body": body,
            }
        )
        return GitHubPullRequest(
            project_id=issue.project_id,
            owner=issue.owner,
            repo=issue.repo,
            number=7,
            url="https://github.com/octocat/hello-world/pull/7",
            head=head,
            base=base,
        )

    async def merge_pull_request(
        self,
        pull_request: GitHubPullRequest,
    ) -> GitHubMergeResult:
        self.merges.append(pull_request)
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
            merge_commit_sha=self.merged_sha,
        )

    async def comment_issue(self, issue_url: str, body: str) -> None:
        self.comments.append((issue_url, body))


class FakeBranchPublisher:
    def __init__(self) -> None:
        self.calls = []

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
):
    software = FakeSoftwareLifecycle(store)
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

    assert gated.status == "GATE_PENDING"
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
    assert await store.pending_attention(project_id=PROJECT_ID) == ()

    events = await store.read_events(gated.work_id, project_id=PROJECT_ID)
    requested = next(event for event in events if event.event_type is WorkEventType.GATE_REQUESTED)
    assert requested.payload_json["action"]["action"] == "merge"
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
    assert interrupted.status == "MERGE_APPROVED"
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
    pull_request = await client.create_pull_request(
        issue=issue,
        title=issue.title,
        head="sagewai/work-1",
        base=issue.default_branch,
        body="Closes #42",
    )
    state = await client.get_pull_request(pull_request)
    await client.comment_issue(ISSUE_URL, "Pending approval")
    merge = await client.merge_pull_request(pull_request)

    assert issue.default_branch == "main"
    assert pull_request.number == 7
    assert merge.merged_sha == "d" * 40
    assert state.merged is True
    assert state.merge_commit_sha == "d" * 40
    assert [call["_operation"] for call in calls] == [
        "get_repo",
        "get_issue",
        "create_pull_request",
        "get_pull_request",
        "create_comment",
        "merge_pull_request",
    ]
