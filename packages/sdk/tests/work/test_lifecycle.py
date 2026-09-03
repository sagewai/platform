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

import asyncio
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sagewai.artifacts import LocalArtifactStore
from sagewai.core.state import InMemoryStore, StepStatus
from sagewai.safety.permissions import PermissionPolicy
from sagewai.work import (
    AcceptanceCriterion,
    ActionResult,
    ActivitySink,
    Assumption,
    CapabilityGrant,
    CapabilitySet,
    ClaimClassification,
    ClassifiedClaim,
    ControlPreconditionKind,
    ListActivitySink,
    OperatorDisciplineReport,
    OperatorResult,
    ProposedAcceptanceCriterion,
    ReviewFinding,
    ReviewResult,
    TaskCapsuleCompiler,
    VerificationResult,
    WorkAnalysisResult,
    WorkContract,
    WorkContractProposal,
    WorkDesignResult,
    WorkEvent,
    WorkEventType,
    WorkItem,
    WorkRecord,
    WorkStore,
    execution_attempt_from_events,
)
from sagewai.work.control import ControlCheckResult, OperatorController
from sagewai.work.knowledge import KnowledgeKind, KnowledgeQuery, KnowledgeStore
from sagewai.work.profiles.software import (
    SOFTWARE_VERIFICATION_ISOLATION_PRECONDITION_ID,
    SOFTWARE_WORKSPACE_CHECK_REF,
    SOFTWARE_WORKSPACE_PRECONDITION_ID,
    GitHubIssue,
    GitHubIssueLifecycle,
    GitHubMergeResult,
    GitHubPullRequest,
    GitHubPullRequestState,
    SoftwareContractContext,
    SoftwareLifecycle,
    SoftwareProfile,
    SoftwareReadOnlyResultValidator,
    SoftwareRepairContext,
    SoftwareRepositoryOutcome,
    SoftwareResultValidator,
    SoftwareReviewContext,
    SoftwareStageOperator,
    SoftwareVerifier,
    SoftwareWorkspaceControlCheck,
    SoftwareWorktreeManager,
    StageOperatorLadder,
    VerificationIsolationError,
    WorkspaceStaleError,
)
from sagewai.work.profiles.software.lifecycle import _store_diff_context
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.fakes_verification import LocalVerificationRunner

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


def _contract(
    base_sha: str,
    *,
    assumption_ids: tuple[str, ...] = (),
    allowed_scope: tuple[str, ...] = ("target.txt",),
) -> WorkContract:
    return WorkContract(
        id="contract-1",
        project_id="project-a",
        work_id="work-1",
        version=1,
        goal="Change target deterministically",
        allowed_scope=allowed_scope,
        acceptance_criteria=(
            AcceptanceCriterion(
                id="criterion-repository",
                project_id="project-a",
                statement="produce the accepted merged repository outcome",
                verification_kind="profile",
            ),
        ),
        constraints=(),
        non_goals=(),
        evidence_refs=(),
        assumption_ids=assumption_ids,
        risk="low",
        design_required=False,
        profile_context=SoftwareContractContext(
            project_id="project-a",
            base_sha=base_sha,
            repository_outcome=SoftwareRepositoryOutcome.MERGED,
            repository_criterion_id="criterion-repository",
        ).model_dump(mode="json"),
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


def _failed_result(request) -> OperatorResult:
    return _operator_result(request).model_copy(
        update={"status": "failed", "summary": "process exit 1"}
    )


class MutationRuntime:
    name = "mutation-runtime"

    def __init__(self, *, implement_text: str, repair_text: str) -> None:
        self.implement_text = implement_text
        self.repair_text = repair_text
        self.calls = 0
        self.capsules = []
        self.requests = []

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        self.requests.append(request)
        text = self.implement_text if request.stage == "implement" else self.repair_text
        (workspace.path / "target.txt").write_text(f"{text}\n")
        return _operator_result(request)


class RecordingSoftwareProfile(SoftwareProfile):
    def __init__(self) -> None:
        self.prepare_calls = 0
        self.verify_calls = 0

    async def prepare(self, work, contract):
        self.prepare_calls += 1
        return await super().prepare(work, contract)

    async def verify(self, work, contract, criterion_ids, actions):
        self.verify_calls += 1
        return await super().verify(work, contract, criterion_ids, actions)


class CrashingSoftwareProfile(RecordingSoftwareProfile):
    async def verify(self, work, contract, criterion_ids, actions):
        await super().verify(work, contract, criterion_ids, actions)
        raise RuntimeError("simulated profile verification restart")


class DiffReadingMutationRuntime(MutationRuntime):
    def __init__(
        self,
        *,
        implement_text: str,
        repair_text: str,
        artifact_store: LocalArtifactStore,
    ) -> None:
        super().__init__(implement_text=implement_text, repair_text=repair_text)
        self.artifact_store = artifact_store
        self.materialized_paths: list[Path] = []
        self.materialized_diffs: list[bytes] = []

    async def run(self, request, capsule, capabilities, workspace):
        if request.stage == "repair":
            context = SoftwareRepairContext.model_validate(capsule.profile_context)
            assert context.diff is None
            assert context.diff_workspace_path is not None
            materialized = workspace.path / context.diff_workspace_path
            self.materialized_paths.append(materialized)
            self.materialized_diffs.append(materialized.read_bytes())
            assert (
                materialized.stat().st_ino
                != self.artifact_store.resolve(
                    context.diff_artifact.storage_ref, project_id=request.project_id
                ).stat().st_ino
            )
        return await super().run(request, capsule, capabilities, workspace)


class AnalysisRuntime:
    name = "analysis-runtime"

    def __init__(
        self,
        *,
        proposal: WorkContractProposal | None = None,
        claims: tuple[ClassifiedClaim, ...] = (),
    ) -> None:
        self.proposal = proposal
        self.claims = claims
        self.calls = 0
        self.capsules = []

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        proposal = self.proposal or WorkContractProposal(
            goal="Change target deterministically",
            allowed_scope=("target.txt",),
            acceptance_criteria=(
                ProposedAcceptanceCriterion(
                    statement="deterministic verification passes",
                    verification_kind="deterministic",
                ),
            ),
            constraints=(),
            non_goals=(),
            risk="low",
            design_required=False,
        )
        analysis = WorkAnalysisResult(
            attempt_id=request.run_id,
            proposal=proposal,
            claims=self.claims,
        )
        return _operator_result(
            request,
            profile_context={"analysis_result": analysis.model_dump(mode="json")},
        )


def _profile_analysis_runtime() -> AnalysisRuntime:
    return AnalysisRuntime(
        proposal=WorkContractProposal(
            goal="Change target with profile verification",
            allowed_scope=("target.txt",),
            acceptance_criteria=(
                ProposedAcceptanceCriterion(
                    statement="profile action succeeds",
                    verification_kind="profile",
                ),
            ),
            constraints=(),
            non_goals=(),
            risk="low",
            design_required=False,
        )
    )


class AnalysisAndDesignRuntime(AnalysisRuntime):
    def __init__(
        self,
        *,
        design_claims: tuple[ClassifiedClaim, ...] = (),
        mutate_during_design: bool = False,
    ) -> None:
        super().__init__(
            proposal=WorkContractProposal(
                goal="Change target deterministically",
                allowed_scope=("target.txt",),
                acceptance_criteria=(
                    ProposedAcceptanceCriterion(
                        statement="deterministic verification passes",
                        verification_kind="deterministic",
                    ),
                ),
                constraints=(),
                non_goals=(),
                risk="low",
                design_required=True,
            ),
            claims=(),
        )
        self.design_claims = design_claims
        self.mutate_during_design = mutate_during_design
        self.design_calls = 0
        self.design_capsules = []
        self.design_requests = []

    async def run(self, request, capsule, capabilities, workspace):
        if request.stage == "analysis":
            return await super().run(request, capsule, capabilities, workspace)
        assert request.stage == "design"
        self.design_calls += 1
        self.design_capsules.append(capsule)
        self.design_requests.append(request)
        if self.mutate_during_design:
            (workspace.path / "target.txt").write_text("design mutation\n")
        design = WorkDesignResult(
            attempt_id=request.run_id,
            claims=self.design_claims,
        )
        return _operator_result(
            request,
            profile_context={"design_result": design.model_dump(mode="json")},
        )


class RestoringWorkspaceControlCheck:
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, context):
        self.calls += 1
        passed = self.calls > 1
        return ControlCheckResult(
            project_id=context.request.project_id,
            precondition_id=context.precondition.id,
            passed=passed,
            evidence_refs=("check://design-workspace",),
            detail=None if passed else "design workspace control unavailable",
            checked_at=datetime.now(timezone.utc),
        )


class MissingAnalysisResultRuntime:
    name = "missing-analysis-result-runtime"

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        return _operator_result(request)


class FailingOnceKnowledgeStore:
    def __init__(self, store: KnowledgeStore, *, fail_on_publish: int = 1) -> None:
        self.store = store
        self.fail_on_publish = fail_on_publish
        self.publish_calls = 0
        self.failed = False

    async def get(self, item_id: str, *, project_id: str):
        return await self.store.get(item_id, project_id=project_id)

    async def search(self, query: KnowledgeQuery):
        return await self.store.search(query)

    async def search_high_importance_project_findings_any_term(
        self, query: KnowledgeQuery, *, limit: int
    ):
        return await self.store.search_high_importance_project_findings_any_term(
            query, limit=limit
        )

    async def publish(self, item):
        self.publish_calls += 1
        if not self.failed and self.publish_calls == self.fail_on_publish:
            self.failed = True
            raise RuntimeError("simulated knowledge persistence interruption")
        await self.store.publish(item)


class OutOfScopeMutationRuntime(MutationRuntime):
    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        (workspace.path / "outside.txt").write_text("outside\n")
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


class CleanFailingMutationRuntime(MutationRuntime):
    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        self.requests.append(request)
        return _failed_result(request)


class FailingDirtyMutationRuntime(MutationRuntime):
    name = "dirty-failing-runtime"

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        self.requests.append(request)
        (workspace.path / "target.txt").write_text("stray\n")
        (workspace.path / "source.txt").write_text("dirty\n")
        return _failed_result(request)


class DirtyFailingRepairRuntime(MutationRuntime):
    name = "dirty-failing-repair-runtime"

    async def run(self, request, capsule, capabilities, workspace):
        assert request.stage == "repair"
        self.calls += 1
        self.capsules.append(capsule)
        self.requests.append(request)
        (workspace.path / "source.txt").write_text("dirty repair\n")
        (workspace.path / "stray.txt").write_text("stray repair\n")
        return _operator_result(request).model_copy(
            update={"status": "failed", "summary": "process exit 1"}
        )


class CommittingFailingMutationRuntime(MutationRuntime):
    name = "committing-failing-runtime"

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        self.requests.append(request)
        (workspace.path / "target.txt").write_text("committed\n")
        subprocess.run(("git", "-C", str(workspace.path), "add", "target.txt"), check=True)
        subprocess.run(
            ("git", "-C", str(workspace.path), "commit", "-qm", "operator commit"),
            check=True,
        )
        return _failed_result(request)


class CleanWorkspaceMutationRuntime(MutationRuntime):
    def __init__(self, *, implement_text: str, repair_text: str) -> None:
        super().__init__(implement_text=implement_text, repair_text=repair_text)
        self.target_existed_before_run = False
        self.source_text_before_run = ""

    async def run(self, request, capsule, capabilities, workspace):
        self.target_existed_before_run = (workspace.path / "target.txt").exists()
        self.source_text_before_run = (workspace.path / "source.txt").read_text()
        return await super().run(request, capsule, capabilities, workspace)


class TargetReadingMutationRuntime(MutationRuntime):
    def __init__(self, *, implement_text: str, repair_text: str) -> None:
        super().__init__(implement_text=implement_text, repair_text=repair_text)
        self.target_text_before_run: list[str | None] = []

    async def run(self, request, capsule, capabilities, workspace):
        target = workspace.path / "target.txt"
        self.target_text_before_run.append(target.read_text() if target.exists() else None)
        return await super().run(request, capsule, capabilities, workspace)


class WorkspaceReadingMutationRuntime(MutationRuntime):
    def __init__(self, *, implement_text: str, repair_text: str) -> None:
        super().__init__(implement_text=implement_text, repair_text=repair_text)
        self.target_text_before_run: list[str | None] = []
        self.source_text_before_run: list[str] = []
        self.stray_existed_before_run: list[bool] = []

    async def run(self, request, capsule, capabilities, workspace):
        target = workspace.path / "target.txt"
        self.target_text_before_run.append(target.read_text() if target.exists() else None)
        self.source_text_before_run.append((workspace.path / "source.txt").read_text())
        self.stray_existed_before_run.append((workspace.path / "stray.txt").exists())
        return await super().run(request, capsule, capabilities, workspace)


class FailingOnceThenPassingMutationRuntime(MutationRuntime):
    name = "flaky-runtime"

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        if self.calls == 1:
            return _failed_result(request)
        return await super().run(request, capsule, capabilities, workspace)


class FailedWithoutActionReceiptRuntime(MutationRuntime):
    async def run(self, request, capsule, capabilities, workspace):
        result = await super().run(request, capsule, capabilities, workspace)
        return result.model_copy(
            update={
                "status": "failed",
                "evidence_refs": ("runtime://native-failure",),
                "action_results": (),
            }
        )


class FailingOnceWithoutActionReceiptRuntime(MutationRuntime):
    async def run(self, request, capsule, capabilities, workspace):
        if self.calls == 0:
            self.calls += 1
            self.capsules.append(capsule)
            self.requests.append(request)
            return _operator_result(request).model_copy(
                update={
                    "status": "failed",
                    "evidence_refs": ("runtime://native-configuration-failure",),
                    "action_results": (),
                }
            )
        return await super().run(request, capsule, capabilities, workspace)


class PassedWithFailedActionReceiptRuntime(MutationRuntime):
    async def run(self, request, capsule, capabilities, workspace):
        result = await super().run(request, capsule, capabilities, workspace)
        failed = result.action_results[0].model_copy(update={"status": "failed"})
        return result.model_copy(update={"action_results": (failed,)})


class FailingAnalysisRuntime(AnalysisRuntime):
    name = "failing-analysis-runtime"

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        return _failed_result(request)


class DirtyFailingAnalysisRuntime(AnalysisRuntime):
    name = "dirty-failing-analysis-runtime"

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        (workspace.path / "target.txt").write_text("stray\n")
        (workspace.path / "source.txt").write_text("dirty\n")
        return _failed_result(request)


class FailingDesignRuntime(AnalysisAndDesignRuntime):
    name = "failing-design-runtime"

    async def run(self, request, capsule, capabilities, workspace):
        assert request.stage == "design"
        self.design_calls += 1
        self.design_capsules.append(capsule)
        self.design_requests.append(request)
        return _failed_result(request)


class DirtyFailingDesignRuntime(AnalysisAndDesignRuntime):
    name = "dirty-failing-design-runtime"

    async def run(self, request, capsule, capabilities, workspace):
        assert request.stage == "design"
        self.design_calls += 1
        self.design_capsules.append(capsule)
        self.design_requests.append(request)
        (workspace.path / "target.txt").write_text("stray\n")
        (workspace.path / "source.txt").write_text("dirty\n")
        return _failed_result(request)


class ReviewRuntime:
    name = "review-runtime"

    def __init__(self, *verdicts: str) -> None:
        self.verdicts = list(verdicts)
        self.calls = 0
        self.capsules = []
        self.requests = []

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        self.requests.append(request)
        verdict = self.verdicts.pop(0) if self.verdicts else "accept"
        findings = ()
        if verdict == "repair":
            findings = (
                ReviewFinding(
                    project_id=request.project_id,
                    severity="high",
                    claim="The target needs repair",
                    evidence_refs=tuple(capsule.prior_result_refs),
                    required_change="Write the repaired target",
                    profile_context={
                        "project_id": request.project_id,
                        "file": "target.txt",
                        "line": 1,
                    },
                ),
            )
        review = ReviewResult(
            project_id=request.project_id,
            attempt_id=request.run_id,
            verdict=verdict,
            findings=findings,
            evidence_refs=(f"review://{request.run_id}",),
            introduced_assumptions=(),
            unsupported_claims=(),
            scope_expansions=(),
            unsupported_implementation_choices=(),
        )
        return _operator_result(
            request,
            profile_context={"review_result": review.model_dump(mode="json")},
        )


class DiffReadingReviewRuntime(ReviewRuntime):
    def __init__(self, artifact_store: LocalArtifactStore, *verdicts: str) -> None:
        super().__init__(*verdicts)
        self.artifact_store = artifact_store
        self.materialized_paths: list[Path] = []
        self.materialized_diffs: list[bytes] = []

    async def run(self, request, capsule, capabilities, workspace):
        context = SoftwareReviewContext.model_validate(capsule.profile_context)
        if context.diff is None:
            assert context.diff_workspace_path is not None
            materialized = workspace.path / context.diff_workspace_path
            self.materialized_paths.append(materialized)
            self.materialized_diffs.append(materialized.read_bytes())
            assert (
                materialized.stat().st_ino
                != self.artifact_store.resolve(
                    context.diff_artifact.storage_ref, project_id=request.project_id
                ).stat().st_ino
            )
        return await super().run(request, capsule, capabilities, workspace)


class FailingReviewRuntime(ReviewRuntime):
    name = "failing-review-runtime"

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        self.requests.append(request)
        return _failed_result(request)


class DirtyFailingReviewRuntime(ReviewRuntime):
    name = "dirty-failing-review-runtime"

    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        self.requests.append(request)
        (workspace.path / "target.txt").write_text("stray\n")
        (workspace.path / "source.txt").write_text("dirty\n")
        return _failed_result(request)


class InvalidReviewRuntime(ReviewRuntime):
    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        self.capsules.append(capsule)
        self.requests.append(request)
        return _operator_result(
            request,
            profile_context={
                "review_result": {
                    "attempt_id": request.run_id,
                    "verdict": "accept",
                    "findings": [],
                    "evidence_refs": [f"review://{request.run_id}"],
                }
            },
        )


class WrongAttemptReviewRuntime(ReviewRuntime):
    async def run(self, request, capsule, capabilities, workspace):
        result = await super().run(request, capsule, capabilities, workspace)
        payload = dict(result.profile_context["review_result"])
        payload["attempt_id"] = "another-review-attempt"
        return result.model_copy(update={"profile_context": {"review_result": payload}})


class ForeignProjectReviewRuntime(ReviewRuntime):
    async def run(self, request, capsule, capabilities, workspace):
        result = await super().run(request, capsule, capabilities, workspace)
        payload = dict(result.profile_context["review_result"])
        payload["project_id"] = "project-b"
        return result.model_copy(update={"profile_context": {"review_result": payload}})


class ForeignFindingContextReviewRuntime(ReviewRuntime):
    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        review = ReviewResult(
            project_id=request.project_id,
            attempt_id=request.run_id,
            verdict="repair",
            findings=(
                ReviewFinding(
                    project_id=request.project_id,
                    severity="high",
                    claim="The target needs repair",
                    evidence_refs=(f"review://{request.run_id}",),
                    required_change="Write the repaired target",
                    profile_context={
                        "project_id": "project-b",
                        "file": "target.txt",
                        "line": 1,
                    },
                ),
            ),
            evidence_refs=(f"review://{request.run_id}",),
            introduced_assumptions=(),
            unsupported_claims=(),
            scope_expansions=(),
            unsupported_implementation_choices=(),
        )
        return _operator_result(
            request,
            profile_context={"review_result": review.model_dump(mode="json")},
        )


class InvalidFindingContextReviewRuntime(ReviewRuntime):
    async def run(self, request, capsule, capabilities, workspace):
        self.calls += 1
        review = ReviewResult(
            project_id=request.project_id,
            attempt_id=request.run_id,
            verdict="repair",
            findings=(
                ReviewFinding(
                    project_id=request.project_id,
                    severity="high",
                    claim="The target needs repair",
                    evidence_refs=(f"review://{request.run_id}",),
                    required_change="Write the repaired target",
                    profile_context={
                        "project_id": request.project_id,
                        "unexpected": True,
                    },
                ),
            ),
            evidence_refs=(f"review://{request.run_id}",),
            introduced_assumptions=(),
            unsupported_claims=(),
            scope_expansions=(),
            unsupported_implementation_choices=(),
        )
        return _operator_result(
            request,
            profile_context={"review_result": review.model_dump(mode="json")},
        )


class CrashVerifier:
    async def verify(self, **_kwargs):
        raise RuntimeError("simulated restart")


class RestoringIsolationVerifier:
    def __init__(self, delegate: SoftwareVerifier) -> None:
        self.delegate = delegate
        self.calls = 0

    async def verify(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise VerificationIsolationError("sandbox unavailable")
        return await self.delegate.verify(**kwargs)


class ForeignProjectCheckVerifier:
    def __init__(self, delegate: SoftwareVerifier) -> None:
        self.delegate = delegate

    async def verify(self, **kwargs):
        result = await self.delegate.verify(**kwargs)
        profile_context = dict(result.profile_context)
        checks = [dict(check) for check in profile_context["checks"]]
        checks[0]["project_id"] = "project-b"
        profile_context["checks"] = checks
        return result.model_copy(update={"profile_context": profile_context})



class MoveHeadAfterVerifier:
    def __init__(self, delegate: SoftwareVerifier) -> None:
        self.delegate = delegate
        self.calls = 0

    async def verify(self, **kwargs):
        self.calls += 1
        result = await self.delegate.verify(**kwargs)
        workspace = kwargs["workspace"]
        subprocess.run(
            ("git", "-C", str(workspace.path), "commit", "--allow-empty", "-qm", "moved"),
            check=True,
        )
        return result


class RemoveWorkspaceAfterVerifier:
    def __init__(self, delegate: SoftwareVerifier) -> None:
        self.delegate = delegate

    async def verify(self, **kwargs):
        result = await self.delegate.verify(**kwargs)
        shutil.rmtree(kwargs["workspace"].path)
        return result


class RemoveWorkspaceOnStatusStore:
    def __init__(self, store: WorkStore, *, status: str, workspace_path: Path) -> None:
        self.store = store
        self.status = status
        self.workspace_path = workspace_path
        self.removed = False

    def __getattr__(self, name: str):
        return getattr(self.store, name)

    async def save_work(self, record: WorkRecord) -> None:
        await self.store.save_work(record)
        if record.status == self.status and not self.removed:
            self.removed = True
            shutil.rmtree(self.workspace_path)


class HideWorkspaceDuringFirstReviewRuntime(ReviewRuntime):
    def __init__(self, *verdicts: str) -> None:
        super().__init__(*verdicts)
        self.hidden_path: Path | None = None

    async def run(self, request, capsule, capabilities, workspace):
        if self.calls == 0:
            self.calls += 1
            self.capsules.append(capsule)
            self.requests.append(request)
            self.hidden_path = workspace.path.with_name("workspace-hidden")
            workspace.path.rename(self.hidden_path)
            await asyncio.Event().wait()
        return await super().run(request, capsule, capabilities, workspace)


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


class BlockingOnceSoftwareResultValidator(SoftwareResultValidator):
    def __init__(self) -> None:
        self.calls = 0

    async def validate(self, *, request, result, workspace):
        report = await super().validate(
            request=request,
            result=result,
            workspace=workspace,
        )
        self.calls += 1
        if self.calls > 1:
            return report
        return report.model_copy(
            update={
                "scope_violations": ("outside.txt is outside allowed targets",),
                "verdict": "blocked",
            }
        )


def _controller(
    work_store: WorkStore,
    durability: InMemoryStore,
    validator,
    workspace_control_check=None,
) -> OperatorController:
    return OperatorController(
        work_store=work_store,
        durability_store=durability,
        permission_policy=PermissionPolicy(),
        control_checks={
            SOFTWARE_WORKSPACE_CHECK_REF: (
                workspace_control_check or SoftwareWorkspaceControlCheck()
            ),
        },
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


def test_diff_context_limit_uses_exact_utf8_bytes_and_keeps_artifact(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(root=tmp_path / "objects")

    inline, at_limit = _store_diff_context(
        artifact_store=store,
        project_id="project-a",
        raw_diff="é",
        max_inline_diff_bytes=2,
    )
    omitted, above_limit = _store_diff_context(
        artifact_store=store,
        project_id="project-a",
        raw_diff="é",
        max_inline_diff_bytes=1,
    )

    assert inline == "é"
    assert omitted is None
    assert above_limit.storage_ref == at_limit.storage_ref
    assert store.read(above_limit.storage_ref, project_id="project-a") == "é".encode()


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
    analyzer: AnalysisRuntime | None = None,
    analyst_control_check=None,
    analyst_actor: str = "operator:analyst",
    implementer_actor: str = "operator:implementer",
    profile: SoftwareProfile | None = None,
    reviewer_actor: str = "operator:reviewer",
    repairer_actor: str | None = None,
    artifact_root: Path | None = None,
    max_inline_diff_bytes: int = 4000,
    implementer_validator=None,
    implementer_ladder: tuple[MutationRuntime, ...] | None = None,
    analyst_ladder: tuple[AnalysisRuntime, ...] | None = None,
    designer_ladder: tuple[AnalysisAndDesignRuntime, ...] | None = None,
    reviewer_ladder: tuple[ReviewRuntime, ...] | None = None,
    repairer_ladder: tuple[MutationRuntime, ...] | None = None,
    reviewer_validator=None,
    max_attempts_per_stage: int = 3,
    activity_sink: ActivitySink | None = None,
) -> SoftwareLifecycle:
    artifact_store = LocalArtifactStore(
        root=artifact_root or worktree_root.parent / "objects"
    )
    compiler = TaskCapsuleCompiler(
        knowledge_store=knowledge_store,
        artifact_store=artifact_store,
    )
    analyst_positions = (
        ((analyst_actor, analyzer or AnalysisRuntime()),)
        if analyst_ladder is None
        else tuple(
            (f"operator:analyst:{index}", runtime)
            for index, runtime in enumerate(analyst_ladder, start=1)
        )
    )
    analyst = StageOperatorLadder(
        tuple(
            SoftwareStageOperator(
                actor_ref=actor_ref,
                runtime=runtime,
                capabilities=_read_capabilities(),
                controller=_controller(
                    work_store,
                    durability,
                    SoftwareReadOnlyResultValidator(),
                    workspace_control_check=analyst_control_check,
                ),
            )
            for actor_ref, runtime in analyst_positions
        )
    )
    if designer_ladder is None:
        designer = analyst
    else:
        designer = StageOperatorLadder(
            tuple(
                SoftwareStageOperator(
                    actor_ref=f"operator:designer:{index}",
                    runtime=runtime,
                    capabilities=_read_capabilities(),
                    controller=_controller(
                        work_store,
                        durability,
                        SoftwareReadOnlyResultValidator(),
                    ),
                )
                for index, runtime in enumerate(designer_ladder, start=1)
            )
        )
    implementer_positions = (
        ((implementer_actor, implementer),)
        if implementer_ladder is None
        else tuple(
            (f"operator:implementer:{index}", runtime)
            for index, runtime in enumerate(implementer_ladder, start=1)
        )
    )
    reviewer_positions = (
        ((reviewer_actor, reviewer),)
        if reviewer_ladder is None
        else tuple(
            (f"operator:reviewer:{index}", runtime)
            for index, runtime in enumerate(reviewer_ladder, start=1)
        )
    )
    repairer_positions = (
        ((repairer_actor or implementer_actor, repairer),)
        if repairer_ladder is None
        else tuple(
            (f"operator:repairer:{index}", runtime)
            for index, runtime in enumerate(repairer_ladder, start=1)
        )
    )
    return SoftwareLifecycle(
        profile=profile or SoftwareProfile(),
        work_store=work_store,
        knowledge_store=knowledge_store,
        capsule_compiler=compiler,
        worktree_manager=SoftwareWorktreeManager(root=worktree_root),
        verifier=verifier
        or SoftwareVerifier(
            knowledge_store=knowledge_store,
            runner=LocalVerificationRunner(),
            artifact_store=artifact_store,
            activity_sink=activity_sink,
        ),
        artifact_store=artifact_store,
        repository=repository,
        analyst=analyst,
        designer=designer,
        implementer=StageOperatorLadder(
            tuple(
                SoftwareStageOperator(
                    actor_ref=actor_ref,
                    runtime=runtime,
                    capabilities=_write_capabilities(),
                    controller=_controller(
                        work_store,
                        durability,
                        implementer_validator or SoftwareResultValidator(),
                    ),
                )
                for actor_ref, runtime in implementer_positions
            )
        ),
        reviewer=StageOperatorLadder(
            tuple(
                SoftwareStageOperator(
                    actor_ref=actor_ref,
                    runtime=runtime,
                    capabilities=_read_capabilities(),
                    controller=_controller(
                        work_store,
                        durability,
                        reviewer_validator or SoftwareReadOnlyResultValidator(),
                    ),
                )
                for actor_ref, runtime in reviewer_positions
            )
        ),
        repairer=StageOperatorLadder(
            tuple(
                SoftwareStageOperator(
                    actor_ref=actor_ref,
                    runtime=runtime,
                    capabilities=_write_capabilities(),
                    controller=_controller(
                        work_store,
                        durability,
                        SoftwareResultValidator(),
                    ),
                )
                for actor_ref, runtime in repairer_positions
            )
        ),
        repo_instructions=("AGENTS.md",),
        verification_commands=commands,
        max_attempts_per_stage=max_attempts_per_stage,
        max_inline_diff_bytes=max_inline_diff_bytes,
    )


@pytest.fixture
async def stores(dialect_engine):  # noqa: F811
    work_store = WorkStore(engine=dialect_engine)
    knowledge_store = KnowledgeStore(engine=dialect_engine)
    await work_store.init()
    await knowledge_store.init()
    return work_store, knowledge_store


def test_next_run_starts_new_attempt_after_base_moved() -> None:
    events = [
        WorkEvent(
            id="start",
            project_id="project-a",
            work_id="work-1",
            sequence=1,
            event_type=WorkEventType.STAGE_STARTED,
            actor_type="test",
            actor_ref=None,
            payload_json={
                "stage": "implement",
                "run_id": "work-1:implement:1",
                "runtime": "codex",
            },
            created_at=NOW,
        ),
        WorkEvent(
            id="execution",
            project_id="project-a",
            work_id="work-1",
            sequence=2,
            event_type=WorkEventType.EXECUTION_RECORDED,
            actor_type="test",
            actor_ref=None,
            payload_json={
                "run_id": "work-1:implement:1",
                "status": "passed",
            },
            created_at=NOW,
        ),
        WorkEvent(
            id="base-moved",
            project_id="project-a",
            work_id="work-1",
            sequence=3,
            event_type=WorkEventType.BASE_MOVED,
            actor_type="test",
            actor_ref=None,
            payload_json={
                "phase": "publish",
                "expected_base": "base",
                "found_base": "other",
            },
            created_at=NOW,
        ),
    ]

    assert SoftwareLifecycle._next_run(events, "work-1", "implement") == (
        "work-1:implement:2",
        2,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ("SUPERSEDED", "BASE_MOVED"))
async def test_resume_early_return_status_returns_record_without_appending_events(
    status: str,
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, _base_sha = _repository(tmp_path)
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="unused", repair_text="unused"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )
    record = WorkRecord(
        work_id="work-1",
        project_id="project-a",
        source_ref=None,
        profile="software",
        status=status,
        contract_version=1,
        active_run_id=None,
        pending_gate=None,
        profile_context={"base_sha": "base"},
        created_at=NOW,
        updated_at=NOW,
    )
    await work_store.save_work(record)
    before = await work_store.read_events("work-1", project_id="project-a")

    resumed = await lifecycle.resume("work-1", project_id="project-a")
    after = await work_store.read_events("work-1", project_id="project-a")

    assert resumed == record
    assert after == before == []


@pytest.mark.asyncio
async def test_implementation_escalates_to_the_next_ladder_position_on_runtime_failure(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    flaky = FailingOnceThenPassingMutationRuntime(
        implement_text="unused",
        repair_text="unused",
    )
    passing = MutationRuntime(implement_text="initial", repair_text="unused")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=flaky,
        implementer_ladder=(flaky, passing),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    selected = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "implement"
    ]
    assert [(item["position"], item["reason"]) for item in selected] == [
        (1, "initial"),
        (2, "escalated"),
    ]
    assert [item["run_id"] for item in selected] == [
        "work-1:implement:1",
        "work-1:implement:2",
    ]
    assert [item["runtime"] for item in selected] == [flaky.name, passing.name]
    assert [item["role"] for item in selected] == ["implementer", "implementer"]
    assert [item["attempt"] for item in selected] == [1, 2]
    assert flaky.calls == 1
    assert passing.calls == 1
    assert record.status == "READY_TO_MERGE"


@pytest.mark.asyncio
async def test_start_carries_task_id_to_record_profile_context(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )
    contract = _contract(base_sha).model_copy(
        update={
            "profile_context": SoftwareContractContext(
                project_id="project-a",
                base_sha=base_sha,
                repository_outcome=SoftwareRepositoryOutcome.MERGED,
                repository_criterion_id="criterion-repository",
                task_id="task-1",
            ).model_dump(mode="json")
        }
    )

    record = await lifecycle.start(work_item=_work_item(), contract=contract)

    assert record.profile_context["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_analysis_escalates_to_the_next_ladder_position_on_runtime_failure(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = FailingAnalysisRuntime()
    passing = AnalysisRuntime()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyst_ladder=(failing, passing),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(
            base_sha,
            allowed_scope=("target.txt", "source.txt", "stray.txt"),
        ),
    )

    events = await work_store.read_events("work-1", project_id="project-a")
    selected = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "analysis"
    ]
    assert [(item["position"], item["reason"]) for item in selected] == [
        (1, "initial"),
        (2, "escalated"),
    ]
    assert [item["run_id"] for item in selected] == [
        "work-1:analysis:1",
        "work-1:analysis:2",
    ]
    assert [item["runtime"] for item in selected] == [failing.name, passing.name]
    assert [item["role"] for item in selected] == ["analyst", "analyst"]
    assert [item["attempt"] for item in selected] == [1, 2]
    assert failing.calls == 1
    assert passing.calls == 1
    assert record.status == "READY_TO_MERGE"


@pytest.mark.asyncio
async def test_dirty_failed_analysis_attempt_blocks_after_escalation(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = DirtyFailingAnalysisRuntime()
    passing = AnalysisRuntime()
    implementer = MutationRuntime(implement_text="must-not-run", repair_text="unused")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyst_ladder=(failing, passing),
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    selected = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "analysis"
    ]
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert [item["run_id"] for item in selected] == [
        "work-1:analysis:1",
        "work-1:analysis:2",
    ]
    assert blocker.payload_json["reason"] == "analyst_changed_workspace"
    assert failing.calls == 1
    assert passing.calls == 1
    assert implementer.calls == 0
    assert record.status == "WORK_BLOCKED"


@pytest.mark.asyncio
async def test_design_escalates_to_the_next_ladder_position_on_runtime_failure(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    analyzer = AnalysisAndDesignRuntime()
    failing = FailingDesignRuntime()
    passing = AnalysisAndDesignRuntime()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=analyzer,
        designer_ladder=(failing, passing),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    selected = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "design"
    ]
    assert [(item["position"], item["reason"]) for item in selected] == [
        (1, "initial"),
        (2, "escalated"),
    ]
    assert [item["run_id"] for item in selected] == [
        "work-1:design:1",
        "work-1:design:2",
    ]
    assert [item["runtime"] for item in selected] == [failing.name, passing.name]
    assert [item["role"] for item in selected] == ["designer", "designer"]
    assert [item["attempt"] for item in selected] == [1, 2]
    assert analyzer.calls == 1
    assert failing.design_calls == 1
    assert passing.design_calls == 1
    assert record.status == "READY_TO_MERGE"


@pytest.mark.asyncio
async def test_dirty_failed_design_attempt_blocks_after_escalation(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    analyzer = AnalysisAndDesignRuntime()
    failing = DirtyFailingDesignRuntime()
    passing = AnalysisAndDesignRuntime()
    implementer = MutationRuntime(implement_text="must-not-run", repair_text="unused")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=analyzer,
        designer_ladder=(failing, passing),
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    selected = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "design"
    ]
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert [item["run_id"] for item in selected] == [
        "work-1:design:1",
        "work-1:design:2",
    ]
    assert blocker.payload_json["reason"] == "designer_changed_workspace"
    assert analyzer.calls == 1
    assert failing.design_calls == 1
    assert passing.design_calls == 1
    assert implementer.calls == 0
    assert record.status == "WORK_BLOCKED"


@pytest.mark.asyncio
async def test_review_escalates_to_the_next_ladder_position_on_runtime_failure(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = FailingReviewRuntime()
    passing = ReviewRuntime("accept")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=passing,
        reviewer_ladder=(failing, passing),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    selected = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "review"
    ]
    assert [(item["position"], item["reason"]) for item in selected] == [
        (1, "initial"),
        (2, "escalated"),
    ]
    assert [item["run_id"] for item in selected] == [
        "work-1:review:1",
        "work-1:review:2",
    ]
    assert [item["runtime"] for item in selected] == [failing.name, passing.name]
    assert [item["role"] for item in selected] == ["reviewer", "reviewer"]
    assert [item["attempt"] for item in selected] == [1, 2]
    assert failing.calls == 1
    assert passing.calls == 1
    assert record.status == "READY_TO_MERGE"


@pytest.mark.asyncio
async def test_dirty_failed_review_attempt_blocks_after_escalation(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = DirtyFailingReviewRuntime()
    passing = ReviewRuntime("accept")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=passing,
        reviewer_ladder=(failing, passing),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    selected = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "review"
    ]
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert [item["run_id"] for item in selected] == [
        "work-1:review:1",
        "work-1:review:2",
    ]
    assert blocker.payload_json["reason"] == "reviewer_changed_workspace"
    assert failing.calls == 1
    assert passing.calls == 1
    assert record.status == "WORK_BLOCKED"


@pytest.mark.asyncio
async def test_blocked_does_not_escalate_to_next_ladder_position(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    validator = BlockingOnceSoftwareResultValidator()
    blocked_reviewer = ReviewRuntime("accept")
    unused_reviewer = ReviewRuntime("accept")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=blocked_reviewer,
        reviewer_ladder=(blocked_reviewer, unused_reviewer),
        reviewer_validator=validator,
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    selected = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "review"
    ]
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert [item["position"] for item in selected] == [1]
    assert [item["run_id"] for item in selected] == ["work-1:review:1"]
    assert blocker.payload_json["reason"] == "review_failed"
    assert validator.calls == 1
    assert blocked_reviewer.calls == 1
    assert unused_reviewer.calls == 0
    assert record.status == "WORK_BLOCKED"


@pytest.mark.asyncio
async def test_implementation_escalation_restores_workspace_before_next_attempt(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = FailingDirtyMutationRuntime(
        implement_text="unused",
        repair_text="unused",
    )
    passing = CleanWorkspaceMutationRuntime(
        implement_text="initial",
        repair_text="unused",
    )
    analyzer = AnalysisRuntime(
        proposal=WorkContractProposal(
            goal="Change target deterministically",
            allowed_scope=("target.txt", "source.txt"),
            acceptance_criteria=(
                ProposedAcceptanceCriterion(
                    statement="deterministic verification passes",
                    verification_kind="deterministic",
                ),
            ),
            constraints=(),
            non_goals=(),
            risk="low",
            design_required=False,
        )
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=analyzer,
        implementer=failing,
        implementer_ladder=(failing, passing),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha, allowed_scope=("target.txt", "source.txt")),
    )

    assert failing.calls == 1
    assert passing.calls == 1
    assert passing.target_existed_before_run is False
    assert passing.source_text_before_run == "base\n"
    assert record.status == "READY_TO_MERGE"


@pytest.mark.asyncio
async def test_repair_escalation_keeps_implemented_workspace_for_next_attempt(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = CleanFailingMutationRuntime(
        implement_text="unused",
        repair_text="unused",
    )
    passing = TargetReadingMutationRuntime(
        implement_text="unused",
        repair_text="fixed",
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=ReviewRuntime("accept"),
        repairer=failing,
        repairer_ladder=(failing, passing),
        commands=(_command("fixed"),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    selected = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "repair"
    ]
    assert passing.target_text_before_run == ["initial\n"]
    assert selected == [
        {
            "role": "repairer",
            "stage": "repair",
            "run_id": "work-1:repair:1",
            "attempt": 1,
            "position": 1,
            "runtime": failing.name,
            "reason": "initial",
        },
        {
            "role": "repairer",
            "stage": "repair",
            "run_id": "work-1:repair:2",
            "attempt": 2,
            "position": 2,
            "runtime": passing.name,
            "reason": "escalated",
        },
    ]
    assert failing.calls == 1
    assert record.status == "READY_TO_MERGE"


@pytest.mark.asyncio
async def test_repair_escalation_leaves_dirty_failed_workspace_for_next_attempt(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = DirtyFailingRepairRuntime(
        implement_text="unused",
        repair_text="unused",
    )
    passing = WorkspaceReadingMutationRuntime(
        implement_text="unused",
        repair_text="fixed",
    )
    analyzer = AnalysisRuntime(
        proposal=WorkContractProposal(
            goal="Change target deterministically",
            allowed_scope=("target.txt", "source.txt", "stray.txt"),
            acceptance_criteria=(
                ProposedAcceptanceCriterion(
                    statement="deterministic verification passes",
                    verification_kind="deterministic",
                ),
            ),
            constraints=(),
            non_goals=(),
            risk="low",
            design_required=False,
        )
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=analyzer,
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=ReviewRuntime("accept"),
        repairer=failing,
        repairer_ladder=(failing, passing),
        commands=(_command("fixed"),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(
            base_sha,
            allowed_scope=("target.txt", "source.txt", "stray.txt"),
        ),
    )

    assert failing.calls == 1
    assert passing.calls == 1
    assert passing.target_text_before_run == ["initial\n"]
    assert passing.source_text_before_run == ["dirty repair\n"]
    assert passing.stray_existed_before_run == [True]
    assert record.status == "READY_TO_MERGE"


@pytest.mark.asyncio
async def test_repair_blocks_after_max_attempts_with_implementation_on_disk(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = CleanFailingMutationRuntime(
        implement_text="unused",
        repair_text="unused",
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=ReviewRuntime("accept"),
        repairer=failing,
        repairer_ladder=(failing,),
        commands=(_command("fixed"),),
        max_attempts_per_stage=2,
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert record.status == "WORK_BLOCKED"
    assert failing.calls == 2
    assert blocker.payload_json["reason"] == "repair_failed"
    assert blocker.payload_json["attempts"] == 2
    assert (
        tmp_path / "worktrees/project-a/work-1/workspace/target.txt"
    ).read_text() == "initial\n"


@pytest.mark.asyncio
async def test_implementation_restore_with_moved_head_degrades_control(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = CommittingFailingMutationRuntime(
        implement_text="unused",
        repair_text="unused",
    )
    passing = MutationRuntime(implement_text="must-not-run", repair_text="unused")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=failing,
        implementer_ladder=(failing, passing),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    degraded = next(
        event for event in events if event.event_type is WorkEventType.CONTROL_DEGRADED
    )
    assert record.status == "CONTROL_DEGRADED"
    assert failing.calls == 1
    assert passing.calls == 0
    assert degraded.payload_json["run_id"] == "work-1:implement:1"
    assert degraded.payload_json["stage"] == "implement"
    assert degraded.payload_json["failed_preconditions"] == [
        SOFTWARE_WORKSPACE_PRECONDITION_ID
    ]
    assert degraded.payload_json["evidence_refs"] == ["workspace://workspace"]
    assert degraded.payload_json["details"].startswith(
        f"{SOFTWARE_WORKSPACE_PRECONDITION_ID}: workspace HEAD moved:"
    )
    assert degraded.payload_json["frozen_action_ids"] == [
        "work-1:implement:1:change"
    ]


@pytest.mark.asyncio
async def test_verification_failure_does_not_escalate(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    implementer = MutationRuntime(implement_text="initial", repair_text="unused")
    unused_implementer = MutationRuntime(implement_text="unused", repair_text="unused")
    repairer = MutationRuntime(implement_text="unused", repair_text="fixed")
    sink = ListActivitySink()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=implementer,
        implementer_ladder=(implementer, unused_implementer),
        reviewer=ReviewRuntime("accept"),
        repairer=repairer,
        commands=(_command("fixed"),),
        activity_sink=sink,
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    selected_implement = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "implement"
    ]
    selected_repair = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "repair"
    ]
    assert [item["position"] for item in selected_implement] == [1]
    assert [item["position"] for item in selected_repair] == [1]
    assert implementer.calls == 1
    assert unused_implementer.calls == 0
    assert repairer.calls == 1
    assert record.status == "READY_TO_MERGE"
    verify_runs = [item.run_id for item in sink.items if item.source == "verifier" and item.kind == "command"]
    assert verify_runs == ["work-1:verify:1", "work-1:verify:2"]


@pytest.mark.asyncio
async def test_implementation_blocks_after_max_attempts(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = FailedMutationRuntime(implement_text="failed", repair_text="unused")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=failing,
        implementer_ladder=(failing,),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
        max_attempts_per_stage=2,
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    selected = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "implement"
    ]
    assert [(item["run_id"], item["position"], item["reason"]) for item in selected] == [
        ("work-1:implement:1", 1, "initial"),
        ("work-1:implement:2", 1, "escalated"),
    ]
    assert [item["attempt"] for item in selected] == [1, 2]
    assert failing.calls == 2
    assert record.status == "WORK_BLOCKED"
    blocker = next(
        event for event in events if event.event_type is WorkEventType.WORK_BLOCKED
    )
    assert blocker.payload_json["reason"] == "implement_failed"
    assert blocker.payload_json["attempts"] == 2


@pytest.mark.asyncio
async def test_analysis_failed_block_includes_attempt_count(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = FailingAnalysisRuntime()
    implementer = MutationRuntime(implement_text="must-not-run", repair_text="unused")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyst_ladder=(failing,),
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
        max_attempts_per_stage=2,
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert record.status == "WORK_BLOCKED"
    assert failing.calls == 2
    assert implementer.calls == 0
    assert blocker.payload_json["reason"] == "analysis_failed"
    assert blocker.payload_json["attempts"] == 2


@pytest.mark.asyncio
async def test_design_failed_block_includes_attempt_count(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = FailingDesignRuntime()
    implementer = MutationRuntime(implement_text="must-not-run", repair_text="unused")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=AnalysisAndDesignRuntime(),
        designer_ladder=(failing,),
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
        max_attempts_per_stage=2,
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert record.status == "WORK_BLOCKED"
    assert failing.design_calls == 2
    assert implementer.calls == 0
    assert blocker.payload_json["reason"] == "design_failed"
    assert blocker.payload_json["attempts"] == 2


@pytest.mark.asyncio
async def test_review_failed_block_includes_attempt_count(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    failing = FailingReviewRuntime()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=failing,
        reviewer_ladder=(failing,),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
        max_attempts_per_stage=2,
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert record.status == "WORK_BLOCKED"
    assert failing.calls == 2
    assert blocker.payload_json["reason"] == "review_failed"
    assert blocker.payload_json["attempts"] == 2


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
    analyzer = AnalysisRuntime(
        claims=(
            ClassifiedClaim(
                classification=ClaimClassification.FACT,
                statement="The repository base is pinned",
                kind="repository_state",
                evidence_refs=(f"git://{base_sha}",),
                confidence="high",
                impact_if_wrong="medium",
            ),
        )
    )
    profile = RecordingSoftwareProfile()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        analyzer=analyzer,
        implementer=implementer,
        reviewer=reviewer,
        repairer=repairer,
        profile=profile,
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert record.status == "READY_TO_MERGE"
    assert record.status != "COMPLETE"
    assert implementer.calls == 1
    assert profile.prepare_calls == 1
    assert profile.verify_calls == 0
    assert reviewer.calls == 1
    assert repairer.calls == 0
    for request in (*implementer.requests, *reviewer.requests):
        assert len(request.control_preconditions) == 1
        precondition = request.control_preconditions[0]
        assert precondition.kind is ControlPreconditionKind.WORKSPACE
        assert precondition.check_ref == SOFTWARE_WORKSPACE_CHECK_REF
    analysis_ref = "work-1:analysis:1:claim:1"
    assert implementer.capsules[0].knowledge_refs == (analysis_ref,)

    capsule = reviewer.capsules[0]
    context = SoftwareReviewContext.model_validate(capsule.profile_context)
    assert context.verification.passed is True
    assert context.relevant_files == ("target.txt",)
    assert "initial" in context.diff
    assert context.diff_artifact.media_type == "text/x-diff"
    assert context.diff_artifact.created_by == "software.lifecycle"
    fresh_artifact_store = LocalArtifactStore(root=tmp_path / "objects")
    assert fresh_artifact_store.read(context.diff_artifact.storage_ref, project_id="project-a") == (
        context.diff.encode()
    )
    assert capsule.prior_result_refs == context.verification.evidence_refs
    assert len(context.verification.evidence_refs) == 1
    assert tuple(item.id for item in capsule.knowledge_items) == (
        analysis_ref,
        context.verification.evidence_refs[-1],
    )
    serialized = json.dumps(capsule.model_dump(mode="json")).lower()
    assert "session" not in serialized
    events = await work_store.read_events("work-1", project_id="project-a")
    attempt = execution_attempt_from_events(events, "work-1:implement:1")
    assert attempt is not None
    assert attempt.project_id == "project-a"
    assert attempt.work_id == "work-1"
    assert attempt.stage == "implement"
    assert attempt.runtime == "mutation-runtime"
    assert attempt.workspace_ref == "workspace://workspace"
    assert attempt.status == "passed"
    assert attempt.completed_at is not None
    assert attempt.profile_context == {
        "project_id": "project-a",
        "base_sha": base_sha,
        "result_sha": _git(
            tmp_path / "worktrees/project-a/work-1/workspace",
            "rev-parse",
            "HEAD",
        ),
    }
    assert "chat_history" not in serialized


@pytest.mark.asyncio
async def test_verifier_emits_command_and_tool_result_activity(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    sink = ListActivitySink()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
        activity_sink=sink,
    )
    await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    verifier_events = [item for item in sink.items if item.source == "verifier"]
    assert [item.kind for item in verifier_events] == ["command", "tool_result"]
    assert verifier_events[0].summary == _command("initial")
    assert verifier_events[1].summary.startswith("exit 0")
    assert verifier_events[0].run_id == verifier_events[1].run_id
    assert verifier_events[0].run_id == "work-1:verify:1"


@pytest.mark.asyncio
async def test_analysis_stage_completed_carries_artifact_refs(
    stores,
    tmp_path: Path,
) -> None:
    class ArtifactAnalysisRuntime(AnalysisRuntime):
        async def run(self, request, capsule, capabilities, workspace):
            result = await super().run(request, capsule, capabilities, workspace)
            return result.model_copy(
                update={"artifact_refs": ("artifact://sha256:analysis",)}
            )

    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=ArtifactAnalysisRuntime(),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )
    await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    events = await work_store.read_events("work-1", project_id="project-a")
    completed = next(
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "analysis"
    )
    assert completed.payload_json["artifact_refs"] == ["artifact://sha256:analysis"]


@pytest.mark.asyncio
async def test_design_required_runs_read_only_design_before_implementation(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    planner = AnalysisAndDesignRuntime(
        design_claims=(
            ClassifiedClaim(
                classification=ClaimClassification.DECISION,
                statement="Cobalt lattice remains the selected topology",
                kind="design",
                evidence_refs=(),
                confidence="high",
                impact_if_wrong="medium",
            ),
        )
    )
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=planner,
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    assert record.status == "READY_TO_MERGE"
    assert planner.calls == 1
    assert planner.design_calls == 1
    assert planner.design_requests[0].action_intents == ()
    assert planner.design_capsules[0].stage == "design"
    assert implementer.calls == 1
    design_ref = "work-1:design:1:claim:1"
    implement_capsule = implementer.capsules[0]
    assert implement_capsule.prior_result_refs == (
        design_ref,
        "runtime://work-1:design:1",
    )
    assert design_ref in implement_capsule.knowledge_refs
    assert design_ref in tuple(item.id for item in implement_capsule.knowledge_items)
    events = await work_store.read_events("work-1", project_id="project-a")
    design_completed = next(
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "design"
    )
    assert design_completed.payload_json["knowledge_refs"] == [design_ref]
    completed_stages = [
        event.payload_json["stage"]
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
    ]
    assert completed_stages == ["analysis", "design", "implement"]
    design = await knowledge_store.search(
        KnowledgeQuery(text="cobalt lattice", project_id="project-a", work_id="work-1")
    )
    assert [item.kind for item in design] == [KnowledgeKind.DECISION]


@pytest.mark.asyncio
async def test_design_knowledge_is_direct_context_for_review_and_repair(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    planner = AnalysisAndDesignRuntime(
        design_claims=(
            ClassifiedClaim(
                classification=ClaimClassification.DECISION,
                statement="Cobalt lattice remains the selected topology",
                kind="design",
                evidence_refs=(),
                confidence="high",
                impact_if_wrong="medium",
            ),
        )
    )
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    reviewer = ReviewRuntime("repair", "accept")
    repairer = MutationRuntime(implement_text="unused", repair_text="fixed")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=planner,
        implementer=implementer,
        reviewer=reviewer,
        repairer=repairer,
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    assert record.status == "READY_TO_MERGE"
    assert implementer.calls == 1
    assert reviewer.calls == 2
    assert repairer.calls == 1
    design_ref = "work-1:design:1:claim:1"
    later_capsules = (
        implementer.capsules[0],
        reviewer.capsules[0],
        repairer.capsules[0],
        reviewer.capsules[1],
    )
    for capsule in later_capsules:
        assert design_ref in capsule.prior_result_refs
        assert design_ref in capsule.knowledge_refs
        assert design_ref in tuple(item.id for item in capsule.knowledge_items)


@pytest.mark.asyncio
async def test_design_not_required_skips_design(stores, tmp_path: Path) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    analyzer = AnalysisRuntime()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=analyzer,
        implementer=MutationRuntime(implement_text="initial", repair_text="fixed"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    assert record.status == "READY_TO_MERGE"
    assert analyzer.calls == 1
    events = await work_store.read_events("work-1", project_id="project-a")
    assert not any(
        event.payload_json.get("stage") == "design"
        for event in events
        if event.event_type in {WorkEventType.STAGE_STARTED, WorkEventType.STAGE_COMPLETED}
    )


@pytest.mark.asyncio
async def test_resume_design_uses_completed_result_without_rerunning_operator(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    failing_store = FailingOnceKnowledgeStore(knowledge_store)
    repository, base_sha = _repository(tmp_path)
    planner = AnalysisAndDesignRuntime(
        design_claims=(
            ClassifiedClaim(
                classification=ClaimClassification.DECISION,
                statement="Reuse the current implementation boundary",
                kind="design",
                evidence_refs=(),
                confidence="high",
                impact_if_wrong="medium",
            ),
        )
    )
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=failing_store,
        durability=InMemoryStore(),
        analyzer=planner,
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    with pytest.raises(RuntimeError, match="knowledge persistence interruption"):
        await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    interrupted = await work_store.load_work("work-1", project_id="project-a")
    assert interrupted is not None and interrupted.status == "DESIGNING"
    assert planner.design_calls == 1

    resumed = await lifecycle.resume("work-1", project_id="project-a")

    assert resumed.status == "READY_TO_MERGE"
    assert planner.design_calls == 1
    assert implementer.calls == 1
    events = await work_store.read_events("work-1", project_id="project-a")
    assert (
        sum(
            event.event_type is WorkEventType.STAGE_COMPLETED
            and event.payload_json.get("stage") == "design"
            for event in events
        )
        == 1
    )


@pytest.mark.asyncio
async def test_restored_design_control_resumes_same_run_without_duplicate_execution(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    planner = AnalysisAndDesignRuntime()
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    control_check = RestoringWorkspaceControlCheck()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=planner,
        analyst_control_check=control_check,
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    degraded = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert degraded.status == "CONTROL_DEGRADED"
    assert planner.design_calls == 0

    resumed = await lifecycle.resume("work-1", project_id="project-a")

    assert resumed.status == "READY_TO_MERGE"
    assert planner.design_calls == 1
    assert planner.design_requests[0].run_id == "work-1:design:1"
    assert implementer.calls == 1
    events = await work_store.read_events("work-1", project_id="project-a")
    control_events = [
        event.event_type
        for event in events
        if event.event_type
        in {WorkEventType.CONTROL_DEGRADED, WorkEventType.CONTROL_RESTORED}
    ]
    assert control_events == [
        WorkEventType.CONTROL_DEGRADED,
        WorkEventType.CONTROL_RESTORED,
    ]
    completed_design = [
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("run_id") == "work-1:design:1"
    ]
    recorded_design = [
        event
        for event in events
        if event.event_type is WorkEventType.EXECUTION_RECORDED
        and event.payload_json.get("run_id") == "work-1:design:1"
    ]
    assert len(completed_design) == 1
    assert len(recorded_design) == 1


@pytest.mark.asyncio
async def test_design_workspace_mutation_blocks_before_implementation(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    planner = AnalysisAndDesignRuntime(mutate_during_design=True)
    implementer = MutationRuntime(implement_text="must-not-run", repair_text="fixed")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=planner,
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    assert record.status == "WORK_BLOCKED"
    assert planner.design_calls == 1
    assert implementer.calls == 0
    events = await work_store.read_events("work-1", project_id="project-a")
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert blocker.payload_json["reason"] == "designer_changed_workspace"


@pytest.mark.asyncio
async def test_design_unsupported_claim_blocks_before_implementation(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    planner = AnalysisAndDesignRuntime(
        design_claims=(
            ClassifiedClaim(
                classification=ClaimClassification.UNKNOWN,
                statement="A compatibility fallback may be required",
                kind="compatibility",
                evidence_refs=(),
                confidence="low",
                impact_if_wrong="high",
            ),
        )
    )
    implementer = MutationRuntime(implement_text="must-not-run", repair_text="fixed")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=planner,
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    assert record.status == "WORK_BLOCKED"
    assert implementer.calls == 0
    events = await work_store.read_events("work-1", project_id="project-a")
    assumption_event = next(
        event for event in events if event.event_type is WorkEventType.ASSUMPTION_RECORDED
    )
    assert assumption_event.payload_json["statement"] == (
        "A compatibility fallback may be required"
    )
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert blocker.payload_json["reason"] == "design_assumption_unresolved"
    questions = await knowledge_store.search(
        KnowledgeQuery(
            text="compatibility fallback",
            project_id="project-a",
            work_id="work-1",
        )
    )
    assert [item.kind for item in questions] == [KnowledgeKind.QUESTION]


@pytest.mark.asyncio
async def test_analysis_records_high_impact_unknown_and_blocks_before_implementation(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    analyzer = AnalysisRuntime(
        claims=(
            ClassifiedClaim(
                classification=ClaimClassification.FACT,
                statement="The repository base is pinned",
                kind="repository_state",
                evidence_refs=(f"git://{base_sha}",),
                confidence="high",
                impact_if_wrong="medium",
            ),
            ClassifiedClaim(
                classification=ClaimClassification.UNKNOWN,
                statement="A compatibility fallback is required",
                kind="compatibility",
                evidence_refs=(),
                confidence="low",
                impact_if_wrong="high",
            ),
        )
    )
    implementer = MutationRuntime(implement_text="must-not-run", repair_text="fixed")
    reviewer = ReviewRuntime("accept")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        analyzer=analyzer,
        implementer=implementer,
        reviewer=reviewer,
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    assert record.status == "WORK_BLOCKED"
    assert analyzer.calls == 1
    assert implementer.calls == reviewer.calls == 0
    events = await work_store.read_events("work-1", project_id="project-a")
    assumption_event = next(
        event for event in events if event.event_type is WorkEventType.ASSUMPTION_RECORDED
    )
    assumption = Assumption.model_validate(assumption_event.payload_json)
    assert assumption.status == "open"
    assert assumption.statement == "A compatibility fallback is required"
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert blocker.payload_json["assumption_id"] == assumption.id
    assert not any(
        event.event_type is WorkEventType.CONTRACT_ACCEPTED for event in events
    )

    facts = await knowledge_store.search(
        KnowledgeQuery(text="repository pinned", project_id="project-a", work_id="work-1")
    )
    questions = await knowledge_store.search(
        KnowledgeQuery(text="compatibility fallback", project_id="project-a", work_id="work-1")
    )
    assert [(item.kind, item.factness_score) for item in facts] == [(KnowledgeKind.FACT, 100)]
    assert [(item.kind, item.factness_score) for item in questions] == [(KnowledgeKind.QUESTION, 0)]


@pytest.mark.asyncio
async def test_evidence_free_fact_is_an_open_assumption_not_verified_fact(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    analyzer = AnalysisRuntime(
        claims=(
            ClassifiedClaim(
                classification=ClaimClassification.FACT,
                statement="An undocumented migration is required",
                kind="migration",
                evidence_refs=(),
                confidence="high",
                impact_if_wrong="high",
            ),
        )
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        analyzer=analyzer,
        implementer=MutationRuntime(implement_text="must-not-run", repair_text="fixed"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    assert record.status == "WORK_BLOCKED"
    events = await work_store.read_events("work-1", project_id="project-a")
    assumption_event = next(
        event for event in events if event.event_type is WorkEventType.ASSUMPTION_RECORDED
    )
    assumption = Assumption.model_validate(assumption_event.payload_json)
    assert assumption.statement == "An undocumented migration is required"
    assert assumption.status == "open"
    assert not any(
        event.event_type is WorkEventType.CONTRACT_ACCEPTED for event in events
    )
    items = await knowledge_store.search(
        KnowledgeQuery(text="undocumented migration", project_id="project-a", work_id="work-1")
    )
    assert [(item.kind, item.factness_score) for item in items] == [
        (KnowledgeKind.FACT, 0)
    ]


@pytest.mark.asyncio
async def test_evidence_free_high_impact_inference_blocks_before_implementation(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    implementer = MutationRuntime(implement_text="must-not-run", repair_text="fixed")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=AnalysisRuntime(
            claims=(
                ClassifiedClaim(
                    classification=ClaimClassification.INFERENCE,
                    statement="A compatibility adapter is probably required",
                    kind="compatibility",
                    evidence_refs=(),
                    confidence="low",
                    impact_if_wrong="high",
                ),
            )
        ),
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    assert record.status == "WORK_BLOCKED"
    assert implementer.calls == 0
    events = await work_store.read_events("work-1", project_id="project-a")
    assumption_event = next(
        event for event in events if event.event_type is WorkEventType.ASSUMPTION_RECORDED
    )
    assert assumption_event.payload_json["statement"] == (
        "A compatibility adapter is probably required"
    )
    assert not any(
        event.event_type is WorkEventType.CONTRACT_ACCEPTED for event in events
    )
    items = await knowledge_store.search(
        KnowledgeQuery(text="compatibility adapter", project_id="project-a", work_id="work-1")
    )
    assert [(item.kind, item.factness_score) for item in items] == [
        (KnowledgeKind.INFERENCE, 0)
    ]


@pytest.mark.asyncio
async def test_evidence_free_high_impact_requirement_blocks_before_implementation(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    implementer = MutationRuntime(implement_text="must-not-run", repair_text="fixed")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=AnalysisRuntime(
            claims=(
                ClassifiedClaim(
                    classification=ClaimClassification.REQUIREMENT,
                    statement="A migration path is required",
                    kind="migration",
                    evidence_refs=(),
                    confidence="high",
                    impact_if_wrong="high",
                ),
            )
        ),
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    assert record.status == "WORK_BLOCKED"
    assert implementer.calls == 0
    events = await work_store.read_events("work-1", project_id="project-a")
    assumption_event = next(
        event for event in events if event.event_type is WorkEventType.ASSUMPTION_RECORDED
    )
    assert assumption_event.payload_json["statement"] == "A migration path is required"
    assert not any(
        event.event_type is WorkEventType.CONTRACT_ACCEPTED for event in events
    )


@pytest.mark.asyncio
async def test_missing_analysis_result_blocks_with_pending_attention(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    analyzer = MissingAnalysisResultRuntime()
    implementer = MutationRuntime(implement_text="must-not-run", repair_text="fixed")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        analyzer=analyzer,
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    assert record.status == "WORK_BLOCKED"
    assert analyzer.calls == 1
    assert implementer.calls == 0
    events = await work_store.read_events("work-1", project_id="project-a")
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert blocker.payload_json["reason"] == "analysis_result_missing"
    pending = await work_store.pending_attention(project_id="project-a")
    assert [(item.work_id, item.kind.value) for item in pending] == [
        ("work-1", "WORK_BLOCKED")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("proposed_scope", [".", "./", "././", ".//"])
async def test_invalid_analysis_proposal_blocks_instead_of_wedging_work(
    stores,
    tmp_path: Path,
    proposed_scope: str,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    analyzer = AnalysisRuntime(
        proposal=WorkContractProposal(
            goal="Change target deterministically",
            allowed_scope=(proposed_scope,),
            acceptance_criteria=(
                ProposedAcceptanceCriterion(
                    statement="deterministic verification passes",
                    verification_kind="deterministic",
                ),
            ),
            constraints=(),
            non_goals=(),
            risk="low",
            design_required=False,
        )
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=analyzer,
        implementer=MutationRuntime(implement_text="must-not-run", repair_text="fixed"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    assert record.status == "WORK_BLOCKED"
    events = await work_store.read_events("work-1", project_id="project-a")
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert blocker.payload_json["reason"] == "analysis_result_invalid"
    assert blocker.payload_json["violations"] == [
        f"analysis contract scope is not surgical: {proposed_scope}"
    ]
    assert not any(
        event.event_type is WorkEventType.CONTRACT_ACCEPTED for event in events
    )


@pytest.mark.asyncio
async def test_resume_analysis_uses_completed_durable_result_without_rerunning_operator(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    failing_store = FailingOnceKnowledgeStore(knowledge_store, fail_on_publish=2)
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    analyzer = AnalysisRuntime(
        claims=(
            ClassifiedClaim(
                classification=ClaimClassification.FACT,
                statement="The repository base is pinned",
                kind="repository_state",
                evidence_refs=(f"git://{base_sha}",),
                confidence="high",
                impact_if_wrong="medium",
            ),
            ClassifiedClaim(
                classification=ClaimClassification.DECISION,
                statement="Only target.txt is in scope",
                kind="scope",
                evidence_refs=(),
                confidence="high",
                impact_if_wrong="medium",
            ),
        )
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=failing_store,
        durability=durability,
        analyzer=analyzer,
        implementer=MutationRuntime(implement_text="initial", repair_text="fixed"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    with pytest.raises(RuntimeError, match="knowledge persistence interruption"):
        await lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))

    interrupted = await work_store.load_work("work-1", project_id="project-a")
    assert interrupted is not None
    assert interrupted.status == "ANALYZING"
    assert analyzer.calls == 1
    original = await knowledge_store.get(
        "work-1:analysis:1:claim:1",
        project_id="project-a",
    )
    assert original is not None

    resumed = await lifecycle.resume("work-1", project_id="project-a")

    assert resumed.status == "READY_TO_MERGE"
    assert analyzer.calls == 1
    facts = await knowledge_store.search(
        KnowledgeQuery(text="repository pinned", project_id="project-a", work_id="work-1")
    )
    assert len(facts) == 1
    persisted = await knowledge_store.get(original.id, project_id="project-a")
    assert persisted is not None
    assert persisted.created_at == original.created_at
    decisions = await knowledge_store.search(
        KnowledgeQuery(text="target scope", project_id="project-a", work_id="work-1")
    )
    assert [item.kind for item in decisions] == [KnowledgeKind.DECISION]


@pytest.mark.asyncio
async def test_conflicting_analysis_knowledge_blocks_on_resume(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    failing_store = FailingOnceKnowledgeStore(knowledge_store, fail_on_publish=2)
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    claims = (
        ClassifiedClaim(
            classification=ClaimClassification.FACT,
            statement="The repository base is pinned",
            kind="repository_state",
            evidence_refs=(f"git://{base_sha}",),
            confidence="high",
            impact_if_wrong="medium",
        ),
        ClassifiedClaim(
            classification=ClaimClassification.DECISION,
            statement="Only target.txt is in scope",
            kind="scope",
            evidence_refs=(),
            confidence="high",
            impact_if_wrong="medium",
        ),
    )
    first_analyzer = AnalysisRuntime(claims=claims)
    first = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=failing_store,
        durability=durability,
        analyzer=first_analyzer,
        implementer=MutationRuntime(implement_text="must-not-run", repair_text="fixed"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )
    with pytest.raises(RuntimeError, match="knowledge persistence interruption"):
        await first.start(work_item=_work_item(), contract=_contract(base_sha))

    resumed_analyzer = AnalysisRuntime(claims=claims)
    resumed = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        analyzer=resumed_analyzer,
        analyst_actor="operator:replacement-analyst",
        implementer=MutationRuntime(implement_text="must-not-run", repair_text="fixed"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await resumed.resume("work-1", project_id="project-a")

    assert record.status == "WORK_BLOCKED"
    assert first_analyzer.calls == 1
    assert resumed_analyzer.calls == 0
    events = await work_store.read_events("work-1", project_id="project-a")
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert blocker.payload_json["reason"] == "analysis_knowledge_conflict"


@pytest.mark.asyncio
async def test_false_positive_contract_drift_revalidates_without_rerunning_operators(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    analyzer = AnalysisRuntime()
    implementer = MutationRuntime(implement_text="initial", repair_text="unused")
    validator = BlockingOnceSoftwareResultValidator()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        analyzer=analyzer,
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
        implementer_validator=validator,
    )

    blocked = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )
    durable = await durability.load_run(
        "work:work-1:implement",
        "work-1:implement:1",
        project_id="project-a",
    )

    assert blocked.status == "WORK_BLOCKED"
    assert durable is not None
    assert durable.status is StepStatus.FAILED
    assert durable.output_data["status"] == "passed"

    resumed = await lifecycle.resume("work-1", project_id="project-a")

    assert resumed.status == "READY_TO_MERGE"
    assert analyzer.calls == 1
    assert implementer.calls == 1
    assert validator.calls == 2
    events = await work_store.read_events("work-1", project_id="project-a")
    assert [
        event.payload_json["status"]
        for event in events
        if event.event_type is WorkEventType.EXECUTION_RECORDED
        and event.payload_json.get("run_id") == "work-1:implement:1"
    ] == ["blocked", "passed"]
    assert sum(
        event.event_type is WorkEventType.STAGE_STARTED
        and event.payload_json.get("run_id") == "work-1:implement:1"
        for event in events
    ) == 1


@pytest.mark.asyncio
async def test_analysis_narrows_draft_scope_and_out_of_scope_change_is_rejected(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    analyzer = AnalysisRuntime()
    implementer = OutOfScopeMutationRuntime(
        implement_text="unused",
        repair_text="unused",
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        analyzer=analyzer,
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha, allowed_scope=(".",)),
    )
    resumed = await lifecycle.resume("work-1", project_id="project-a")

    assert record.status == resumed.status == "WORK_BLOCKED"
    assert analyzer.calls == 1
    assert implementer.calls == 1
    events = await work_store.read_events("work-1", project_id="project-a")
    proposed_event = next(
        event
        for event in events
        if event.event_type is WorkEventType.CONTRACT_PROPOSED
        and event.actor_ref == "operator:analyst"
    )
    accepted_event = next(
        event for event in events if event.event_type is WorkEventType.CONTRACT_ACCEPTED
    )
    proposed = WorkContract.model_validate(proposed_event.payload_json)
    accepted = WorkContract.model_validate(accepted_event.payload_json)
    assert proposed.allowed_scope == ("target.txt",)
    assert proposed.acceptance_criteria
    assert proposed.risk == "low"
    assert proposed_event.sequence < accepted_event.sequence
    assert accepted.allowed_scope == ("target.txt",)
    assert accepted.allowed_scope != (".",)
    reports = [
        OperatorDisciplineReport.model_validate(event.payload_json)
        for event in events
        if event.event_type is WorkEventType.OPERATOR_DISCIPLINE_RECORDED
    ]
    assert any(
        "outside.txt is outside allowed targets" in report.scope_violations for report in reports
    )
    blocker = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert blocker.payload_json == {
        "reason": "contract_drift",
        "run_id": "work-1:implement:1",
        "accepted_contract_version": accepted.version,
        "required_contract_version": accepted.version + 1,
        "violations": ["outside.txt is outside allowed targets"],
        "decision_request": "Create and accept a new WorkContract version or stop the work.",
        "evidence_refs": ["runtime://work-1:implement:1"],
    }
    assert accepted.version == 2
    assert not any(
        event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "implement"
        for event in events
    )
    assert not any(
        event.event_type in {WorkEventType.VERIFICATION_RECORDED, WorkEventType.REVIEW_RECORDED}
        for event in events
    )


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
                head_sha="b" * 40,
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
        repository_outcome=SoftwareRepositoryOutcome.MERGED,
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
    assert delivered.status == "COMPLETE"
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
async def test_failed_action_receipt_uses_canonical_verification_transition(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    profile = RecordingSoftwareProfile()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        analyzer=_profile_analysis_runtime(),
        profile=profile,
        implementer=PassedWithFailedActionReceiptRuntime(
            implement_text="initial", repair_text="unused"
        ),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert record.status == "READY_TO_MERGE"
    assert profile.verify_calls == 2
    events = await work_store.read_events("work-1", project_id="project-a")
    execution = next(
        event
        for event in events
        if event.event_type is WorkEventType.EXECUTION_RECORDED
        and event.payload_json["run_id"] == "work-1:implement:1"
    )
    assert execution.payload_json["status"] == "passed"
    verifications = [
        VerificationResult.model_validate(event.payload_json)
        for event in events
        if event.event_type is WorkEventType.VERIFICATION_RECORDED
    ]
    assert [result.passed for result in verifications] == [False, True]
    attempt = execution_attempt_from_events(events, "work-1:implement:1")
    assert attempt is not None
    assert attempt.status == "passed"


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
        "run_id": "work-1:implement:3",
        "attempts": 3,
        "decision_request": "Inspect the failed implementation evidence and decide whether to retry or stop the work.",
        "evidence_refs": ["runtime://implement-failure"],
    }


@pytest.mark.asyncio
async def test_failed_implementation_without_action_receipt_is_not_contract_drift(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=FailedWithoutActionReceiptRuntime(
            implement_text="failed",
            repair_text="unused",
        ),
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
        "attempts": 1,
        "decision_request": "Inspect the failed implementation evidence and decide whether to retry or stop the work.",
        "evidence_refs": ["runtime://native-failure"],
    }


@pytest.mark.asyncio
async def test_resume_retries_failed_implementation_without_rerunning_analysis(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    analyzer = AnalysisRuntime()
    implementer = FailingOnceWithoutActionReceiptRuntime(
        implement_text="initial",
        repair_text="unused",
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=analyzer,
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    blocked = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )
    resumed = await lifecycle.resume("work-1", project_id="project-a")

    assert blocked.status == "WORK_BLOCKED"
    assert resumed.status == "READY_TO_MERGE"
    assert analyzer.calls == 1
    assert implementer.calls == 2
    assert [request.run_id for request in implementer.requests] == [
        "work-1:implement:1",
        "work-1:implement:2",
    ]
    events = await work_store.read_events("work-1", project_id="project-a")
    completed = [
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "implement"
    ]
    assert [event.payload_json["run_id"] for event in completed] == [
        "work-1:implement:2"
    ]


@pytest.mark.asyncio
async def test_repeated_failed_implementation_records_each_attempt(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    analyzer = AnalysisRuntime()
    implementer = FailedWithoutActionReceiptRuntime(
        implement_text="failed",
        repair_text="unused",
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=analyzer,
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    first = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )
    second = await lifecycle.resume("work-1", project_id="project-a")

    assert first.status == "WORK_BLOCKED"
    assert second.status == "WORK_BLOCKED"
    assert analyzer.calls == 1
    assert implementer.calls == 2
    assert [request.run_id for request in implementer.requests] == [
        "work-1:implement:1",
        "work-1:implement:2",
    ]
    events = await work_store.read_events("work-1", project_id="project-a")
    blockers = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.WORK_BLOCKED
    ]
    assert [blocker["run_id"] for blocker in blockers] == [
        "work-1:implement:1",
        "work-1:implement:2",
    ]
    assert [blocker["reason"] for blocker in blockers] == [
        "implement_failed",
        "implement_failed",
    ]


@pytest.mark.asyncio
async def test_later_review_blocker_prevents_stale_implementation_retry(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    analyzer = AnalysisRuntime()
    implementer = FailingOnceWithoutActionReceiptRuntime(
        implement_text="initial",
        repair_text="unused",
    )
    reviewer = ReviewRuntime("blocked")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=analyzer,
        implementer=implementer,
        reviewer=reviewer,
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    first = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )
    second = await lifecycle.resume("work-1", project_id="project-a")
    unchanged = await lifecycle.resume("work-1", project_id="project-a")

    assert first.status == "WORK_BLOCKED"
    assert second.status == "WORK_BLOCKED"
    assert unchanged == second
    assert analyzer.calls == 1
    assert implementer.calls == 2
    assert reviewer.calls == 1
    events = await work_store.read_events("work-1", project_id="project-a")
    blockers = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.WORK_BLOCKED
    ]
    assert [blocker["reason"] for blocker in blockers] == [
        "implement_failed",
        "review_blocked",
    ]


@pytest.mark.asyncio
async def test_failed_implementation_resume_backfills_interrupted_execution_event(
    stores,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    analyzer = AnalysisRuntime()
    implementer = FailingOnceWithoutActionReceiptRuntime(
        implement_text="initial",
        repair_text="unused",
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=analyzer,
        implementer=implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )
    original_append = work_store.append_event
    interrupted = False

    async def interrupt_execution_event(event: WorkEvent) -> None:
        nonlocal interrupted
        if (
            not interrupted
            and event.event_type is WorkEventType.EXECUTION_RECORDED
            and event.payload_json.get("run_id") == "work-1:implement:1"
        ):
            interrupted = True
            raise RuntimeError("simulated execution-event persistence interruption")
        await original_append(event)

    monkeypatch.setattr(work_store, "append_event", interrupt_execution_event)
    with pytest.raises(
        RuntimeError,
        match="simulated execution-event persistence interruption",
    ):
        await lifecycle.start(
            work_item=_work_item(),
            contract=_contract(base_sha),
        )

    monkeypatch.setattr(work_store, "append_event", original_append)
    blocked = await lifecycle.resume("work-1", project_id="project-a")
    resumed = await lifecycle.resume("work-1", project_id="project-a")

    assert blocked.status == "WORK_BLOCKED"
    assert resumed.status == "READY_TO_MERGE"
    assert analyzer.calls == 1
    assert implementer.calls == 2
    assert [request.run_id for request in implementer.requests] == [
        "work-1:implement:1",
        "work-1:implement:2",
    ]
    events = await work_store.read_events("work-1", project_id="project-a")
    executions = [
        event.payload_json
        for event in events
        if event.event_type is WorkEventType.EXECUTION_RECORDED
        and str(event.payload_json.get("run_id", "")).startswith("work-1:implement:")
    ]
    assert [execution["run_id"] for execution in executions] == [
        "work-1:implement:1",
        "work-1:implement:2",
    ]
    assert [execution["status"] for execution in executions] == [
        "blocked",
        "passed",
    ]


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
async def test_invalid_review_result_blocks_without_recording_review(
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
        reviewer=InvalidReviewRuntime(),
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
    assert blocker.payload_json["reason"] == "review_result_invalid"
    assert blocker.payload_json["run_id"] == "work-1:review:1"
    assert blocker.payload_json["evidence_refs"] == ["runtime://work-1:review:1"]
    assert not any(event.event_type is WorkEventType.REVIEW_RECORDED for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reviewer", "error"),
    [
        (WrongAttemptReviewRuntime(), "review result belongs to a different attempt"),
        (ForeignProjectReviewRuntime(), "review result belongs to a different project"),
        (
            ForeignFindingContextReviewRuntime(),
            "review finding context belongs to a different project",
        ),
        (InvalidFindingContextReviewRuntime(), "Extra inputs are not permitted"),
    ],
)
async def test_review_integrity_errors_remain_hard_failures(
    stores,
    tmp_path: Path,
    reviewer,
    error: str,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=reviewer,
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    with pytest.raises(ValueError, match=error):
        await lifecycle.start(
            work_item=_work_item(),
            contract=_contract(base_sha),
        )

    events = await work_store.read_events("work-1", project_id="project-a")
    assert not any(event.event_type is WorkEventType.WORK_BLOCKED for event in events)


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
    artifact_store = LocalArtifactStore(root=tmp_path / "objects")
    large_initial = "initial-" * 1000
    implementer = MutationRuntime(implement_text=large_initial, repair_text="fixed")
    repairer = DiffReadingMutationRuntime(
        implement_text="unused",
        repair_text="fixed",
        artifact_store=artifact_store,
    )
    reviewer = DiffReadingReviewRuntime(artifact_store, "repair", "accept")
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
    repair_precondition = repairer.requests[0].control_preconditions[0]
    assert repair_precondition.kind is ControlPreconditionKind.WORKSPACE
    assert repair_precondition.required_for == ("design", "implement", "repair", "review")
    repair_context = SoftwareRepairContext.model_validate(repairer.capsules[0].profile_context)
    first_review_context = SoftwareReviewContext.model_validate(
        reviewer.capsules[0].profile_context
    )
    assert repair_context.diff is None
    assert first_review_context.diff is None
    assert repair_context.diff_artifact.storage_ref == (
        first_review_context.diff_artifact.storage_ref
    )
    assert repair_context.diff_artifact.media_type == "text/x-diff"
    independent_store = LocalArtifactStore(root=tmp_path / "objects")
    raw_diff = independent_store.read(
        repair_context.diff_artifact.storage_ref, project_id="project-a"
    )
    assert b"initial" in raw_diff
    assert repair_context.diff_artifact.size_bytes == len(raw_diff)
    assert reviewer.materialized_diffs[0] == raw_diff
    assert repairer.materialized_diffs == [raw_diff]
    assert all(not path.exists() for path in reviewer.materialized_paths)
    assert all(not path.exists() for path in repairer.materialized_paths)
    assert "initial" not in json.dumps(repairer.capsules[0].model_dump(mode="json"))
    events = await work_store.read_events("work-1", project_id="project-a")
    review_runs = [
        event.payload_json["run_id"]
        for event in events
        if event.event_type is WorkEventType.RUNTIME_SELECTED
        and event.payload_json["stage"] == "review"
    ]
    assert review_runs == ["work-1:review:1", "work-1:review:2"]
    repair_started = next(
        event
        for event in events
        if event.event_type is WorkEventType.STAGE_STARTED
        and event.payload_json["stage"] == "repair"
    )
    assert repair_started.payload_json["artifact_bytes_referenced"] == len(raw_diff)
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
    await work_store.save_work(ready.model_copy(update={"status": "TRIAGING"}))

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
async def test_profile_verification_crash_resumes_persisted_execution(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    first_implementer = MutationRuntime(implement_text="initial", repair_text="unused")
    first_profile = CrashingSoftwareProfile()
    first = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        analyzer=_profile_analysis_runtime(),
        profile=first_profile,
        implementer=first_implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    with pytest.raises(RuntimeError, match="profile verification restart"):
        await first.start(work_item=_work_item(), contract=_contract(base_sha))

    interrupted_events = await work_store.read_events("work-1", project_id="project-a")
    assert sum(
        event.event_type is WorkEventType.EXECUTION_RECORDED
        and event.payload_json.get("run_id") == "work-1:implement:1"
        for event in interrupted_events
    ) == 1
    assert not any(
        event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("run_id") == "work-1:implement:1"
        for event in interrupted_events
    )

    resumed_implementer = MutationRuntime(implement_text="must-not-run", repair_text="unused")
    resumed_profile = RecordingSoftwareProfile()
    resumed = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        profile=resumed_profile,
        implementer=resumed_implementer,
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
    )

    record = await resumed.resume("work-1", project_id="project-a")

    assert record.status == "READY_TO_MERGE"
    assert first_implementer.calls == 1
    assert resumed_implementer.calls == 0
    assert first_profile.verify_calls == 1
    assert resumed_profile.verify_calls == 1
    events = await work_store.read_events("work-1", project_id="project-a")
    assert sum(
        event.event_type is WorkEventType.EXECUTION_RECORDED
        and event.payload_json.get("run_id") == "work-1:implement:1"
        for event in events
    ) == 1
    assert sum(
        event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("run_id") == "work-1:implement:1"
        for event in events
    ) == 1
    assert sum(
        event.event_type is WorkEventType.VERIFICATION_RECORDED
        and event.payload_json.get("attempt_id") == "work-1:implement:1"
        for event in events
    ) == 1


@pytest.mark.asyncio
async def test_restart_resume_does_not_rerun_completed_implementation(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    first_implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    first_profile = RecordingSoftwareProfile()
    first = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        profile=first_profile,
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
    resumed_profile = RecordingSoftwareProfile()
    resumed_reviewer = ReviewRuntime("accept")
    resumed = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        profile=resumed_profile,
        implementer=resumed_implementer,
        reviewer=resumed_reviewer,
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    record = await resumed.resume("work-1", project_id="project-a")

    assert record.status == "READY_TO_MERGE"
    assert first_implementer.calls == 1
    assert resumed_implementer.calls == 0
    assert first_profile.verify_calls == 0
    assert resumed_profile.verify_calls == 0
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
async def test_foreign_project_verification_check_is_rejected_before_persistence(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="initial", repair_text="unused"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_always_pass_command(),),
        verifier=ForeignProjectCheckVerifier(
            SoftwareVerifier(
                knowledge_store=knowledge_store,
                runner=LocalVerificationRunner(),
            )
        ),
    )

    with pytest.raises(
        ValueError, match="verification check belongs to a different project"
    ):
        await lifecycle.start(
            work_item=_work_item(),
            contract=_contract(base_sha),
        )

    events = await work_store.read_events("work-1", project_id="project-a")
    assert not any(
        event.event_type is WorkEventType.VERIFICATION_RECORDED for event in events
    )


@pytest.mark.asyncio
async def test_verification_isolation_restores_without_rerunning_implementation(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    reviewer = ReviewRuntime("accept")
    repairer = MutationRuntime(implement_text="unused", repair_text="fixed")
    verifier = RestoringIsolationVerifier(
        SoftwareVerifier(
            knowledge_store=knowledge_store,
            runner=LocalVerificationRunner(),
        )
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=implementer,
        reviewer=reviewer,
        repairer=repairer,
        commands=(_command("initial"),),
        verifier=verifier,
    )

    degraded = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert degraded.status == "CONTROL_DEGRADED"
    assert implementer.calls == 1
    assert verifier.calls == 1
    assert repairer.calls == 0
    assert reviewer.calls == 0

    resumed = await lifecycle.resume("work-1", project_id="project-a")

    assert resumed.status == "READY_TO_MERGE"
    assert implementer.calls == 1
    assert verifier.calls == 2
    assert reviewer.calls == 1
    assert repairer.calls == 0
    events = await work_store.read_events("work-1", project_id="project-a")
    control_events = [
        event
        for event in events
        if event.event_type
        in {WorkEventType.CONTROL_DEGRADED, WorkEventType.CONTROL_RESTORED}
    ]
    assert [event.event_type for event in control_events] == [
        WorkEventType.CONTROL_DEGRADED,
        WorkEventType.CONTROL_RESTORED,
    ]
    assert control_events[0].payload_json["failed_preconditions"] == [
        SOFTWARE_VERIFICATION_ISOLATION_PRECONDITION_ID
    ]
    assert control_events[1].payload_json["precondition_ids"] == [
        SOFTWARE_VERIFICATION_ISOLATION_PRECONDITION_ID
    ]



@pytest.mark.asyncio
async def test_restored_workspace_resumes_review_without_rerunning_completed_stages(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    durability = InMemoryStore()
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    reviewer = ReviewRuntime("accept")
    verifier = MoveHeadAfterVerifier(
        SoftwareVerifier(knowledge_store=knowledge_store, runner=LocalVerificationRunner())
    )
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=durability,
        implementer=implementer,
        reviewer=reviewer,
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
        verifier=verifier,
    )

    degraded = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert degraded.status == "CONTROL_DEGRADED"
    assert implementer.calls == 1
    assert verifier.calls == 1
    assert reviewer.calls == 0

    workspace = tmp_path / "worktrees" / "project-a" / "work-1" / "workspace"
    subprocess.run(
        ("git", "-C", str(workspace), "checkout", "--detach", "-q", base_sha),
        check=True,
    )
    resumed = await lifecycle.resume("work-1", project_id="project-a")

    assert resumed.status == "READY_TO_MERGE"
    assert implementer.calls == 1
    assert verifier.calls == 1
    assert reviewer.calls == 1
    events = await work_store.read_events("work-1", project_id="project-a")
    control_events = [
        event.event_type
        for event in events
        if event.event_type
        in {WorkEventType.CONTROL_DEGRADED, WorkEventType.CONTROL_RESTORED}
    ]
    assert control_events == [
        WorkEventType.CONTROL_DEGRADED,
        WorkEventType.CONTROL_RESTORED,
    ]


@pytest.mark.asyncio
async def test_missing_workspace_before_review_degrades_before_capsule_read(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    reviewer = ReviewRuntime("accept")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="initial", repair_text="fixed"),
        reviewer=reviewer,
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
        verifier=RemoveWorkspaceAfterVerifier(
            SoftwareVerifier(
                knowledge_store=knowledge_store,
                runner=LocalVerificationRunner(),
            )
        ),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert record.status == "CONTROL_DEGRADED"
    assert reviewer.calls == 0
    events = await work_store.read_events("work-1", project_id="project-a")
    degraded = next(
        event for event in events if event.event_type is WorkEventType.CONTROL_DEGRADED
    )
    assert degraded.payload_json["stage"] == "review"
    assert degraded.payload_json["details"] == (
        "software-workspace: recorded workspace does not exist"
    )
    still_degraded = await lifecycle.resume("work-1", project_id="project-a")
    assert still_degraded.status == "CONTROL_DEGRADED"
    events = await work_store.read_events("work-1", project_id="project-a")
    assert sum(
        event.event_type is WorkEventType.CONTROL_DEGRADED for event in events
    ) == 1


@pytest.mark.asyncio
async def test_missing_workspace_before_design_degrades_before_capsule_read(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    worktree_root = tmp_path / "worktrees"
    designer = AnalysisAndDesignRuntime()
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=worktree_root,
        work_store=RemoveWorkspaceOnStatusStore(
            work_store,
            status="DESIGNING",
            workspace_path=worktree_root / "project-a" / "work-1" / "workspace",
        ),
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        analyzer=AnalysisAndDesignRuntime(),
        designer_ladder=(designer,),
        implementer=MutationRuntime(implement_text="initial", repair_text="fixed"),
        reviewer=ReviewRuntime("accept"),
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert record.status == "CONTROL_DEGRADED"
    assert designer.design_calls == 0
    events = await work_store.read_events("work-1", project_id="project-a")
    degraded = next(
        event for event in events if event.event_type is WorkEventType.CONTROL_DEGRADED
    )
    assert degraded.payload_json["stage"] == "design"
    assert degraded.payload_json["details"] == (
        "software-workspace: recorded workspace does not exist"
    )
    still_degraded = await lifecycle.resume("work-1", project_id="project-a")
    assert still_degraded.status == "CONTROL_DEGRADED"
    events = await work_store.read_events("work-1", project_id="project-a")
    assert sum(
        event.event_type is WorkEventType.CONTROL_DEGRADED for event in events
    ) == 1


@pytest.mark.asyncio
async def test_missing_workspace_before_repair_degrades_before_capsule_read(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    repairer = MutationRuntime(implement_text="unused", repair_text="fixed")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=MutationRuntime(implement_text="initial", repair_text="fixed"),
        reviewer=ReviewRuntime("accept"),
        repairer=repairer,
        commands=(_always_fail_command(),),
        verifier=RemoveWorkspaceAfterVerifier(
            SoftwareVerifier(
                knowledge_store=knowledge_store,
                runner=LocalVerificationRunner(),
            )
        ),
    )

    record = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert record.status == "CONTROL_DEGRADED"
    assert repairer.calls == 0
    events = await work_store.read_events("work-1", project_id="project-a")
    degraded = next(
        event for event in events if event.event_type is WorkEventType.CONTROL_DEGRADED
    )
    assert degraded.payload_json["stage"] == "repair"
    assert degraded.payload_json["frozen_action_ids"] == ["work-1:repair:1:change"]


@pytest.mark.asyncio
async def test_inflight_review_workspace_loss_restores_without_replaying_prior_stages(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, base_sha = _repository(tmp_path)
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    reviewer = HideWorkspaceDuringFirstReviewRuntime("accept")
    lifecycle = _lifecycle(
        repository=repository,
        worktree_root=tmp_path / "worktrees",
        work_store=work_store,
        knowledge_store=knowledge_store,
        durability=InMemoryStore(),
        implementer=implementer,
        reviewer=reviewer,
        repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
        commands=(_command("initial"),),
    )

    degraded = await lifecycle.start(
        work_item=_work_item(),
        contract=_contract(base_sha),
    )

    assert degraded.status == "CONTROL_DEGRADED"
    assert implementer.calls == 1
    events = await work_store.read_events("work-1", project_id="project-a")
    degraded_event = next(
        event for event in events if event.event_type is WorkEventType.CONTROL_DEGRADED
    )
    assert degraded_event.payload_json["details"] == (
        "software-workspace: recorded workspace does not exist"
    )
    workspace = tmp_path / "worktrees" / "project-a" / "work-1" / "workspace"
    assert reviewer.hidden_path is not None
    reviewer.hidden_path.rename(workspace)
    resumed = await lifecycle.resume("work-1", project_id="project-a")

    assert resumed.status == "READY_TO_MERGE"
    assert implementer.calls == 1
    assert reviewer.calls == 2
    events = await work_store.read_events("work-1", project_id="project-a")
    control_events = [
        event.event_type
        for event in events
        if event.event_type
        in {WorkEventType.CONTROL_DEGRADED, WorkEventType.CONTROL_RESTORED}
    ]
    assert control_events == [
        WorkEventType.CONTROL_DEGRADED,
        WorkEventType.CONTROL_RESTORED,
    ]
    pending = await work_store.pending_attention(project_id="project-a")
    assert pending == ()


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
        project_id="project-a",
        statement="A fallback path is required",
        kind="compatibility",
        evidence_refs=(),
        confidence="low",
        impact_if_wrong="high",
        status="open",
    )

    with pytest.raises(ValueError, match="assumption belongs to a different project"):
        await lifecycle.start(
            work_item=_work_item(),
            contract=_contract(base_sha, assumption_ids=(assumption.id,)),
            assumptions=(assumption.model_copy(update={"project_id": "project-b"}),),
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


@pytest.mark.asyncio
async def test_negative_inline_diff_limit_is_rejected(
    stores,
    tmp_path: Path,
) -> None:
    work_store, knowledge_store = stores
    repository, _ = _repository(tmp_path)

    with pytest.raises(ValueError, match="max_inline_diff_bytes cannot be negative"):
        _lifecycle(
            repository=repository,
            worktree_root=tmp_path / "worktrees",
            work_store=work_store,
            knowledge_store=knowledge_store,
            durability=InMemoryStore(),
            implementer=MutationRuntime(implement_text="initial", repair_text="fixed"),
            reviewer=ReviewRuntime("accept"),
            repairer=MutationRuntime(implement_text="unused", repair_text="fixed"),
            commands=(_always_pass_command(),),
            max_inline_diff_bytes=-1,
        )
