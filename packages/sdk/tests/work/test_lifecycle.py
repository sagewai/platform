# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Acceptance tests for the first deterministic software Work lifecycle."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sagewai.core.state import InMemoryStore
from sagewai.safety.permissions import PermissionPolicy
from sagewai.work import (
    ActionResult,
    Assumption,
    CapabilityGrant,
    CapabilitySet,
    OperatorDisciplineReport,
    OperatorResult,
    ReviewFinding,
    ReviewResult,
    TaskCapsuleCompiler,
    WorkContract,
    WorkEvent,
    WorkEventType,
    WorkItem,
    WorkStore,
)
from sagewai.work.control import OperatorController
from sagewai.work.knowledge import KnowledgeStore
from sagewai.work.profiles.software import (
    GitHubIssue,
    GitHubIssueLifecycle,
    GitHubMergeResult,
    GitHubPullRequest,
    GitHubPullRequestState,
    SoftwareContractContext,
    SoftwareLifecycle,
    SoftwareReadOnlyResultValidator,
    SoftwareRepairContext,
    SoftwareResultValidator,
    SoftwareReviewContext,
    SoftwareStageOperator,
    SoftwareVerifier,
    SoftwareWorktreeManager,
    WorkspaceStaleError,
)
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _git(repository: Path, *args: str) -> str:
    return subprocess.check_output(
        ("git", "-C", str(repository), *args),
        text=True,
    ).strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", "-q", str(repository)), check=True)
    subprocess.run(
        ("git", "-C", str(repository), "config", "user.email", "test@example.com"),
        check=True,
    )
    subprocess.run(
        ("git", "-C", str(repository), "config", "user.name", "Test"),
        check=True,
    )
    (repository / "AGENTS.md").write_text("Run deterministic verification.\n")
    (repository / "source.txt").write_text("base\n")
    subprocess.run(("git", "-C", str(repository), "add", "."), check=True)
    subprocess.run(
        ("git", "-C", str(repository), "commit", "-qm", "base"),
        check=True,
    )
    return repository, _git(repository, "rev-parse", "HEAD")


def _work_item() -> WorkItem:
    return WorkItem(
        id="work-1",
        project_id="project-a",
        profile="software",
        source="local",
        source_ref=None,
        title="Change target",
        description="Change target deterministically",
        created_at=NOW,
    )


def _contract(base_sha: str, *, assumption_ids: tuple[str, ...] = ()) -> WorkContract:
    return WorkContract(
        id="contract-1",
        project_id="project-a",
        work_id="work-1",
        version=1,
        goal="Change target deterministically",
        allowed_scope=("target.txt",),
        acceptance_criteria=("deterministic verification passes",),
        constraints=(),
        non_goals=(),
        evidence_refs=(),
        assumption_ids=assumption_ids,
        risk="low",
        design_required=False,
        profile_context=SoftwareContractContext(base_sha=base_sha).model_dump(mode="json"),
    )


def _write_capabilities() -> CapabilitySet:
    return CapabilitySet(
        project_id="project-a",
        grants=(
            CapabilityGrant(
                project_id="project-a",
                name="filesystem.write",
                kind="filesystem",
                scope={"roots": ["target.txt"]},
                permissions=("workspace.write",),
            ),
        ),
    )


def _read_capabilities() -> CapabilitySet:
    return CapabilitySet(
        project_id="project-a",
        grants=(
            CapabilityGrant(
                project_id="project-a",
                name="filesystem.read",
                kind="filesystem",
                scope={"roots": ["target.txt"]},
                permissions=("workspace.read",),
            ),
        ),
    )


def _operator_result(request, *, profile_context=None) -> OperatorResult:
    now = datetime.now(timezone.utc)
    return OperatorResult(
        project_id=request.project_id,
        work_id=request.work_id,
        run_id=request.run_id,
        status="passed",
        summary=f"{request.stage} completed",
        evidence_refs=(f"runtime://{request.run_id}",),
        artifact_refs=(),
        changes=(),
        verification=(),
        risks=(),
        action_results=tuple(
            ActionResult(
                project_id=request.project_id,
                action_id=intent.action_id,
                status="succeeded",
                external_ref=None,
                evidence_refs=(f"runtime://{request.run_id}",),
                started_at=now,
                completed_at=now,
            )
            for intent in request.action_intents
        ),
        profile_context=profile_context or {},
    )


class MutationRuntime:
    name = "mutation-runtime"

    def __init__(self, *, implement_text: str, repair_text: str) -> None:
        self.implement_text = implement_text
        self.repair_text = repair_text
        self.calls = 0
        self.capsules = []

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        text = self.implement_text if request.stage == "implement" else self.repair_text
        (workspace.path / "target.txt").write_text(f"{text}\n")
        return _operator_result(request)


class FailedMutationRuntime(MutationRuntime):
    async def run(self, request, capsule, capabilities, workspace):
        result = await super().run(request, capsule, capabilities, workspace)
        return result.model_copy(
            update={
                "status": "failed",
                "evidence_refs": ("runtime://implement-failure",),
            }
        )


class ReviewRuntime:
    name = "review-runtime"

    def __init__(self, *verdicts: str) -> None:
        self.verdicts = list(verdicts)
        self.calls = 0
        self.capsules = []

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        verdict = self.verdicts.pop(0) if self.verdicts else "accept"
        findings = ()
        if verdict == "repair":
            findings = (
                ReviewFinding(
                    severity="high",
                    claim="The target needs repair",
                    evidence_refs=tuple(capsule.prior_result_refs),
                    required_change="Write the repaired target",
                    profile_context={"file": "target.txt", "line": 1},
                ),
            )
        review = ReviewResult(
            attempt_id=request.run_id,
            verdict=verdict,
            findings=findings,
            evidence_refs=(f"review://{request.run_id}",),
        )
        return _operator_result(
            request,
            profile_context={"review_result": review.model_dump(mode="json")},
        )


class CrashVerifier:
    async def verify(self, **_kwargs):
        raise RuntimeError("simulated restart")


class PassingValidator:
    async def validate(self, *, request, result, workspace):
        return OperatorDisciplineReport(
            project_id=request.project_id,
            work_id=request.work_id,
            run_id=request.run_id,
            unsupported_claims=(),
            scope_violations=(),
            permission_violations=(),
            risk_mismatches=(),
            unnecessary_changes=(),
            output_tokens=None,
            changed_files=0,
            diff_lines=0,
            verdict="pass",
        )


def _controller(
    work_store: WorkStore,
    durability: InMemoryStore,
    validator,
) -> OperatorController:
    return OperatorController(
        work_store=work_store,
        durability_store=durability,
        permission_policy=PermissionPolicy(),
        control_checks={},
        result_validator=validator,
        heartbeat_interval=0.01,
    )


def _command(expected: str) -> str:
    return (
        f'{sys.executable} -c "from pathlib import Path; '
        f"assert Path('target.txt').read_text() == '{expected}\\n'\""
    )


def _always_pass_command() -> str:
    return f'{sys.executable} -c "raise SystemExit(0)"'


def _always_fail_command() -> str:
    return f'{sys.executable} -c "raise SystemExit(1)"'


def _lifecycle(
    *,
    repository: Path,
    worktree_root: Path,
    work_store: WorkStore,
    knowledge_store: KnowledgeStore,
    durability: InMemoryStore,
    implementer: MutationRuntime,
    reviewer: ReviewRuntime,
    repairer: MutationRuntime,
    commands: tuple[str, ...],
    verifier=None,
    implementer_actor: str = "operator:implementer",
    reviewer_actor: str = "operator:reviewer",
    repairer_actor: str | None = None,
) -> SoftwareLifecycle:
    compiler = TaskCapsuleCompiler(knowledge_store=knowledge_store)
    return SoftwareLifecycle(
        work_store=work_store,
        capsule_compiler=compiler,
        worktree_manager=SoftwareWorktreeManager(root=worktree_root),
        verifier=verifier or SoftwareVerifier(knowledge_store=knowledge_store),
        repository=repository,
        implementer=SoftwareStageOperator(
            actor_ref=implementer_actor,
            runtime=implementer,
            capabilities=_write_capabilities(),
            controller=_controller(
                work_store,
                durability,
                SoftwareResultValidator(),
            ),
        ),
        reviewer=SoftwareStageOperator(
            actor_ref=reviewer_actor,
            runtime=reviewer,
            capabilities=_read_capabilities(),
            controller=_controller(
                work_store,
                durability,
                SoftwareReadOnlyResultValidator(),
            ),
        ),
        repairer=SoftwareStageOperator(
            actor_ref=repairer_actor or implementer_actor,
            runtime=repairer,
            capabilities=_write_capabilities(),
            controller=_controller(
                work_store,
                durability,
                SoftwareResultValidator(),
            ),
        ),
        repo_instructions=("AGENTS.md",),
        verification_commands=commands,
    )


@pytest.fixture
async def stores(dialect_engine):  # noqa: F811
    work_store = WorkStore(engine=dialect_engine)
    knowledge_store = KnowledgeStore(engine=dialect_engine)
    await work_store.init()
    await knowledge_store.init()
    return work_store, knowledge_store


@pytest.mark.asyncio
async def test_successful_implement_verify_review_reaches_ready_to_merge(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    repairer = MutationRuntime(implement_text="unused", repair_text="fixed")
    reviewer = ReviewRuntime("accept")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=implementer,
        reviewer=reviewer,
        repairer=repairer,
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert record.status == "READY_TO_MERGE"
    assert record.status != "COMPLETE"
    assert implementer.calls == 1
    assert reviewer.calls == 1
    assert repairer.calls == 0

    capsule = reviewer.capsules[0]
    context = SoftwareReviewContext.model_validate(capsule.profile_context)
    assert context.verification.passed is True
    assert context.relevant_files == ("target.txt",)
    assert "initial" in context.diff
    assert tuple(item.id for item in capsule.knowledge_items) == (
        *context.verification.evidence_refs,
    )
    serialized = json.dumps(capsule.model_dump(mode="json")).lower()
    assert "session" not in serialized
    assert "chat_history" not in serialized


@pytest.mark.asyncio
async def test_github_flow_uses_real_software_lifecycle_events(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    software = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=MutationRuntime(implement_text="initial", repair_text="fixed"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    class GitHubFixture:
        def __init__(self) -> None:
            self.merged_sha = None
            self.expected_head_sha = None

        async def fetch_issue(self, issue_url):
            return GitHubIssue(
                project_id="project-a",
                owner="octocat",
                repo="hello-world",
                number=42,
                url=issue_url,
                title="Change target",
                body="deterministic verification passes",
                default_branch="main",
            )

        async def find_open_pull_request(self, *, issue, head, base):
            return None

        async def create_pull_request(self, *, issue, title, head, base, body):
            return GitHubPullRequest(
                project_id=issue.project_id,
                owner=issue.owner,
                repo=issue.repo,
                number=7,
                url="https://github.com/octocat/hello-world/pull/7",
                head=head,
                base=base,
            )

        async def get_pull_request(self, pull_request):
            return GitHubPullRequestState(
                project_id=pull_request.project_id,
                pull_request_number=pull_request.number,
                merged=self.merged_sha is not None,
                merge_commit_sha=self.merged_sha,
            )

        async def merge_pull_request(self, pull_request, *, expected_head_sha):
            self.expected_head_sha = expected_head_sha
            self.merged_sha = "c" * 40
            return GitHubMergeResult(
                project_id=pull_request.project_id,
                pull_request_number=pull_request.number,
                merged_sha=self.merged_sha,
            )

        async def comment_issue(self, issue_url, body):
            return None

    class PublisherFixture:
        def __init__(self) -> None:
            self.expected_sha = None

        async def validate_target(self, **_kwargs):
            return None

        async def publish(self, *, expected_sha, **_kwargs):
            self.expected_sha = expected_sha
            return "b" * 40

    github = GitHubFixture()
    publisher = PublisherFixture()
    flow = GitHubIssueLifecycle(
        work_store=work_store,
        software_lifecycle=software,
        github=github,
        branch_publisher=publisher,
    )

    gated = await flow.start(
        issue_url="https://github.com/octocat/hello-world/issues/42",
        project_id="project-a",
        base_sha=base_sha,
    )
    delivered = await flow.approve(
        gated.work_id,
        project_id="project-a",
        gate_id=gated.pending_gate,
        actor_ref="operator:arda",
    )

    assert gated.status == "READY_TO_MERGE"
    assert publisher.expected_sha == base_sha
    assert github.expected_head_sha == "b" * 40
    assert delivered.status == "READY_TO_DELIVER"
    events = await work_store.read_events(gated.work_id, project_id="project-a")
    assert any(
        event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "implement"
        for event in events
    )
    assert any(
        event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "merge"
        for event in events
    )


@pytest.mark.asyncio
async def test_failed_implementation_blocks_with_specific_question_and_evidence(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=FailedMutationRuntime(implement_text="failed", repair_text="unused"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert record.status == "WORK_BLOCKED"
    events = await work_store.read_events("work-1", project_id="project-a")
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert blocker.payload_json == {
        "reason": "implement_failed",
        "run_id": "work-1:implement:1",
        "decision_request": "Inspect the failed implementation evidence and decide whether to retry or stop the work.",
        "evidence_refs": ["runtime://implement-failure"],
    }


@pytest.mark.asyncio
async def test_blocked_review_carries_specific_operator_question_and_evidence(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=ReviewRuntime("blocked"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert record.status == "WORK_BLOCKED"
    events = await work_store.read_events("work-1", project_id="project-a")
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert blocker.payload_json == {
        "reason": "review_blocked",
        "run_id": "work-1:review:1",
        "decision_request": "Resolve the independent review blocker or stop the work.",
        "evidence_refs": ["review://work-1:review:1"],
    }


@pytest.mark.asyncio
async def test_verification_failure_never_reaches_review_and_cannot_be_overridden(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    implementer = MutationRuntime(implement_text="bad", repair_text="bad")
    repairer = MutationRuntime(implement_text="unused", repair_text="still-bad")
    reviewer = ReviewRuntime("accept")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=implementer,
        reviewer=reviewer,
        repairer=repairer,
        commands=(_always_fail_command(),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert record.status == "WORK_BLOCKED"
    assert reviewer.calls == 0
    assert repairer.calls == 2
    events = await work_store.read_events("work-1", project_id="project-a")
    blockers = [event for event in events if event.event_type is WorkEventType.WORK_BLOCKED]
    assert len(blockers) == 1
    assert blockers[0].payload_json["reason"] == "repair_budget_exhausted"


@pytest.mark.asyncio
async def test_review_finding_reaches_repair_as_typed_canonical_context(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    repairer = MutationRuntime(implement_text="unused", repair_text="fixed")
    reviewer = ReviewRuntime("repair", "accept")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=implementer,
        reviewer=reviewer,
        repairer=repairer,
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert record.status == "READY_TO_MERGE"
    assert reviewer.calls == 2
    assert repairer.calls == 1
    repair_context = SoftwareRepairContext.model_validate(repairer.capsules[0].profile_context)
    assert len(repair_context.findings) == 1
    assert repair_context.findings[0].required_change == "Write the repaired target"
    assert repair_context.open_assumptions == ()


@pytest.mark.asyncio
async def test_delivery_triage_resumes_repair_with_failed_observation_context(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    repairer = MutationRuntime(implement_text="unused", repair_text="fixed")
    reviewer = ReviewRuntime("accept", "accept")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=reviewer,
        repairer=repairer,
        commands=(_always_pass_command(),),
    )
    ready = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )
    workspace = tmp_path / "worktrees" / "project-a" / "work-1" / "workspace"
    subprocess.run(("git", "-C", str(workspace), "add", "--all"), check=True)
    subprocess.run(
        ("git", "-C", str(workspace), "commit", "-qm", "published change"),
        check=True,
    )
    branch_sha = _git(workspace, "rev-parse", "HEAD")
    events = await work_store.read_events("work-1", project_id="project-a")
    await work_store.append_event(
        WorkEvent(
            id="branch-publication-1",
            project_id="project-a",
            work_id="work-1",
            sequence=events[-1].sequence + 1,
            event_type=WorkEventType.STAGE_COMPLETED,
            actor_type="test",
            actor_ref="github",
            payload_json={
                "stage": "branch_published",
                "branch": "sagewai/work-1",
                "branch_sha": branch_sha,
            },
            created_at=NOW,
        )
    )
    events = await work_store.read_events("work-1", project_id="project-a")
    await work_store.append_event(
        WorkEvent(
            id="triage-1",
            project_id="project-a",
            work_id="work-1",
            sequence=events[-1].sequence + 1,
            event_type=WorkEventType.TRIAGE_CREATED,
            actor_type="delivery_lifecycle",
            actor_ref="delivery_lifecycle",
            payload_json={
                "deployment_id": "deployment-1",
                "observation": {
                    "verdict": "fail",
                    "evidence_refs": ["metrics://failed-canary"],
                },
                "summary": "Canary error rate exceeded the configured gate.",
                "evidence_refs": ["metrics://failed-canary", "rollback://deployment-1"],
            },
            created_at=NOW,
        )
    )
    await work_store.save_work(ready.model_copy(update={"status": "TRIAGE"}))

    repaired = await lifecycle.resume("work-1", project_id="project-a")

    assert repaired.status == "READY_TO_MERGE"
    assert repairer.calls == 1
    assert reviewer.calls == 2
    capsule = repairer.capsules[0]
    repair_context = SoftwareRepairContext.model_validate(capsule.profile_context)
    assert repair_context.triage is not None
    assert repair_context.triage.deployment_id == "deployment-1"
    assert repair_context.triage.summary == "Canary error rate exceeded the configured gate."
    assert repair_context.triage.observation["verdict"] == "fail"
    assert "metrics://failed-canary" in capsule.prior_result_refs
    assert "work-event://triage-1" in capsule.prior_result_refs


@pytest.mark.asyncio
async def test_review_repair_budget_emits_one_specific_work_blocked(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    repairer = MutationRuntime(implement_text="unused", repair_text="fixed")
    reviewer = ReviewRuntime("repair", "repair", "repair")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=implementer,
        reviewer=reviewer,
        repairer=repairer,
        commands=(_always_pass_command(),),
    )

    first = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )
    second = await lifecycle.resume("work-1", project_id="project-a")

    assert first.status == second.status == "WORK_BLOCKED"
    assert repairer.calls == 2
    assert reviewer.calls == 3
    events = await work_store.read_events("work-1", project_id="project-a")
    blockers = [event for event in events if event.event_type is WorkEventType.WORK_BLOCKED]
    assert len(blockers) == 1
    assert blockers[0].payload_json == {
        "reason": "repair_budget_exhausted",
        "repair_attempts": 2,
        "decision_request": "Revise the contract or stop the work.",
    }


@pytest.mark.asyncio
async def test_restart_resume_does_not_rerun_completed_implementation(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    first_implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    first = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=first_implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
        verifier=CrashVerifier(),
    )

    with pytest.raises(RuntimeError, match="simulated restart"):
        await first.start(
            work_item=_work_item(),
            contract=_contract(base_sha),
        )

    resumed_implementer = MutationRuntime(implement_text="must-not-run", repair_text="fixed")
    resumed_reviewer = ReviewRuntime("accept")
    resumed = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=resumed_implementer,
        reviewer=resumed_reviewer,
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    record = await resumed.resume("work-1", project_id="project-a")

    assert record.status == "READY_TO_MERGE"
    assert first_implementer.calls == 1
    assert resumed_implementer.calls == 0
    assert resumed_reviewer.calls == 1
    events = await work_store.read_events("work-1", project_id="project-a")
    completed = [
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json["stage"] == "implement"
    ]
    assert len(completed) == 1


@pytest.mark.asyncio
async def test_resume_refuses_unexpected_workspace_head_change(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=MutationRuntime(implement_text="initial", repair_text="fixed"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
        verifier=CrashVerifier(),
    )
    with pytest.raises(RuntimeError, match="simulated restart"):
        await lifecycle.start(
            work_item=_work_item(),
            contract=_contract(base_sha),
        )

    workspace = tmp_path / "worktrees" / "project-a" / "work-1" / "workspace"
    subprocess.run(("git", "-C", str(workspace), "add", "target.txt"), check=True)
    subprocess.run(
        ("git", "-C", str(workspace), "commit", "-qm", "unexpected"),
        check=True,
    )

    with pytest.raises(WorkspaceStaleError, match="workspace HEAD moved"):
        await lifecycle.resume("work-1", project_id="project-a")


@pytest.mark.asyncio
async def test_unsupported_compatibility_assumption_blocks_before_execution(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    implementer = MutationRuntime(implement_text="must-not-run", repair_text="fixed")
    reviewer = ReviewRuntime("accept")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=implementer,
        reviewer=reviewer,
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )
    assumption = Assumption(
        id="assumption-compat",
        statement="A fallback path is required",
        kind="compatibility",
        evidence_refs=(),
        confidence="low",
        impact_if_wrong="high",
        status="open",
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha, assumption_ids=(assumption.id,)),
        assumptions=(assumption,),
    )

    assert record.status == "WORK_BLOCKED"
    assert implementer.calls == reviewer.calls == 0
    events = await work_store.read_events("work-1", project_id="project-a")
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert blocker.payload_json == {
        "reason": "unsupported_assumption",
        "assumption_id": "assumption-compat",
        "decision_request": "Provide evidence or revise the contract.",
    }


@pytest.mark.asyncio
async def test_implementer_cannot_be_assigned_as_reviewer(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, _ = _repository(tmp_path)
    durability = InMemoryStore()
    runtime = MutationRuntime(implement_text="initial", repair_text="fixed")

    with pytest.raises(ValueError, match="reviewer cannot review"):
        _lifecycle(
            repository=repository,
            worktree_root=tmp_path / "worktrees",
            work_store=work_store,
            knowledge_store=knowledge_store,
            durability=durability,
            implementer=runtime,
            reviewer=ReviewRuntime("accept"),
            repairer=runtime,
            commands=(_always_pass_command(),),
            implementer_actor="operator:same",
            reviewer_actor="operator:same",
        )


@pytest.mark.asyncio
async def test_repairer_cannot_be_assigned_as_reviewer(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, _ = _repository(tmp_path)
    durability = InMemoryStore()

    with pytest.raises(ValueError, match="reviewer cannot review"):
        _lifecycle(
            repository=repository,
            worktree_root=tmp_path / "worktrees",
            work_store=work_store,
            knowledge_store=knowledge_store,
            durability=durability,
            implementer=MutationRuntime(
                implement_text="initial",
                repair_text="fixed",
            ),
            reviewer=ReviewRuntime("repair", "accept"),
            repairer=MutationRuntime(
                implement_text="unused",
                repair_text="fixed",
            ),
            commands=(_always_pass_command(),),
            reviewer_actor="operator:same",
            repairer_actor="operator:same",
        )
