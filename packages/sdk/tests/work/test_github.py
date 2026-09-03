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
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sagewai.work import (
    AcceptanceCriterion,
    CompletionEvaluation,
    GateDecision,
    PendingAttentionKind,
    VerificationResult,
    WorkEvent,
    WorkEventType,
    WorkRecord,
    WorkStore,
)
from sagewai.work.profiles.software import (
    SoftwareContractContext,
    SoftwareDeliveryContractContext,
    SoftwareRepositoryOutcome,
)
from sagewai.work.profiles.software.github import (
    BaseMovedError,
    CatalogGitHubClient,
    GitHubComment,
    GitHubIssue,
    GitHubIssueLifecycle,
    GitHubMergeRejectedError,
    GitHubMergeResult,
    GitHubPullRequest,
    GitHubPullRequestState,
    WorktreeBranchPublisher,
    is_github_comment_url,
    parse_comment_url,
    parse_pull_request_url,
)
from tests.db.conftest import dialect_engine  # noqa: F401

PROJECT_ID = "project-a"
ISSUE_URL = "https://github.com/octocat/hello-world/issues/42"
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class FakeSoftwareLifecycle:
    def __init__(
        self,
        store: WorkStore,
        *,
        degraded: bool = False,
        pause_analysis: bool = False,
        delivery: bool = False,
        missing_criterion: bool = False,
    ) -> None:
        self.store = store
        self.degraded = degraded
        self.pause_analysis = pause_analysis
        self.delivery = delivery
        self.missing_criterion = missing_criterion
        self.starts = []
        self.resumes = 0
        self.analysis_contracts = {}

    async def start(self, *, work_item, contract, assumptions=()):
        if self.delivery:
            delivery_criterion_id = f"{contract.id}:delivery"
            software = SoftwareContractContext.model_validate(
                contract.profile_context
            ).model_copy(
                update={
                    "delivery": SoftwareDeliveryContractContext(
                        project_id=work_item.project_id,
                        target_environment="test",
                        criterion_ids=(delivery_criterion_id,),
                        release_provider_ref="provider://release/test",
                        deployment_provider_ref="provider://deployment/test",
                        observation_provider_ref="provider://observation/test",
                        rollout_policy_ref="policy://rollout/test",
                        rollback_policy_ref="policy://rollback/test",
                    )
                }
            )
            contract = contract.model_copy(
                update={
                    "acceptance_criteria": contract.acceptance_criteria
                    + (
                        AcceptanceCriterion(
                            id=delivery_criterion_id,
                            project_id=work_item.project_id,
                            statement="deliver to the explicit test target",
                            verification_kind="profile",
                        ),
                    ),
                    "profile_context": software.model_dump(mode="json"),
                }
            )
        if self.missing_criterion:
            contract = contract.model_copy(
                update={
                    "acceptance_criteria": contract.acceptance_criteria
                    + (
                        AcceptanceCriterion(
                            id=f"{contract.id}:missing",
                            project_id=work_item.project_id,
                            statement="prove the intentionally missing criterion",
                            verification_kind="profile",
                        ),
                    )
                }
            )
        self.starts.append((work_item, contract, assumptions))
        contract_event = (
            WorkEventType.CONTRACT_PROPOSED
            if self.pause_analysis
            else WorkEventType.CONTRACT_ACCEPTED
        )
        for event_type, payload in (
            (WorkEventType.WORK_CREATED, work_item.model_dump(mode="json")),
            (contract_event, contract.model_dump(mode="json")),
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
        if self.pause_analysis:
            self.analysis_contracts[work_item.id] = contract
        software = SoftwareContractContext.model_validate(contract.profile_context)
        record = WorkRecord(
            work_id=work_item.id,
            project_id=work_item.project_id,
            source_ref=work_item.source_ref,
            profile=work_item.profile,
            status="ANALYZING" if self.pause_analysis else "READY_TO_MERGE",
            contract_version=None if self.pause_analysis else contract.version,
            active_run_id="review-1",
            pending_gate=None,
            profile_context=(
                {"base_sha": software.base_sha}
                if software.task_id is None
                else {"base_sha": software.base_sha, "task_id": software.task_id}
            ),
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
        contract = self.analysis_contracts.pop(work_id, None)
        if record.status == "ANALYZING" and contract is not None:
            events = await self.store.read_events(work_id, project_id=project_id)
            await self.store.append_event(
                WorkEvent(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    work_id=work_id,
                    sequence=len(events) + 1,
                    event_type=WorkEventType.CONTRACT_ACCEPTED,
                    actor_type="fake_software_lifecycle",
                    actor_ref=None,
                    payload_json=contract.model_dump(mode="json"),
                    created_at=NOW,
                )
            )
            record = record.model_copy(
                update={
                    "status": "READY_TO_MERGE",
                    "contract_version": contract.version,
                    "updated_at": NOW,
                }
            )
            await self.store.save_work(record)
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
        self.deleted_comments: list[str] = []
        self.merged_sha = None
        self.fail_after_merge_once = False
        self.fail_create_once = False
        self.fail_after_create_once = False
        self.fail_comment_once = False
        self.merge_rejection = None
        self.readback_sha = None
        self.readback_merged_once: bool | None = None
        self.remote_pull_request = None
        self.labeled_issues = ()

    async def list_labeled_issues(
        self,
        *,
        owner: str,
        repo: str,
        label: str,
    ) -> tuple[GitHubIssue, ...]:
        assert (owner, repo, label) == ("octocat", "hello-world", "sagewai")
        return self.labeled_issues

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
            head_sha="b" * 40,
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
        readback_merged_once = self.readback_merged_once
        self.readback_merged_once = None
        return GitHubPullRequestState(
            project_id=pull_request.project_id,
            pull_request_number=pull_request.number,
            merged=(
                self.merged_sha is not None
                if readback_merged_once is None
                else readback_merged_once
            ),
            merge_commit_sha=self.readback_sha or self.merged_sha,
        )

    async def comment_issue(self, issue_url: str, body: str) -> GitHubComment:
        if self.fail_comment_once:
            self.fail_comment_once = False
            raise RuntimeError("GitHub comment unavailable")
        self.comments.append((issue_url, body))
        return GitHubComment(
            project_id=PROJECT_ID,
            id=len(self.comments),
            url=f"{issue_url}#issuecomment-{len(self.comments)}",
            body=body,
        )

    async def delete_comment(self, comment_url: str) -> None:
        self.deleted_comments.append(comment_url)


class FakeBranchPublisher:
    def __init__(self) -> None:
        self.validations = []
        self.calls = []
        self.fail_phases: set[str] = set()
        self.validation_error: Exception | None = None

    def _validation_phase(self) -> str:
        if self.calls:
            return "merge"
        if self.validations:
            return "publish"
        return "intake"

    async def validate_target(
        self,
        *,
        owner: str,
        repo: str,
        base_sha: str,
        default_branch: str,
    ) -> None:
        phase = self._validation_phase()
        self.validations.append((owner, repo, base_sha, default_branch))
        if phase in self.fail_phases:
            if self.validation_error is not None:
                raise self.validation_error
            raise BaseMovedError(expected=base_sha, found="other")

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

def _external_incident_event(
    *,
    work_id: str,
    sequence: int,
    deployment_id: str,
    severity: str,
    evidence_refs: tuple[str, ...],
    active_control_event_ids: tuple[str, ...] = (),
    cause: str | None = None,
    created_at: datetime = NOW,
) -> WorkEvent:
    summary = f"production incident for deployment {deployment_id}"
    if cause is not None:
        summary = f"{summary}; {cause}"
    return WorkEvent(
        id=f"incident-{sequence}",
        project_id=PROJECT_ID,
        work_id=work_id,
        sequence=sequence,
        event_type=WorkEventType.EXTERNAL_OUTCOME_RECORDED,
        actor_type="software",
        actor_ref="software_delivery",
        payload_json={
            "incident": {
                "incident_id": f"software-delivery:{deployment_id}",
                "summary": summary,
                "severity": severity,
                "evidence_refs": list(evidence_refs),
                "active_control_event_ids": list(active_control_event_ids),
            }
        },
        created_at=created_at,
    )

def _flow(
    store: WorkStore,
    *,
    decision: GateDecision = GateDecision.REQUIRE_APPROVAL,
    degraded: bool = False,
    pause_analysis: bool = False,
    repository_outcome: SoftwareRepositoryOutcome = SoftwareRepositoryOutcome.MERGED,
    delivery: bool = False,
    missing_criterion: bool = False,
    execution_route: str | None = None,
    fleet_org_id: str | None = None,
):
    software = FakeSoftwareLifecycle(
        store,
        degraded=degraded,
        pause_analysis=pause_analysis,
        delivery=delivery,
        missing_criterion=missing_criterion,
    )
    github = FakeGitHub()
    publisher = FakeBranchPublisher()
    flow = GitHubIssueLifecycle(
        work_store=store,
        software_lifecycle=software,
        github=github,
        branch_publisher=publisher,
        merge_policy=lambda _request: decision,
        repository_outcome=repository_outcome,
        execution_route=execution_route,
        fleet_org_id=fleet_org_id,
    )
    return flow, software, github, publisher


@pytest.mark.asyncio
async def test_labeled_intake_starts_one_unseen_issue_once(
    store: WorkStore,
) -> None:
    flow, software, github, _publisher = _flow(store)
    github.labeled_issues = (github.issue,)

    started = await flow.intake_labeled(
        owner="octocat",
        repo="hello-world",
        label="sagewai",
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )
    repeated = await flow.intake_labeled(
        owner="octocat",
        repo="hello-world",
        label="sagewai",
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )

    assert started is not None
    assert started.source_ref == ISSUE_URL
    assert repeated is None
    assert len(software.starts) == 1
    assert (
        await store.find_work_by_source_ref(
            ISSUE_URL,
            project_id=PROJECT_ID,
        )
        == started
    )


@pytest.mark.asyncio
async def test_issue_to_pr_requires_gate_then_records_merged_sha(
    store: WorkStore,
) -> None:
    flow, software, github, publisher = _flow(
        store,
        execution_route="fleet",
        fleet_org_id="org-a",
    )

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
    execution = SoftwareContractContext.model_validate(contract.profile_context)
    assert execution.execution_route == "fleet"
    assert execution.fleet_org_id == "org-a"
    assert len(contract.acceptance_criteria) == 1
    repository_criterion = contract.acceptance_criteria[0]
    assert repository_criterion.id == f"{contract.id}:repository"
    assert repository_criterion.project_id == PROJECT_ID
    assert repository_criterion.statement == "produce the accepted repository outcome"
    assert repository_criterion.verification_kind == "profile"
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
    assert delivered.status == "COMPLETE"
    assert delivered.pending_gate is None
    assert delivered.profile_context["github"]["merged_sha"] == "c" * 40
    assert len(github.merges) == 1
    assert github.merges[0]["expected_head_sha"] == "b" * 40
    assert len(publisher.validations) == 3
    assert publisher.validations[-1] == ("octocat", "hello-world", "a" * 40, "main")
    assert await store.pending_attention(project_id=PROJECT_ID) == ()

    events = await store.read_events(gated.work_id, project_id=PROJECT_ID)
    requested = next(event for event in events if event.event_type is WorkEventType.GATE_REQUESTED)
    assert requested.payload_json["action"]["action"] == "merge"
    assert requested.payload_json["action"]["risk"] == "medium"
    assert requested.payload_json["action"]["reversibility"] == "compensatable"
    assert requested.payload_json["action"]["rollback"] == "revert_pull_request"
    assert requested.payload_json["action"]["post_check"] == "merged_sha_read_back"
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
    repository_event = next(
        event
        for event in events
        if event.event_type is WorkEventType.VERIFICATION_RECORDED
        and event.payload_json.get("stage") == "repository"
    )
    repository_result = VerificationResult.model_validate(repository_event.payload_json)
    assert repository_result.project_id == PROJECT_ID
    assert repository_result.contract_id == contract.id
    assert repository_result.evidence_refs == (
        "https://github.com/octocat/hello-world/pull/7",
        "git://" + "c" * 40,
    )
    assert repository_result.profile_context["repository_outcome"] == "merged"
    assert repository_result.profile_context["result_sha"] == "c" * 40
    completion_event = next(
        event for event in events if event.event_type is WorkEventType.WORK_COMPLETED
    )
    evaluation = CompletionEvaluation.model_validate(completion_event.payload_json)
    assert evaluation.project_id == PROJECT_ID
    assert evaluation.work_id == gated.work_id
    assert evaluation.contract_id == contract.id
    assert evaluation.passed is True


@pytest.mark.asyncio
async def test_a_successful_merge_records_its_post_check(store: WorkStore) -> None:
    flow, _, github, _ = _flow(store, decision=GateDecision.ALLOW)

    delivered = await flow.start(
        issue_url=ISSUE_URL, project_id=PROJECT_ID, base_sha="a" * 40
    )

    observations = [
        event
        for event in await store.read_events(delivered.work_id, project_id=PROJECT_ID)
        if event.event_type is WorkEventType.OBSERVATION_RECORDED
    ]
    assert [event.payload_json["check"] for event in observations] == [
        "merged_sha_read_back"
    ]
    assert observations[0].payload_json["passed"] is True
    assert observations[0].payload_json["action_id"] == f"merge:{delivered.work_id}:7"
    assert delivered.status == "COMPLETE"


@pytest.mark.asyncio
async def test_an_unmerged_read_back_blocks_without_a_revertable_sha(
    store: WorkStore, monkeypatch
) -> None:
    flow, _, github, _ = _flow(store, decision=GateDecision.ALLOW)
    get_pull_request = github.get_pull_request

    async def never_merged(pull_request):
        state = await get_pull_request(pull_request)
        return state.model_copy(update={"merged": False})

    monkeypatch.setattr(github, "get_pull_request", never_merged)
    record = await flow.start(issue_url=ISSUE_URL, project_id=PROJECT_ID, base_sha="a" * 40)

    assert record.status == "WORK_BLOCKED"
    events = await store.read_events(record.work_id, project_id=PROJECT_ID)
    observation = next(
        event for event in events if event.event_type is WorkEventType.OBSERVATION_RECORDED
    )
    assert observation.payload_json["detail"] == "GitHub did not report the pull request as merged"
    assert observation.payload_json["merged_sha"] is None
    blocked = next(
        event for event in reversed(events) if event.event_type is WorkEventType.WORK_BLOCKED
    )
    assert blocked.payload_json["merged_sha"] is None
    assert len(github.merges) == 1


@pytest.mark.asyncio
async def test_a_transient_failed_merge_read_back_is_absorbed(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store, decision=GateDecision.ALLOW)
    merge_pull_request = github.merge_pull_request

    async def merge_then_report_transient_false(pull_request, *, expected_head_sha):
        result = await merge_pull_request(
            pull_request,
            expected_head_sha=expected_head_sha,
        )
        github.readback_merged_once = False
        return result

    github.merge_pull_request = merge_then_report_transient_false

    record = await flow.start(
        issue_url=ISSUE_URL, project_id=PROJECT_ID, base_sha="a" * 40
    )

    assert record.status == "COMPLETE"
    events = await store.read_events(record.work_id, project_id=PROJECT_ID)
    observation = next(
        event for event in events if event.event_type is WorkEventType.OBSERVATION_RECORDED
    )
    assert observation.payload_json["passed"] is True
    assert len(github.merges) == 1
    assert len(github.pull_request_reads) >= 3
    assert len(github.comments) == 0


@pytest.mark.asyncio
async def test_a_failed_merge_read_back_blocks_for_a_human(
    store: WorkStore,
    monkeypatch,
) -> None:
    flow, _, github, _ = _flow(store, decision=GateDecision.ALLOW)
    get_pull_request = github.get_pull_request

    async def get_pull_request_without_sha(pull_request):
        state = await get_pull_request(pull_request)
        if github.merged_sha is None:
            return state
        return state.model_copy(update={"merged": True, "merge_commit_sha": None})

    monkeypatch.setattr(github, "get_pull_request", get_pull_request_without_sha)

    record = await flow.start(
        issue_url=ISSUE_URL, project_id=PROJECT_ID, base_sha="a" * 40
    )

    assert record.status == "WORK_BLOCKED"
    events = await store.read_events(record.work_id, project_id=PROJECT_ID)
    observation = next(
        event for event in events if event.event_type is WorkEventType.OBSERVATION_RECORDED
    )
    assert observation.payload_json["detail"] == (
        "GitHub did not report the merged commit SHA"
    )
    blocked = next(
        event for event in reversed(events) if event.event_type is WorkEventType.WORK_BLOCKED
    )
    assert blocked.payload_json["decision_request"] == (
        "GitHub did not report the merged commit SHA. "
        "Resolve the pull request on GitHub."
    )
    assert blocked.payload_json["merged_sha"] is None


@pytest.mark.asyncio
async def test_foreign_project_pull_request_cannot_prove_repository_outcome(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(
        store,
        decision=GateDecision.ALLOW,
        repository_outcome=SoftwareRepositoryOutcome.PULL_REQUEST,
    )
    github.remote_pull_request = GitHubPullRequest(
        project_id="project-b",
        owner=github.issue.owner,
        repo=github.issue.repo,
        number=7,
        url="https://github.com/octocat/hello-world/pull/7",
        head_sha="c" * 40,
        head="sagewai/foreign",
        base=github.issue.default_branch,
    )

    with pytest.raises(ValueError, match="different project"):
        await flow.start(
            issue_url=ISSUE_URL,
            project_id=PROJECT_ID,
            base_sha="a" * 40,
        )

    records = await store.list_work(project_id=PROJECT_ID)
    events = await store.read_events(records[0].work_id, project_id=PROJECT_ID)
    assert not any(
        event.event_type in {
            WorkEventType.VERIFICATION_RECORDED,
            WorkEventType.WORK_COMPLETED,
        }
        for event in events
    )
    assert not any(
        event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "pull_request"
        for event in events
    )


@pytest.mark.asyncio
async def test_pull_request_head_sha_must_match_published_commit(
    store: WorkStore,
    monkeypatch,
) -> None:
    flow, _, github, _ = _flow(
        store,
        decision=GateDecision.ALLOW,
        repository_outcome=SoftwareRepositoryOutcome.PULL_REQUEST,
    )

    async def mismatched_pull_request(*, issue, head, base):
        return GitHubPullRequest(
            project_id=PROJECT_ID,
            owner=issue.owner,
            repo=issue.repo,
            number=7,
            url="https://github.com/octocat/hello-world/pull/7",
            head=head,
            head_sha="c" * 40,
            base=base,
        )

    monkeypatch.setattr(github, "find_open_pull_request", mismatched_pull_request)

    with pytest.raises(ValueError, match="head SHA"):
        await flow.start(
            issue_url=ISSUE_URL,
            project_id=PROJECT_ID,
            base_sha="a" * 40,
        )

    records = await store.list_work(project_id=PROJECT_ID)
    events = await store.read_events(records[0].work_id, project_id=PROJECT_ID)
    assert not any(
        event.event_type in {
            WorkEventType.VERIFICATION_RECORDED,
            WorkEventType.WORK_COMPLETED,
        }
        for event in events
    )


@pytest.mark.asyncio
async def test_missing_completion_evidence_is_blocked_and_presented_once(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(
        store,
        decision=GateDecision.ALLOW,
        repository_outcome=SoftwareRepositoryOutcome.PULL_REQUEST,
        missing_criterion=True,
    )

    blocked = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )
    repeated = await flow.resume(blocked.work_id, project_id=PROJECT_ID)

    assert blocked.status == repeated.status == "WORK_BLOCKED"
    assert len(github.comments) == 1
    assert "blocked" in github.comments[0][1].lower()
    events = await store.read_events(blocked.work_id, project_id=PROJECT_ID)
    assert len(
        [event for event in events if event.event_type is WorkEventType.WORK_BLOCKED]
    ) == 1
    assert not any(
        event.event_type is WorkEventType.WORK_COMPLETED for event in events
    )



@pytest.mark.asyncio
async def test_delivery_triage_creates_new_reviewed_pr_and_merged_sha(
    store: WorkStore,
    monkeypatch,
) -> None:
    flow, software, github, publisher = _flow(store, delivery=True)
    gated = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )
    delivered = await flow.approve(
        gated.work_id,
        project_id=PROJECT_ID,
        gate_id=gated.pending_gate,
        actor_ref="operator:arda",
    )
    events = await store.read_events(delivered.work_id, project_id=PROJECT_ID)
    assert delivered.status == "READY_TO_DELIVER"
    assert all(
        event.event_type is not WorkEventType.WORK_COMPLETED for event in events
    )
    await store.append_event(
        WorkEvent(
            id="triage-1",
            project_id=PROJECT_ID,
            work_id=delivered.work_id,
            sequence=events[-1].sequence + 1,
            event_type=WorkEventType.TRIAGE_CREATED,
            actor_type="delivery_lifecycle",
            actor_ref="delivery_lifecycle",
            payload_json={
                "deployment_id": "deployment-1",
                "observation": {"verdict": "fail"},
                "summary": "Canary failed and rollback passed.",
                "evidence_refs": ["observation://failed", "rollback://passed"],
            },
            created_at=NOW,
        )
    )
    await store.save_work(delivered.model_copy(update={"status": "TRIAGING"}))

    async def repair_resume(work_id: str, *, project_id: str):
        software.resumes += 1
        repair_events = await store.read_events(work_id, project_id=project_id)
        await store.append_event(
            WorkEvent(
                id="repair-1",
                project_id=project_id,
                work_id=work_id,
                sequence=repair_events[-1].sequence + 1,
                event_type=WorkEventType.STAGE_COMPLETED,
                actor_type="software_lifecycle",
                actor_ref="operator:repairer",
                payload_json={
                    "stage": "repair",
                    "current_sha": "d" * 40,
                    "evidence_refs": ["repair://passed"],
                },
                created_at=NOW,
            )
        )
        current = await store.load_work(work_id, project_id=project_id)
        assert current is not None
        repaired = current.model_copy(update={"status": "READY_TO_MERGE"})
        await store.save_work(repaired)
        return repaired

    async def create_repair_pull_request(*, issue, title, head, base, body):
        github.pull_requests.append(
            {"issue": issue, "title": title, "head": head, "base": base, "body": body}
        )
        pull_request = GitHubPullRequest(
            project_id=issue.project_id,
            owner=issue.owner,
            repo=issue.repo,
            number=8,
            url="https://github.com/octocat/hello-world/pull/8",
            head=head,
            head_sha="b" * 40,
            base=base,
        )
        github.remote_pull_request = pull_request
        return pull_request

    async def merge_repair_pull_request(pull_request, *, expected_head_sha):
        github.merges.append(
            {"pull_request": pull_request, "expected_head_sha": expected_head_sha}
        )
        github.merged_sha = "e" * 40
        return GitHubMergeResult(
            project_id=pull_request.project_id,
            pull_request_number=pull_request.number,
            merged_sha=github.merged_sha,
        )

    monkeypatch.setattr(software, "resume", repair_resume)
    monkeypatch.setattr(github, "create_pull_request", create_repair_pull_request)
    monkeypatch.setattr(github, "merge_pull_request", merge_repair_pull_request)
    github.remote_pull_request = None
    github.merged_sha = None

    repair_gated = await flow.resume(delivered.work_id, project_id=PROJECT_ID)
    repaired_delivery = await flow.approve(
        repair_gated.work_id,
        project_id=PROJECT_ID,
        gate_id=repair_gated.pending_gate,
        actor_ref="operator:arda",
    )

    assert software.resumes == 1
    assert repair_gated.pending_gate == f"merge:{delivered.work_id}:8"
    assert repaired_delivery.status == "READY_TO_DELIVER"
    assert repaired_delivery.profile_context["github"]["pull_request_number"] == 8
    assert repaired_delivery.profile_context["github"]["merged_sha"] == "e" * 40
    assert len(publisher.calls) == 2
    assert publisher.calls[-1]["expected_sha"] == "d" * 40
    assert publisher.validations[-1] == (
        "octocat",
        "hello-world",
        "c" * 40,
        "main",
    )
    assert [item["pull_request"].number for item in github.merges] == [7, 8]
    events = await store.read_events(delivered.work_id, project_id=PROJECT_ID)
    repository_results = [
        VerificationResult.model_validate(event.payload_json)
        for event in events
        if event.event_type is WorkEventType.VERIFICATION_RECORDED
        and event.payload_json.get("stage") == "repository"
    ]
    assert [result.profile_context["result_sha"] for result in repository_results] == [
        "c" * 40,
        "e" * 40,
    ]
    assert not any(
        event.event_type is WorkEventType.WORK_COMPLETED for event in events
    )


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
async def test_resume_delegates_analyzing_work_before_loading_accepted_contract(
    store: WorkStore,
) -> None:
    flow, software, _github, publisher = _flow(store, pause_analysis=True)

    analyzing = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )

    assert analyzing.status == "ANALYZING"
    events = await store.read_events(analyzing.work_id, project_id=PROJECT_ID)
    assert not any(
        event.event_type is WorkEventType.CONTRACT_ACCEPTED for event in events
    )

    gated = await flow.resume(analyzing.work_id, project_id=PROJECT_ID)

    assert gated.status == "READY_TO_MERGE"
    assert gated.pending_gate == f"merge:{analyzing.work_id}:7"
    assert software.resumes == 1
    assert len(publisher.calls) == 1


@pytest.mark.asyncio
async def test_resume_recovers_pull_request_created_before_event_persistence(
    store: WorkStore,
) -> None:
    flow, software, github, publisher = _flow(
        store, repository_outcome=SoftwareRepositoryOutcome.PULL_REQUEST
    )
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

    flow = GitHubIssueLifecycle(
        work_store=store,
        software_lifecycle=software,
        github=github,
        branch_publisher=publisher,
        repository_outcome=SoftwareRepositoryOutcome.MERGED,
    )

    completed = await flow.resume(work_id, project_id=PROJECT_ID)

    assert completed.status == "COMPLETE"
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
    assert github.pull_request_reads == []
    assert github.merges == []
    assert not any(event.event_type is WorkEventType.GATE_REQUESTED for event in events)
    repository_events = [
        event
        for event in events
        if event.event_type is WorkEventType.VERIFICATION_RECORDED
        and event.payload_json.get("stage") == "repository"
    ]
    completion_events = [
        event for event in events if event.event_type is WorkEventType.WORK_COMPLETED
    ]
    assert len(repository_events) == len(completion_events) == 1
    repository_result = VerificationResult.model_validate(
        repository_events[0].payload_json
    )
    assert repository_result.project_id == PROJECT_ID
    assert repository_result.profile_context["repository_outcome"] == "pull_request"
    assert repository_result.profile_context["result_sha"] == "b" * 40

    repeated = await flow.resume(work_id, project_id=PROJECT_ID)
    repeated_events = await store.read_events(work_id, project_id=PROJECT_ID)
    assert repeated == completed
    assert len(repeated_events) == len(events)


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
    assert delivered.status == "COMPLETE"

    resumed = await flow.resume(delivered.work_id, project_id=PROJECT_ID)
    assert resumed.status == "COMPLETE"
    events = await store.read_events(delivered.work_id, project_id=PROJECT_ID)
    merges = [
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "merge"
    ]
    assert len(merges) == 1


@pytest.mark.asyncio
async def test_resume_after_completion_event_does_not_repeat_external_actions(
    store: WorkStore,
    monkeypatch,
) -> None:
    flow, software, github, publisher = _flow(
        store, decision=GateDecision.ALLOW
    )
    original_save = store.save_work
    failed = False

    async def fail_complete_projection(record):
        nonlocal failed
        if not failed and record.status == "COMPLETE":
            failed = True
            raise RuntimeError("completion projection interrupted")
        await original_save(record)

    monkeypatch.setattr(store, "save_work", fail_complete_projection)
    with pytest.raises(RuntimeError, match="completion projection interrupted"):
        await flow.start(
            issue_url=ISSUE_URL,
            project_id=PROJECT_ID,
            base_sha="a" * 40,
        )
    monkeypatch.setattr(store, "save_work", original_save)

    work_id = software.starts[0][0].id
    before = await store.read_events(work_id, project_id=PROJECT_ID)
    flow = GitHubIssueLifecycle(
        work_store=store,
        software_lifecycle=software,
        github=github,
        branch_publisher=publisher,
        merge_policy=lambda _request: GateDecision.ALLOW,
        repository_outcome=SoftwareRepositoryOutcome.MERGED,
    )
    completed = await flow.resume(work_id, project_id=PROJECT_ID)
    after = await store.read_events(work_id, project_id=PROJECT_ID)

    assert completed.status == "COMPLETE"
    assert len(github.pull_requests) == len(github.merges) == 1
    assert len(after) == len(before)
    assert len(
        [
            event
            for event in after
            if event.event_type is WorkEventType.VERIFICATION_RECORDED
            and event.payload_json.get("stage") == "repository"
        ]
    ) == 1
    assert len(
        [event for event in after if event.event_type is WorkEventType.WORK_COMPLETED]
    ) == 1



@pytest.mark.asyncio
async def test_resume_after_delivery_ready_evidence_does_not_repeat_actions(
    store: WorkStore,
    monkeypatch,
) -> None:
    flow, software, github, publisher = _flow(
        store,
        decision=GateDecision.ALLOW,
        delivery=True,
    )
    original_save = store.save_work
    failed = False

    async def fail_ready_projection(record):
        nonlocal failed
        if not failed and record.status == "READY_TO_DELIVER":
            failed = True
            raise RuntimeError("delivery-ready projection interrupted")
        await original_save(record)

    monkeypatch.setattr(store, "save_work", fail_ready_projection)
    with pytest.raises(RuntimeError, match="delivery-ready projection interrupted"):
        await flow.start(
            issue_url=ISSUE_URL,
            project_id=PROJECT_ID,
            base_sha="a" * 40,
        )
    monkeypatch.setattr(store, "save_work", original_save)

    work_id = software.starts[0][0].id
    before = await store.read_events(work_id, project_id=PROJECT_ID)
    flow = GitHubIssueLifecycle(
        work_store=store,
        software_lifecycle=software,
        github=github,
        branch_publisher=publisher,
        merge_policy=lambda _request: GateDecision.ALLOW,
        repository_outcome=SoftwareRepositoryOutcome.MERGED,
    )
    ready = await flow.resume(work_id, project_id=PROJECT_ID)
    after = await store.read_events(work_id, project_id=PROJECT_ID)

    assert ready.status == "READY_TO_DELIVER"
    assert len(github.pull_requests) == len(github.merges) == 1
    assert len(after) == len(before)
    assert len(
        [
            event
            for event in after
            if event.event_type is WorkEventType.VERIFICATION_RECORDED
            and event.payload_json.get("stage") == "repository"
        ]
    ) == 1
    assert not any(
        event.event_type is WorkEventType.WORK_COMPLETED for event in after
    )



@pytest.mark.parametrize(
    "delivery_status",
    (
        "READY_TO_DELIVER",
        "RELEASING",
        "STAGING",
        "PRODUCTION_CANARY",
        "PRODUCTION_ROLLOUT",
        "SOAKING",
        "ROLLING_BACK",
    ),
)
@pytest.mark.asyncio
async def test_delivery_phase_resume_does_not_reenter_software_lifecycle(
    store: WorkStore,
    delivery_status: str,
) -> None:
    flow, software, _github, _publisher = _flow(store, delivery=True)
    gated = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )
    delivered = await flow.approve(
        gated.work_id,
        project_id=PROJECT_ID,
        gate_id=gated.pending_gate,
        actor_ref="operator:arda",
    )
    await store.save_work(delivered.model_copy(update={"status": delivery_status}))

    resumed = await flow.resume(delivered.work_id, project_id=PROJECT_ID)

    assert resumed.status == delivery_status
    assert software.resumes == 0


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

    assert delivered.status == "COMPLETE"
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
async def test_critical_production_incident_freezes_approved_merge(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store, delivery=True)
    gated = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )
    assert gated.pending_gate is not None
    events = await store.read_events(gated.work_id, project_id=PROJECT_ID)
    await store.append_event(
        WorkEvent(
            id="production-deployment",
            project_id=PROJECT_ID,
            work_id=gated.work_id,
            sequence=events[-1].sequence + 1,
            event_type=WorkEventType.DEPLOYMENT_RECORDED,
            actor_type="test",
            actor_ref=None,
            payload_json={
                "deployment": {
                    "id": "production-1",
                    "environment": "production",
                }
            },
            created_at=NOW,
        )
    )
    await store.append_event(
        WorkEvent(
            id="rollback-refused",
            project_id=PROJECT_ID,
            work_id=gated.work_id,
            sequence=events[-1].sequence + 2,
            event_type=WorkEventType.CONTROL_DEGRADED,
            actor_type="delivery_control",
            actor_ref="delivery_control",
            payload_json={
                "severity": "critical",
                "action": "rollback",
                "deployment_id": "production-1",
                "failed_preconditions": ["rollback-authority"],
                "details": "rollback credential expired",
                "evidence_refs": ["check://rollback-authority"],
                "frozen_action_ids": ["rollback"],
            },
            created_at=NOW,
        )
    )
    await store.append_event(
        _external_incident_event(
            work_id=gated.work_id,
            sequence=events[-1].sequence + 3,
            deployment_id="production-1",
            severity="critical",
            evidence_refs=("check://rollback-authority",),
            active_control_event_ids=("rollback-refused",),
            cause=(
                "failed preconditions: rollback-authority; "
                "details: rollback credential expired"
            ),
        )
    )


    frozen = await flow.approve(
        gated.work_id,
        project_id=PROJECT_ID,
        gate_id=gated.pending_gate,
        actor_ref="operator:arda",
    )

    assert frozen.status == "MERGING"
    assert github.merges == []
    incident_comments = [
        body for _, body in github.comments if "production incident" in body
    ]
    assert len(incident_comments) == 1
    assert "CRITICAL" in incident_comments[0]

    events = await store.read_events(gated.work_id, project_id=PROJECT_ID)
    await store.append_event(
        WorkEvent(
            id="rollback-control-restored",
            project_id=PROJECT_ID,
            work_id=gated.work_id,
            sequence=events[-1].sequence + 1,
            event_type=WorkEventType.CONTROL_RESTORED,
            actor_type="delivery_control",
            actor_ref="delivery_control",
            payload_json={
                "precondition_ids": ["rollback-authority"],
                "evidence_refs": ["check://rollback-authority-restored"],
            },
            created_at=NOW,
        )
    )

    resumed = await flow.resume(gated.work_id, project_id=PROJECT_ID)

    assert resumed.status == "READY_TO_DELIVER"
    assert len(github.merges) == 1


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
async def test_target_movement_holds_work_and_resume_revalidates(
    store: WorkStore,
) -> None:
    flow, software, github, publisher = _flow(store)
    publisher.fail_phases = {"publish"}
    publisher.validation_error = ValueError(
        "local Git origin does not match issue repository"
    )

    held = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )

    assert held.status == "READY_TO_MERGE"
    assert held.pending_gate is None
    assert publisher.calls == []
    assert github.pull_requests == []
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert [item.kind.value for item in pending] == ["CONTROL_DEGRADED"]
    assert pending[0].attention_id == "github-target"
    assert pending[0].summary == "local Git origin does not match issue repository"
    events = await store.read_events(held.work_id, project_id=PROJECT_ID)
    degraded = [
        event for event in events if event.event_type is WorkEventType.CONTROL_DEGRADED
    ]
    assert len(degraded) == 1
    assert degraded[0].payload_json == {
        "failed_preconditions": ["github-target"],
        "evidence_refs": [ISSUE_URL],
        "details": "local Git origin does not match issue repository",
        "frozen_action_ids": [
            "publish_branch",
            "create_pull_request",
            "merge",
        ],
    }
    assert "frozen_actions" not in degraded[0].payload_json
    assert not any(event.event_type is WorkEventType.BASE_MOVED for event in events)

    publisher.fail_phases = set()
    publisher.validation_error = None
    resumed = await flow.resume(held.work_id, project_id=PROJECT_ID)

    assert resumed.status == "READY_TO_MERGE"
    assert resumed.pending_gate == f"merge:{held.work_id}:7"
    assert len(software.starts) == 1
    events = await store.read_events(held.work_id, project_id=PROJECT_ID)
    restored = [
        event for event in events if event.event_type is WorkEventType.CONTROL_RESTORED
    ]
    assert len(restored) == 1
    assert restored[0].payload_json == {
        "precondition_ids": ["github-target"],
        "evidence_refs": [ISSUE_URL],
    }
    assert len(publisher.calls) == 1
    assert len(github.pull_requests) == 1


@pytest.mark.asyncio
async def test_merge_response_sha_must_match_github_readback(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store, decision=GateDecision.ALLOW)
    github.readback_sha = "d" * 40

    record = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )

    assert record.status == "WORK_BLOCKED"
    assert len(github.merges) == 1
    events = await store.read_events(record.work_id, project_id=PROJECT_ID)
    observation = next(
        event for event in events if event.event_type is WorkEventType.OBSERVATION_RECORDED
    )
    assert (
        observation.payload_json["detail"]
        == "merge response SHA conflicts with GitHub read-back"
    )
    blocked = next(
        event for event in reversed(events) if event.event_type is WorkEventType.WORK_BLOCKED
    )
    assert blocked.payload_json["reason"] == "merge_post_check_failed"
    assert blocked.payload_json["merged_sha"] == "d" * 40
    assert "revert_pull_request" in blocked.payload_json["decision_request"]


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

    assert delivered.status == "COMPLETE"
    assert len(github.merges) == 1
    events = await store.read_events(delivered.work_id, project_id=PROJECT_ID)
    assert all(event.event_type is not WorkEventType.GATE_REQUESTED for event in events)
    decision = next(event for event in events if event.event_type is WorkEventType.GATE_DECIDED)
    assert decision.payload_json["decision"] == "allow"


@pytest.mark.parametrize(
    ("results", "error"),
    (
        (
            ((1, "", "missing origin"),),
            "cannot read Git origin",
        ),
        (
            ((0, "https://gitlab.com/octocat/hello-world.git\n", ""),),
            "Git origin is not a GitHub repository",
        ),
        (
            (
                (0, "git@github.com:octocat/hello-world.git\n", ""),
                (1, "", "network unavailable"),
            ),
            "cannot read GitHub default branch",
        ),
        (
            (
                (0, "git@github.com:octocat/hello-world.git\n", ""),
                (0, "", ""),
            ),
            "returned no commit",
        ),
    ),
)
@pytest.mark.asyncio
async def test_branch_publisher_surfaces_target_read_failures(
    results,
    error,
    monkeypatch,
    tmp_path,
) -> None:
    responses = iter(results)

    async def fake_subprocess(**_kwargs):
        returncode, stdout, stderr = next(responses)
        return type(
            "Result",
            (),
            {
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
            },
        )()

    monkeypatch.setattr(
        "sagewai.work.profiles.software.github.run_worker_subprocess",
        fake_subprocess,
    )
    publisher = WorktreeBranchPublisher(
        worktree_manager=object(),
        repository=tmp_path,
    )

    with pytest.raises(ValueError, match=error):
        await publisher.validate_target(
            owner="octocat",
            repo="hello-world",
            base_sha="a" * 40,
            default_branch="main",
        )


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

    with pytest.raises(
        BaseMovedError,
        match="requested base does not match GitHub default branch",
    ) as exc_info:
        await publisher.validate_target(
            owner="octocat",
            repo="hello-world",
            base_sha="a" * 40,
            default_branch="main",
        )
    assert exc_info.value.expected == "a" * 40
    assert exc_info.value.found == "b" * 40


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

    assert delivered.status == "COMPLETE"
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
    repository_events = [
        event
        for event in events
        if event.event_type is WorkEventType.VERIFICATION_RECORDED
        and event.payload_json.get("stage") == "repository"
    ]
    completion_events = [
        event for event in events if event.event_type is WorkEventType.WORK_COMPLETED
    ]
    assert len(repository_events) == len(completion_events) == 1
    assert merge_events[0].payload_json["merged_sha"] == "c" * 40


@pytest.mark.asyncio
async def test_canonical_merge_event_without_a_merge_raises(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store, decision=GateDecision.ALLOW)
    delivered = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )
    await store.save_work(delivered.model_copy(update={"status": "MERGING"}))
    github.merged_sha = None

    with pytest.raises(RuntimeError, match="canonical merge event conflicts"):
        await flow.resume(delivered.work_id, project_id=PROJECT_ID)

    assert len(github.merges) == 1


@pytest.mark.asyncio
async def test_canonical_merged_sha_conflict_blocks(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store, decision=GateDecision.ALLOW)
    delivered = await flow.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="a" * 40,
    )
    await store.save_work(delivered.model_copy(update={"status": "MERGING"}))
    github.merged_sha = "d" * 40

    record = await flow.resume(delivered.work_id, project_id=PROJECT_ID)

    assert record.status == "WORK_BLOCKED"
    events = await store.read_events(record.work_id, project_id=PROJECT_ID)
    observation = next(
        event
        for event in events
        if event.event_type is WorkEventType.OBSERVATION_RECORDED
        and event.payload_json["passed"] is False
    )
    assert observation.payload_json["detail"] == (
        "canonical merged SHA conflicts with GitHub state"
    )
    blocked = next(
        event
        for event in reversed(events)
        if event.event_type is WorkEventType.WORK_BLOCKED
    )
    assert blocked.payload_json["reason"] == "merge_post_check_failed"

    assert len(github.merges) == 1


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
async def test_external_outcome_updates_present_once_per_severity(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store)
    await store.save_work(
        WorkRecord(
            work_id="work-1",
            project_id=PROJECT_ID,
            source_ref=ISSUE_URL,
            profile="software",
            status="ROLLING_BACK",
            contract_version=1,
            active_run_id=None,
            pending_gate=None,
            profile_context={"base_sha": "a" * 40},
            created_at=NOW,
            updated_at=NOW,
        )
    )
    await store.append_event(
        _external_incident_event(
            work_id="work-1",
            sequence=1,
            deployment_id="production-1",
            severity="high",
            evidence_refs=("metrics://production-fail",),
        )
    )
    await flow.present_pending("work-1", project_id=PROJECT_ID)

    await store.append_event(
        _external_incident_event(
            work_id="work-1",
            sequence=3,
            deployment_id="production-1",
            severity="high",
            evidence_refs=("provider://rollback-1",),
        )
    )
    await flow.present_pending("work-1", project_id=PROJECT_ID)

    assert github.comments == [
        (
            ISSUE_URL,
            "Sagewai: production incident — HIGH: production incident for "
            "deployment production-1. Evidence: metrics://production-fail.",
        )
    ]

    await store.append_event(
        WorkEvent(
            id="rollback-refused",
            project_id=PROJECT_ID,
            work_id="work-1",
            sequence=4,
            event_type=WorkEventType.CONTROL_DEGRADED,
            actor_type="delivery_control",
            actor_ref="delivery_control",
            payload_json={
                "failed_preconditions": ["rollback-authority"],
                "details": "rollback credential expired",
                "evidence_refs": ["check://rollback-authority"],
                "frozen_action_ids": ["rollback"],
            },
            created_at=NOW + timedelta(seconds=1),
        )
    )
    await store.append_event(
        _external_incident_event(
            work_id="work-1",
            sequence=5,
            deployment_id="production-1",
            severity="critical",
            evidence_refs=("check://rollback-authority",),
            active_control_event_ids=("rollback-refused",),
            cause=(
                "failed preconditions: rollback-authority; "
                "details: rollback credential expired"
            ),
            created_at=NOW + timedelta(seconds=1),
        )
    )
    await flow.present_pending("work-1", project_id=PROJECT_ID)
    await flow.present_pending("work-1", project_id=PROJECT_ID)

    pending = [
        item
        for item in await store.pending_attention(project_id=PROJECT_ID)
        if item.kind is PendingAttentionKind.EXTERNAL_OUTCOME_INCIDENT
    ]
    assert len(pending) == 1
    assert pending[0].attention_id == "software-delivery:production-1"
    assert pending[0].created_at == NOW
    assert pending[0].severity == "critical"
    assert pending[0].evidence_refs == (
        "metrics://production-fail",
        "provider://rollback-1",
        "check://rollback-authority",
    )
    assert github.comments == [
        (
            ISSUE_URL,
            "Sagewai: production incident — HIGH: production incident for "
            "deployment production-1. Evidence: metrics://production-fail.",
        ),
        (
            ISSUE_URL,
            "Sagewai: production incident — CRITICAL: production incident for "
            "deployment production-1; failed preconditions: rollback-authority; "
            "details: rollback credential expired. Evidence: "
            "metrics://production-fail, provider://rollback-1, "
            "check://rollback-authority.",
        ),
    ]
    events = await store.read_events("work-1", project_id=PROJECT_ID)
    receipts = [
        event
        for event in events
        if event.event_type is WorkEventType.EXECUTION_RECORDED
        and event.payload_json.get("action") == "github_pending_attention_presented"
    ]
    assert len(receipts) == 2
    assert [event.payload_json["severity"] for event in receipts] == [
        "high",
        "critical",
    ]

@pytest.mark.asyncio
async def test_refused_production_rollback_presents_one_critical_incident_comment(
    store: WorkStore,
) -> None:
    flow, _, github, _ = _flow(store)
    await store.save_work(
        WorkRecord(
            work_id="work-1",
            project_id=PROJECT_ID,
            source_ref=ISSUE_URL,
            profile="software",
            status="CONTROL_DEGRADED",
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
            id="rollback-refused",
            project_id=PROJECT_ID,
            work_id="work-1",
            sequence=1,
            event_type=WorkEventType.CONTROL_DEGRADED,
            actor_type="delivery_control",
            actor_ref="delivery_control",
            payload_json={
                "failed_preconditions": [
                    "rollback-authority",
                    "rollback-observability",
                ],
                "details": "rollback control failed",
                "evidence_refs": ["check://rollback-control"],
                "frozen_action_ids": ["rollback"],
            },
            created_at=NOW,
        )
    )
    await store.append_event(
        _external_incident_event(
            work_id="work-1",
            sequence=2,
            deployment_id="production-1",
            severity="critical",
            evidence_refs=("check://rollback-control",),
            active_control_event_ids=("rollback-refused",),
            cause=(
                "failed preconditions: rollback-authority, rollback-observability; "
                "details: rollback control failed"
            ),
        )
    )

    await flow.present_pending("work-1", project_id=PROJECT_ID)
    await flow.present_pending("work-1", project_id=PROJECT_ID)

    assert github.comments == [
        (
            ISSUE_URL,
            "Sagewai: production incident — CRITICAL: production incident for "
            "deployment production-1; failed preconditions: rollback-authority, "
            "rollback-observability; details: rollback control failed. Evidence: "
            "check://rollback-control.",
        )
    ]
    events = await store.read_events("work-1", project_id=PROJECT_ID)
    receipts = [
        event
        for event in events
        if event.event_type is WorkEventType.EXECUTION_RECORDED
        and event.payload_json.get("action") == "github_pending_attention_presented"
    ]
    assert len(receipts) == 1
    assert (
        receipts[0].payload_json["attention_id"]
        == "software-delivery:production-1"
    )
    assert receipts[0].payload_json["kind"] == "EXTERNAL_OUTCOME_INCIDENT"

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
        head_sha="e" * 40,
        url="https://github.com/octocat/hello-world/pull/7",
        head="sagewai/work-1",
        base="main",
    )

    with pytest.raises(GitHubMergeRejectedError, match="Head branch was modified"):
        await client.merge_pull_request(pull_request, expected_head_sha="e" * 40)


@pytest.mark.asyncio
async def test_catalog_client_rejects_pull_request_search_identity_mismatch() -> None:
    async def github_callable(_payload):
        return [
            {
                "number": 7,
                "html_url": "https://github.com/octocat/hello-world/pull/7",
                "head": {"ref": "unrelated"},
                "base": {"ref": "main"},
            }
        ]

    client = CatalogGitHubClient(
        project_id=PROJECT_ID,
        github_callable=github_callable,
    )
    issue = GitHubIssue(
        project_id=PROJECT_ID,
        owner="octocat",
        repo="hello-world",
        number=42,
        url=ISSUE_URL,
        title="Fix target",
        body="Acceptance",
        default_branch="main",
    )

    with pytest.raises(ValueError, match="does not match requested head/base"):
        await client.find_open_pull_request(
            issue=issue,
            head="sagewai/work-1",
            base="main",
        )


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
                    "head": {"ref": "sagewai/work-1", "sha": "e" * 40},
                    "base": {"ref": "main"},
                }
            ]
        if operation == "create_pull_request":
            return {
                "number": 7,
                "html_url": "https://github.com/octocat/hello-world/pull/7",
                "head": {"sha": "e" * 40},
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
    assert found.head_sha == "e" * 40
    assert calls[2]["head"] == "octocat:sagewai/work-1"
    assert pull_request.number == 7
    assert pull_request.head_sha == "e" * 40
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


@pytest.mark.asyncio
async def test_catalog_client_identifies_and_deletes_comments() -> None:
    calls: list[dict] = []

    async def call(payload: dict):
        calls.append(payload)
        if payload["_operation"] == "create_comment":
            return {
                "id": 991,
                "html_url": f"{ISSUE_URL}#issuecomment-991",
                "body": payload["body"],
            }
        assert payload["_operation"] == "delete_comment"
        return {}

    client = CatalogGitHubClient(project_id=PROJECT_ID, github_callable=call)

    comment = await client.comment_issue(ISSUE_URL, "Pending approval")
    await client.delete_comment(comment.url)

    assert (comment.project_id, comment.id, comment.body) == (PROJECT_ID, 991, "Pending approval")
    assert comment.url.endswith("#issuecomment-991")
    assert calls[-1] == {
        "_operation": "delete_comment",
        "owner": "octocat",
        "repo": "hello-world",
        "comment_id": 991,
    }


def test_pull_request_and_comment_urls_parse() -> None:
    assert parse_pull_request_url("https://github.com/octocat/hello-world/pull/7") == (
        "octocat",
        "hello-world",
        7,
    )
    assert parse_comment_url(f"{ISSUE_URL}#issuecomment-991") == ("octocat", "hello-world", 991)
    assert is_github_comment_url(ISSUE_URL) is False
    assert is_github_comment_url(f"{ISSUE_URL}#issuecomment-991") is True
    with pytest.raises(ValueError):
        parse_pull_request_url(ISSUE_URL)


@pytest.mark.asyncio
async def test_catalog_client_lists_only_open_labeled_issues() -> None:
    calls = []

    async def github_callable(payload):
        calls.append(dict(payload))
        if payload["_operation"] == "get_repo":
            return {"default_branch": "main"}
        if payload["_operation"] == "list_issues":
            return [
                {
                    "number": 7,
                    "html_url": "https://github.com/octocat/hello-world/pull/7",
                    "title": "Pull request",
                    "body": None,
                    "pull_request": {"url": "https://api.github.com/pulls/7"},
                },
                {
                    "number": 42,
                    "html_url": ISSUE_URL,
                    "title": "Fix target",
                    "body": "Acceptance",
                },
            ]
        raise AssertionError(payload["_operation"])

    client = CatalogGitHubClient(
        project_id=PROJECT_ID,
        github_callable=github_callable,
    )

    issues = await client.list_labeled_issues(
        owner="octocat",
        repo="hello-world",
        label="sagewai",
    )

    assert [issue.number for issue in issues] == [42]
    assert issues[0].default_branch == "main"
    assert calls[1] == {
        "_operation": "list_issues",
        "owner": "octocat",
        "repo": "hello-world",
        "labels": "sagewai",
        "state": "open",
        "sort": "created",
        "direction": "asc",
        "per_page": 100,
    }
