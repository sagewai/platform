# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Verified local commit completion for the software Work lifecycle."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sagewai.core.state import InMemoryStore
from sagewai.work import (
    AcceptanceCriterion,
    CompletionEvaluation,
    ProposedAcceptanceCriterion,
    VerificationResult,
    WorkContract,
    WorkContractProposal,
    WorkEventType,
    WorkStore,
)
from sagewai.work.knowledge import KnowledgeStore
from sagewai.work.profiles.software import (
    SoftwareContractContext,
    SoftwareRepositoryOutcome,
    SoftwareVerifier,
)
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.fakes_verification import LocalVerificationRunner
from tests.work.test_lifecycle import (
    AnalysisRuntime,
    MutationRuntime,
    ReviewRuntime,
    _always_pass_command,
    _command,
    _git,
    _lifecycle,
    _repository,
    _work_item,
)


def _contract(base_sha: str, outcome: SoftwareRepositoryOutcome) -> WorkContract:
    repository_criterion = AcceptanceCriterion(
        id="criterion-repository",
        project_id="project-a",
        statement="produce the accepted repository outcome",
        verification_kind="profile",
    )
    return WorkContract(
        id="contract-1",
        project_id="project-a",
        work_id="work-1",
        version=1,
        goal="Change target deterministically",
        allowed_scope=("target.txt",),
        acceptance_criteria=(repository_criterion,),
        constraints=(),
        non_goals=(),
        evidence_refs=(),
        assumption_ids=(),
        risk="low",
        design_required=False,
        profile_context=SoftwareContractContext(
            project_id="project-a",
            base_sha=base_sha,
            repository_outcome=outcome,
            repository_criterion_id=repository_criterion.id,
        ).model_dump(mode="json"),
    )


class CountingVerifier:
    def __init__(self, delegate: SoftwareVerifier) -> None:
        self.delegate = delegate
        self.calls = 0
        self.criterion_ids: list[tuple[str, ...]] = []

    async def verify(self, **kwargs) -> VerificationResult:
        self.criterion_ids.append(tuple(kwargs["criterion_ids"]))
        self.calls += 1
        return await self.delegate.verify(**kwargs)


class CrashAfterCommitManager:
    """Persist the commit, then simulate death before its Work event."""

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.crashed = False

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def commit_reviewed(self, *args, **kwargs) -> str:
        result_sha = await self.delegate.commit_reviewed(*args, **kwargs)
        if not self.crashed:
            self.crashed = True
            raise RuntimeError("simulated death after local commit")
        return result_sha


class MoveHeadBeforeCommitManager:
    """Move HEAD immediately before the reviewed commit is applied."""

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def commit_reviewed(self, workspace, **kwargs) -> str:
        self.calls += 1
        subprocess.run(
            ("git", "-C", str(workspace.path), "add", "--all"),
            check=True,
        )
        subprocess.run(
            (
                "git",
                "-C",
                str(workspace.path),
                "commit",
                "-qm",
                "unexpected completion movement",
            ),
            check=True,
        )
        return await self.delegate.commit_reviewed(workspace, **kwargs)


class MutateReviewedOutputBeforeCommitManager:
    """Rewrite reviewed content without moving HEAD before the commit boundary."""

    def __init__(self, delegate, *, mutation: str) -> None:
        self.delegate = delegate
        self.mutation = mutation
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def commit_reviewed(self, workspace, **kwargs) -> str:
        self.calls += 1
        target = workspace.path / "target.txt"
        if self.mutation == "rewrite":
            target.write_text("not reviewed\n")
        elif self.mutation == "append_tail":
            content = target.read_text()
            target.write_text(f"{content[:-2]}y\n")
        else:
            target.unlink()
        return await self.delegate.commit_reviewed(workspace, **kwargs)


@pytest.fixture
async def completion_stores(dialect_engine):  # noqa: F811
    work_store = WorkStore(engine=dialect_engine)
    knowledge_store = KnowledgeStore(engine=dialect_engine)
    await work_store.init()
    await knowledge_store.init()
    return work_store, knowledge_store


@pytest.mark.asyncio
async def test_verified_commit_completes_with_exact_sha_and_resumes_without_reruns(
    completion_stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = completion_stores
    repository, base_sha = _repository(tmp_path)
    analyzer = AnalysisRuntime()
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    reviewer = ReviewRuntime("accept")
    verifier = CountingVerifier(
        SoftwareVerifier(knowledge_store=knowledge_store, runner=LocalVerificationRunner())
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=analyzer,
        implementer=implementer,
        reviewer=reviewer,
        repairer=implementer,
        verifier=verifier,
        commands=(_command("initial"),),
    )
    draft = _contract(base_sha, SoftwareRepositoryOutcome.VERIFIED_COMMIT)

    completed = await lifecycle.start(work_item=_work_item(), contract=draft)

    workspace = tmp_path / "worktrees/project-a/work-1/workspace"
    result_sha = _git(workspace, "rev-parse", "HEAD")
    assert completed.status == "COMPLETE"
    assert result_sha != base_sha
    assert _git(repository, "rev-parse", "HEAD") == base_sha
    assert _git(workspace, "status", "--porcelain") == ""

    events = await work_store.read_events("work-1", project_id="project-a")
    accepted_event = next(
        event for event in events if event.event_type is WorkEventType.CONTRACT_ACCEPTED
    )
    accepted = WorkContract.model_validate(accepted_event.payload_json)
    assert accepted.acceptance_criteria[0] == draft.acceptance_criteria[0]
    assert accepted.acceptance_criteria[1].id == "contract-1:analysis:criterion:1"
    assert accepted.acceptance_criteria[1].statement == "deterministic verification passes"

    repository_result = next(
        VerificationResult.model_validate(event.payload_json)
        for event in events
        if event.event_type is WorkEventType.VERIFICATION_RECORDED
        and event.payload_json.get("stage") == "repository"
    )
    assert repository_result.contract_id == accepted.id
    assert repository_result.attempt_id == "work-1:repository:1"
    assert repository_result.passed is True
    assert repository_result.criterion_results[0].criterion_id == "criterion-repository"
    assert repository_result.evidence_refs == (f"git://{result_sha}",)
    assert repository_result.profile_context == {
        "base_sha": base_sha,
        "result_sha": result_sha,
    }

    completed_events = [
        event for event in events if event.event_type is WorkEventType.WORK_COMPLETED
    ]
    assert len(completed_events) == 1
    evaluation = CompletionEvaluation.model_validate(completed_events[0].payload_json)
    assert evaluation.passed is True
    assert evaluation.contract_id == accepted.id
    assert tuple(item.criterion_id for item in evaluation.criterion_results) == tuple(
        item.id for item in accepted.acceptance_criteria
    )

    calls = (analyzer.calls, implementer.calls, verifier.calls, reviewer.calls)
    resumed = await lifecycle.resume("work-1", project_id="project-a")
    assert resumed.status == "COMPLETE"
    assert (analyzer.calls, implementer.calls, verifier.calls, reviewer.calls) == calls
    resumed_events = await work_store.read_events("work-1", project_id="project-a")
    assert sum(
        event.event_type is WorkEventType.WORK_COMPLETED for event in resumed_events
    ) == 1


@pytest.mark.asyncio
async def test_mixed_criterion_kinds_use_exact_issuers_and_missing_policy_blocks_completion(
    completion_stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = completion_stores
    repository, base_sha = _repository(tmp_path)
    analyzer = AnalysisRuntime(
        proposal=WorkContractProposal(
            goal="Change target under explicit policy",
            allowed_scope=("target.txt",),
            acceptance_criteria=(
                ProposedAcceptanceCriterion(
                    statement="verification commands pass",
                    verification_kind="deterministic",
                ),
                ProposedAcceptanceCriterion(
                    statement="profile action succeeds",
                    verification_kind="profile",
                ),
                ProposedAcceptanceCriterion(
                    statement="policy authorizes completion",
                    verification_kind="policy",
                ),
            ),
            constraints=(),
            non_goals=(),
            risk="low",
            design_required=False,
        )
    )
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    reviewer = ReviewRuntime("accept")
    verifier = CountingVerifier(SoftwareVerifier(knowledge_store=knowledge_store, runner=LocalVerificationRunner()))
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=analyzer,
        implementer=implementer,
        reviewer=reviewer,
        repairer=implementer,
        verifier=verifier,
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha, SoftwareRepositoryOutcome.VERIFIED_COMMIT),
    )

    assert record.status == "WORK_BLOCKED"
    events = await work_store.read_events("work-1", project_id="project-a")
    accepted = WorkContract.model_validate(
        next(
            event.payload_json
            for event in events
            if event.event_type is WorkEventType.CONTRACT_ACCEPTED
        )
    )
    deterministic_id, profile_id, policy_id = (
        criterion.id for criterion in accepted.acceptance_criteria[1:]
    )
    assert verifier.criterion_ids == [(deterministic_id,)]

    completed_mutation = next(
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "implement"
    )
    profile_result = VerificationResult.model_validate(
        completed_mutation.payload_json["profile_verification"]
    )
    assert tuple(item.criterion_id for item in profile_result.criterion_results) == (
        profile_id,
    )

    stage_result = next(
        VerificationResult.model_validate(event.payload_json)
        for event in events
        if event.event_type is WorkEventType.VERIFICATION_RECORDED
        and event.payload_json.get("stage") == "verification"
    )
    assert tuple(item.criterion_id for item in stage_result.criterion_results) == (
        deterministic_id,
        profile_id,
    )
    assert policy_id not in {item.criterion_id for item in stage_result.criterion_results}
    assert reviewer.calls == 1
    blocker = next(
        event for event in events if event.event_type is WorkEventType.WORK_BLOCKED
    )
    assert blocker.payload_json["reason"] == "completion_evidence_invalid"
    assert blocker.payload_json["violations"] == [f"missing criterion ids: {policy_id}"]
    assert not any(
        event.event_type is WorkEventType.WORK_COMPLETED for event in events
    )


@pytest.mark.asyncio
async def test_resume_after_commit_before_repository_event_does_not_rerun_stages(
    completion_stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = completion_stores
    repository, base_sha = _repository(tmp_path)
    first_analyzer = AnalysisRuntime()
    first_implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    first_reviewer = ReviewRuntime("accept")
    first_verifier = CountingVerifier(
        SoftwareVerifier(knowledge_store=knowledge_store, runner=LocalVerificationRunner())
    )
    first = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=first_analyzer,
        implementer=first_implementer,
        reviewer=first_reviewer,
        repairer=first_implementer,
        verifier=first_verifier,
        commands=(_command("initial"),),
    )
    first._worktree_manager = CrashAfterCommitManager(  # noqa: SLF001
        first._worktree_manager  # noqa: SLF001
    )

    with pytest.raises(RuntimeError, match="death after local commit"):
        await first.start(
            work_item=_work_item(),
            contract=_contract(base_sha, SoftwareRepositoryOutcome.VERIFIED_COMMIT),
        )

    workspace = tmp_path / "worktrees/project-a/work-1/workspace"
    committed_sha = _git(workspace, "rev-parse", "HEAD")
    assert committed_sha != base_sha
    interrupted_events = await work_store.read_events("work-1", project_id="project-a")
    assert not any(
        event.event_type is WorkEventType.VERIFICATION_RECORDED
        and event.payload_json.get("stage") == "repository"
        for event in interrupted_events
    )

    resumed_analyzer = AnalysisRuntime()
    resumed_implementer = MutationRuntime(implement_text="wrong", repair_text="wrong")
    resumed_reviewer = ReviewRuntime("accept")
    resumed_verifier = CountingVerifier(
        SoftwareVerifier(knowledge_store=knowledge_store, runner=LocalVerificationRunner())
    )
    resumed_lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=resumed_analyzer,
        implementer=resumed_implementer,
        reviewer=resumed_reviewer,
        repairer=resumed_implementer,
        verifier=resumed_verifier,
        commands=(_command("initial"),),
    )

    completed = await resumed_lifecycle.resume("work-1", project_id="project-a")

    assert completed.status == "COMPLETE"
    assert _git(workspace, "rev-parse", "HEAD") == committed_sha
    assert _git(workspace, "log", "-1", "--format=%B") == "sagewai work work-1"
    assert resumed_analyzer.calls == 0
    assert resumed_implementer.calls == 0
    assert resumed_verifier.calls == 0
    assert resumed_reviewer.calls == 0
    completed_events = await work_store.read_events("work-1", project_id="project-a")
    assert sum(
        event.event_type is WorkEventType.WORK_COMPLETED for event in completed_events
    ) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "implement_text"),
    (
        ("rewrite", "initial"),
        ("remove", "initial"),
        ("append_tail", "x" * 100_100),
    ),
    ids=("rewrite", "remove", "large-tail"),
)
async def test_reviewed_output_change_with_unchanged_head_freezes_control(
    completion_stores,
    tmp_path: Path,
    mutation: str,
    implement_text: str,
) -> None:
    work_store, knowledge_store = completion_stores
    repository, base_sha = _repository(tmp_path)
    implementer = MutationRuntime(implement_text=implement_text, repair_text="fixed")
    reviewer = ReviewRuntime("accept")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=implementer,
        reviewer=reviewer,
        repairer=implementer,
        commands=(_always_pass_command(),),
    )
    mutating_manager = MutateReviewedOutputBeforeCommitManager(
        lifecycle._worktree_manager,  # noqa: SLF001
        mutation=mutation,
    )
    lifecycle._worktree_manager = mutating_manager  # noqa: SLF001

    degraded = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha, SoftwareRepositoryOutcome.VERIFIED_COMMIT),
    )

    workspace = tmp_path / "worktrees/project-a/work-1/workspace"
    assert degraded.status == "CONTROL_DEGRADED"
    assert _git(workspace, "rev-parse", "HEAD") == base_sha
    assert mutating_manager.calls == 1
    events = await work_store.read_events("work-1", project_id="project-a")
    review = next(
        event for event in events if event.event_type is WorkEventType.REVIEW_RECORDED
    )
    assert review.payload_json["evidence_refs"][-1].startswith("artifact://sha256:")
    degradation = next(
        event for event in events if event.event_type is WorkEventType.CONTROL_DEGRADED
    )
    assert degradation.payload_json["stage"] == "repository"
    assert "reviewed diff digest changed" in degradation.payload_json["details"]
    assert not any(
        event.event_type is WorkEventType.WORK_COMPLETED
        or (
            event.event_type is WorkEventType.VERIFICATION_RECORDED
            and event.payload_json.get("stage") == "repository"
        )
        for event in events
    )


@pytest.mark.asyncio
async def test_unexpected_head_during_verified_commit_freezes_control(
    completion_stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = completion_stores
    repository, base_sha = _repository(tmp_path)
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    reviewer = ReviewRuntime("accept")
    verifier = CountingVerifier(
        SoftwareVerifier(knowledge_store=knowledge_store, runner=LocalVerificationRunner())
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=implementer,
        reviewer=reviewer,
        repairer=implementer,
        verifier=verifier,
        commands=(_command("initial"),),
    )
    moving_manager = MoveHeadBeforeCommitManager(
        lifecycle._worktree_manager  # noqa: SLF001
    )
    lifecycle._worktree_manager = moving_manager  # noqa: SLF001

    degraded = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha, SoftwareRepositoryOutcome.VERIFIED_COMMIT),
    )

    assert degraded.status == "CONTROL_DEGRADED"
    assert implementer.calls == 1
    assert verifier.calls == 1
    assert reviewer.calls == 1
    assert moving_manager.calls == 1
    events = await work_store.read_events("work-1", project_id="project-a")
    degradation = next(
        event for event in events if event.event_type is WorkEventType.CONTROL_DEGRADED
    )
    assert degradation.payload_json["stage"] == "repository"
    assert degradation.payload_json["run_id"] == "work-1:repository:1"
    assert degradation.payload_json["frozen_action_ids"] == [
        "work-1:repository:1:change"
    ]
    assert "workspace HEAD moved" in degradation.payload_json["details"]
    assert not any(
        event.event_type is WorkEventType.WORK_COMPLETED
        or (
            event.event_type is WorkEventType.VERIFICATION_RECORDED
            and event.payload_json.get("stage") == "repository"
        )
        for event in events
    )

    workspace = tmp_path / "worktrees/project-a/work-1/workspace"
    subprocess.run(
        ("git", "-C", str(workspace), "checkout", "--detach", "-q", base_sha),
        check=True,
    )
    projection = await work_store.load_work("work-1", project_id="project-a")
    assert projection is not None
    await work_store.save_work(projection.model_copy(update={"status": "COMPLETING"}))

    still_degraded = await lifecycle.resume("work-1", project_id="project-a")

    assert still_degraded.status == "CONTROL_DEGRADED"
    assert _git(workspace, "rev-parse", "HEAD") == base_sha
    assert moving_manager.calls == 1
    resumed_events = await work_store.read_events("work-1", project_id="project-a")
    assert not any(
        event.event_type in {
            WorkEventType.CONTROL_RESTORED,
            WorkEventType.WORK_COMPLETED,
        }
        or (
            event.event_type is WorkEventType.VERIFICATION_RECORDED
            and event.payload_json.get("stage") == "repository"
        )
        for event in resumed_events
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [SoftwareRepositoryOutcome.PULL_REQUEST, SoftwareRepositoryOutcome.MERGED],
)
async def test_remote_repository_outcomes_still_stop_ready_to_merge(
    completion_stores,
    tmp_path: Path,
    outcome: SoftwareRepositoryOutcome,
) -> None:
    work_store, knowledge_store = completion_stores
    repository, base_sha = _repository(tmp_path)
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=implementer,
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha, outcome),
    )

    assert record.status == "READY_TO_MERGE"
    assert _git(
        tmp_path / "worktrees/project-a/work-1/workspace",
        "rev-parse",
        "HEAD",
    ) == base_sha
