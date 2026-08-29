# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Deterministic implement, verify, review, and bounded repair lifecycle."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol

from sagewai.artifacts.models import ArtifactRef
from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.work.capsule import TaskCapsuleCompiler
from sagewai.work.completion import evaluate_completion, validate_verification_result
from sagewai.work.contract import AcceptanceCriterion, WorkContract
from sagewai.work.control import OperatorController
from sagewai.work.events import (
    WorkEvent,
    WorkEventType,
    active_control_precondition_ids,
)
from sagewai.work.knowledge import KnowledgeItem, KnowledgeKind, KnowledgeStore
from sagewai.work.models import (
    ActionIntent,
    ActionScope,
    Assumption,
    ClaimClassification,
    ClassifiedClaim,
    CriterionVerification,
    OperatorDisciplineReport,
    ReviewResult,
    TaskCapsule,
    VerificationResult,
    WorkAnalysisResult,
    WorkDesignResult,
    WorkItem,
    WorkRecord,
)
from sagewai.work.profile import WorkProfile
from sagewai.work.profiles.software.models import (
    SoftwareAnalysisContext,
    SoftwareAttemptContext,
    SoftwareCapsuleContext,
    SoftwareContractContext,
    SoftwareDeliveryTriageContext,
    SoftwareDesignContext,
    SoftwareRepairContext,
    SoftwareRepositoryOutcome,
    SoftwareReviewContext,
    SoftwareReviewFindingContext,
    SoftwareWorkspace,
    WorkspaceStaleError,
)
from sagewai.work.profiles.software.scm import (
    SOFTWARE_WORKSPACE_PRECONDITION_ID,
    SoftwareWorktreeManager,
    software_workspace_precondition,
    workspace_diff,
)
from sagewai.work.profiles.software.verification import (
    SOFTWARE_VERIFICATION_ISOLATION_PRECONDITION_ID,
    VerificationIsolationError,
    _normalized_target,
)
from sagewai.work.runtime import (
    CapabilitySet,
    OperatorResult,
    OperatorRuntime,
    WorkRequest,
    Workspace,
)
from sagewai.work.store import WorkStore


def _store_diff_context(
    *,
    artifact_store: LocalArtifactStore,
    project_id: str | None,
    raw_diff: str,
    max_inline_diff_bytes: int,
) -> tuple[str | None, ArtifactRef]:
    diff_bytes = raw_diff.encode("utf-8")
    artifact = artifact_store.put_bytes(
        diff_bytes,
        media_type="text/x-diff",
        created_by="software.lifecycle",
        project_id=project_id,
    )
    inline_diff = raw_diff if len(diff_bytes) <= max_inline_diff_bytes else None
    return inline_diff, artifact


_DIFF_WORKSPACE_PATH = ".sagewai-context/diff.patch"


def _diff_workspace_target(workspace: SoftwareWorkspace, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("diff workspace path must stay inside the workspace")
    root = workspace.path.resolve()
    target = (root / Path(*relative.parts)).resolve()
    if not target.is_relative_to(root):
        raise ValueError("diff workspace path must stay inside the workspace")
    return target


@dataclass
class _DiffMaterializingRuntime:
    delegate: OperatorRuntime
    artifact_store: LocalArtifactStore
    artifact: ArtifactRef
    relative_path: str
    name: str = field(init=False)

    def __post_init__(self) -> None:
        self.name = self.delegate.name

    async def run(
        self,
        request: WorkRequest,
        capsule: TaskCapsule,
        capabilities: CapabilitySet,
        workspace: Workspace | None,
    ) -> OperatorResult:
        if not isinstance(workspace, SoftwareWorkspace):
            raise ValueError("diff materialization requires a software workspace")
        if (
            request.project_id != capsule.project_id
            or request.project_id != workspace.project_id
            or request.project_id != self.artifact.project_id
        ):
            raise ValueError("diff artifact belongs to a different project")
        target = _diff_workspace_target(workspace, self.relative_path)
        parent_existed = target.parent.exists()
        if target.exists() or target.is_symlink():
            raise WorkspaceStaleError(f"diff workspace path already exists: {self.relative_path}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with (
                self.artifact_store.resolve(
                    self.artifact.storage_ref, project_id=request.project_id
                ).open("rb") as source,
                target.open("xb") as destination,
            ):
                shutil.copyfileobj(source, destination)
            return await self.delegate.run(request, capsule, capabilities, workspace)
        finally:
            target.unlink(missing_ok=True)
            if not parent_existed:
                try:
                    target.parent.rmdir()
                except OSError:
                    pass


def expected_result_sha(events: list[WorkEvent], base_sha: str) -> str:
    """Return the latest durable workspace HEAD."""
    result_sha = base_sha
    for event in events:
        if event.event_type is WorkEventType.STAGE_COMPLETED:
            stage = event.payload_json.get("stage")
            if stage in {"implement", "repair"}:
                result_sha = str(event.payload_json["current_sha"])
            elif stage == "branch_published":
                result_sha = str(event.payload_json["branch_sha"])
        elif (
            event.event_type is WorkEventType.VERIFICATION_RECORDED
            and event.payload_json.get("stage") == "repository"
        ):
            profile_context = event.payload_json.get("profile_context", {})
            if "result_sha" in profile_context:
                result_sha = str(profile_context["result_sha"])
    return result_sha


class _Verifier(Protocol):
    async def verify(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        criterion_ids: tuple[str, ...],
        attempt_id: str,
        workspace: SoftwareWorkspace,
        commands: tuple[str, ...],
    ) -> VerificationResult: ...


@dataclass(frozen=True)
class SoftwareStageOperator:
    """Runtime, controller, and capability boundary for one lifecycle role."""

    actor_ref: str
    runtime: OperatorRuntime
    capabilities: CapabilitySet
    controller: OperatorController


class SoftwareLifecycle:
    """Drive the first software profile from contract to READY_TO_MERGE."""

    _workspace_attempt_id = "workspace"
    _max_repairs = 2

    def __init__(
        self,
        *,
        profile: WorkProfile,
        work_store: WorkStore,
        knowledge_store: KnowledgeStore,
        capsule_compiler: TaskCapsuleCompiler,
        worktree_manager: SoftwareWorktreeManager,
        verifier: _Verifier,
        repository: Path,
        analyst: SoftwareStageOperator,
        implementer: SoftwareStageOperator,
        reviewer: SoftwareStageOperator,
        repairer: SoftwareStageOperator,
        repo_instructions: tuple[str, ...],
        verification_commands: tuple[str, ...],
        artifact_store: LocalArtifactStore | None = None,
        max_inline_diff_bytes: int = 4000,
    ) -> None:
        if profile.name != "software":
            raise ValueError("software lifecycle requires the software profile")
        if reviewer.actor_ref in {implementer.actor_ref, repairer.actor_ref}:
            raise ValueError("reviewer cannot review its own result")
        if not verification_commands:
            raise ValueError("at least one verification command is required")
        if max_inline_diff_bytes < 0:
            raise ValueError("max_inline_diff_bytes cannot be negative")
        self._profile = profile
        self._work_store = work_store
        self._knowledge_store = knowledge_store
        self._capsule_compiler = capsule_compiler
        self._worktree_manager = worktree_manager
        self._verifier = verifier
        self._artifact_store = artifact_store or LocalArtifactStore()
        self._max_inline_diff_bytes = max_inline_diff_bytes
        self._repository = repository.resolve()
        self._analyst = analyst
        self._implementer = implementer
        self._reviewer = reviewer
        self._repairer = repairer
        self._repo_instructions = repo_instructions
        self._verification_commands = verification_commands

    async def start(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        assumptions: tuple[Assumption, ...] = (),
    ) -> WorkRecord:
        """Persist canonical inputs, prepare isolation, and drive the lifecycle."""
        software = self._validate_inputs(work_item, contract, assumptions)
        existing = await self._work_store.load_work(
            work_item.id,
            project_id=work_item.project_id,
        )
        if existing is not None:
            raise ValueError(f"work already exists: {work_item.id}")

        await self._append(
            work_item,
            WorkEventType.WORK_CREATED,
            work_item.model_dump(mode="json"),
            actor_ref="local",
        )
        await self._append(
            work_item,
            WorkEventType.CONTRACT_PROPOSED,
            contract.model_dump(mode="json"),
            actor_ref="local",
        )
        for assumption in assumptions:
            await self._append(
                work_item,
                WorkEventType.ASSUMPTION_RECORDED,
                assumption.model_dump(mode="json"),
                actor_ref="local",
            )

        now = datetime.now(timezone.utc)
        record = WorkRecord(
            work_id=work_item.id,
            project_id=work_item.project_id,
            source_ref=work_item.source_ref,
            profile=work_item.profile,
            status="ANALYZING",
            contract_version=None,
            active_run_id=None,
            pending_gate=None,
            profile_context={"base_sha": software.base_sha},
            created_at=now,
            updated_at=now,
        )
        await self._work_store.save_work(record)

        project_id = work_item.project_id
        assert project_id is not None
        workspace = await self._worktree_manager.prepare(
            repository=self._repository,
            project_id=project_id,
            work_id=work_item.id,
            attempt_id=self._workspace_attempt_id,
            base_sha=software.base_sha,
        )
        analyzed = await self._run_analysis(
            work_item=work_item,
            draft_contract=contract,
            supplied_assumptions=assumptions,
            workspace=workspace,
        )
        if analyzed is None:
            blocked = await self.status(work_item.id, project_id=project_id)
            assert blocked is not None
            return blocked
        contract, assumptions = analyzed
        return await self._drive(
            work_item=work_item,
            contract=contract,
            assumptions=assumptions,
            workspace=workspace,
            state=self._post_contract_state(contract),
        )

    async def resume(
        self,
        work_id: str,
        *,
        project_id: str,
    ) -> WorkRecord:
        """Resume canonical state without rerunning completed stages."""
        record = await self._work_store.load_work(work_id, project_id=project_id)
        if record is None:
            raise KeyError(work_id)
        if record.status in {"READY_TO_MERGE", "WORK_BLOCKED", "COMPLETE"}:
            return record

        events = await self._work_store.read_events(work_id, project_id=project_id)
        if record.status == "ANALYZING":
            work_item, draft_contract, assumptions = self._analysis_inputs(events)
            software = self._validate_inputs(work_item, draft_contract, assumptions)
            workspace = await self._worktree_manager.prepare(
                repository=self._repository,
                project_id=project_id,
                work_id=work_id,
                attempt_id=self._workspace_attempt_id,
                base_sha=software.base_sha,
            )
            analyzed = await self._run_analysis(
                work_item=work_item,
                draft_contract=draft_contract,
                supplied_assumptions=assumptions,
                workspace=workspace,
            )
            if analyzed is None:
                blocked = await self.status(work_id, project_id=project_id)
                assert blocked is not None
                return blocked
            contract, assumptions = analyzed
            return await self._drive(
                work_item=work_item,
                contract=contract,
                assumptions=assumptions,
                workspace=workspace,
                state=self._post_contract_state(contract),
            )
        work_item, contract, assumptions = self._canonical_inputs(events)
        software = self._validate_inputs(work_item, contract, assumptions)
        state = self._state_from_events(events)
        if state == "COMPLETE":
            return await self._set_status(work_item, "COMPLETE")
        try:
            if state == "READY_TO_IMPLEMENT":
                workspace = await self._worktree_manager.prepare(
                    repository=self._repository,
                    project_id=project_id,
                    work_id=work_id,
                    attempt_id=self._workspace_attempt_id,
                    base_sha=software.base_sha,
                )
            else:
                workspace = await self._worktree_manager.resume(
                    repository=self._repository,
                    project_id=project_id,
                    work_id=work_id,
                    attempt_id=self._workspace_attempt_id,
                    base_sha=software.base_sha,
                    expected_sha=expected_result_sha(events, software.base_sha),
                    publish_commit_message=(
                        f"sagewai work {work_item.id}"
                        if state == "COMPLETING"
                        else None
                    ),
                )
        except WorkspaceStaleError as exc:
            stage = self._operator_stage(state)
            if stage is None:
                raise
            await self._record_workspace_degradation(
                work_item,
                stage=stage,
                run_id=self._operator_run_id(work_item, stage=stage, events=events),
                detail=str(exc),
                evidence_refs=(f"workspace://{self._workspace_attempt_id}",),
            )
            degraded = await self.status(work_id, project_id=project_id)
            assert degraded is not None
            return degraded
        return await self._drive(
            work_item=work_item,
            contract=contract,
            assumptions=assumptions,
            workspace=workspace,
            state=state,
        )

    async def status(
        self,
        work_id: str,
        *,
        project_id: str,
    ) -> WorkRecord | None:
        """Load the current project-scoped lifecycle projection."""
        return await self._work_store.load_work(work_id, project_id=project_id)

    async def _run_analysis(
        self,
        *,
        work_item: WorkItem,
        draft_contract: WorkContract,
        supplied_assumptions: tuple[Assumption, ...],
        workspace: SoftwareWorkspace,
    ) -> tuple[WorkContract, tuple[Assumption, ...]] | None:
        run_id = f"{work_item.id}:analysis:1"
        current_sha = await self._worktree_manager.current_sha(workspace)
        context = SoftwareAnalysisContext(
            software=self._software_capsule(draft_contract, current_sha),
            analysis_result_schema=WorkAnalysisResult.model_json_schema(),
        )
        capsule = await self._capsule_compiler.compile(
            work_item=work_item,
            contract=draft_contract,
            stage="analysis",
            search_text=f"{work_item.title} {work_item.description}",
            open_assumption_ids=tuple(
                item.id for item in self._open_assumptions(supplied_assumptions)
            ),
            prior_result_refs=draft_contract.evidence_refs,
            profile_context=context.model_dump(mode="json"),
        )
        request = WorkRequest(
            project_id=work_item.project_id,
            work_id=work_item.id,
            run_id=run_id,
            stage="analysis",
            action_scope=ActionScope(
                project_id=work_item.project_id,
                objective=(
                    "Ground material claims and propose the smallest sufficient software "
                    "contract"
                ),
                allowed_targets=draft_contract.allowed_scope,
                allowed_capabilities=tuple(
                    grant.name for grant in self._analyst.capabilities.grants
                ),
            ),
            action_intents=(),
            control_preconditions=(),
        )
        diff_before, files_before = await workspace_diff(workspace)
        result = await self._analyst.controller.run(
            runtime=self._analyst.runtime,
            request=request,
            capsule=capsule,
            capabilities=self._analyst.capabilities,
            workspace=workspace,
        )
        diff_after, files_after = await workspace_diff(workspace)
        if diff_after != diff_before or files_after != files_before:
            await self._block_once(
                work_item,
                {
                    "reason": "analyst_changed_workspace",
                    "run_id": run_id,
                    "decision_request": (
                        "Investigate the analysis workspace change and decide whether to "
                        "retry analysis or stop the work."
                    ),
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._analyst.actor_ref,
            )
            return None
        if result.status != "passed":
            await self._block_once(
                work_item,
                {
                    "reason": "analysis_failed",
                    "run_id": run_id,
                    "decision_request": (
                        "Inspect the failed analysis evidence and decide whether to retry "
                        "or stop the work."
                    ),
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._analyst.actor_ref,
            )
            return None

        payload = result.profile_context.get("analysis_result")
        if payload is None:
            await self._block_once(
                work_item,
                {
                    "reason": "analysis_result_missing",
                    "run_id": run_id,
                    "decision_request": "Retry analysis with the required structured result.",
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._analyst.actor_ref,
            )
            return None
        try:
            analysis = WorkAnalysisResult.model_validate(payload)
            if analysis.attempt_id != run_id:
                raise ValueError("analysis result belongs to a different attempt")
            analyzed_assumptions = tuple(
                Assumption(
                    id=f"{run_id}:assumption:{index}",
                    project_id=work_item.project_id,
                    statement=claim.statement,
                    kind=claim.kind,
                    evidence_refs=claim.evidence_refs,
                    confidence=claim.confidence,
                    impact_if_wrong=claim.impact_if_wrong,
                    status="open",
                )
                for index, claim in enumerate(analysis.claims, start=1)
                if claim.classification is ClaimClassification.UNKNOWN
                or (
                    claim.classification
                    in {
                        ClaimClassification.FACT,
                        ClaimClassification.INFERENCE,
                        ClaimClassification.REQUIREMENT,
                    }
                    and not claim.evidence_refs
                )
            )
            assumptions = tuple(
                {
                    item.id: item
                    for item in (*supplied_assumptions, *analyzed_assumptions)
                }.values()
            )
            analysis_evidence_refs = tuple(
                dict.fromkeys(
                    (
                        f"{run_id}:claim:{index}"
                        for index, claim in enumerate(analysis.claims, start=1)
                        if claim.classification
                        in {
                            ClaimClassification.FACT,
                            ClaimClassification.INFERENCE,
                            ClaimClassification.DECISION,
                            ClaimClassification.UNKNOWN,
                        }
                    ),
                )
            )
            requirement_evidence_refs = tuple(
                ref
                for claim in analysis.claims
                if claim.classification is ClaimClassification.REQUIREMENT
                for ref in claim.evidence_refs
            )
            accepted = self._accepted_analysis_contract(
                draft_contract,
                analysis,
                assumptions,
                tuple(
                    dict.fromkeys(
                        (*analysis_evidence_refs, *requirement_evidence_refs)
                    )
                ),
            )
        except ValueError as exc:
            await self._block_once(
                work_item,
                {
                    "reason": "analysis_result_invalid",
                    "run_id": run_id,
                    "violations": [str(exc)],
                    "decision_request": (
                        "Retry analysis with a valid, surgical contract proposal."
                    ),
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._analyst.actor_ref,
            )
            return None

        try:
            await self._publish_analysis_claims(
                work_item,
                analysis.claims,
                run_id=run_id,
                base_sha=workspace.base_sha,
            )
        except ValueError as exc:
            await self._block_once(
                work_item,
                {
                    "reason": "analysis_knowledge_conflict",
                    "run_id": run_id,
                    "violations": [str(exc)],
                    "decision_request": (
                        "Inspect the conflicting analysis evidence and decide whether to "
                        "retry with a new analysis attempt."
                    ),
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._analyst.actor_ref,
            )
            return None
        await self._append_once(
            work_item,
            WorkEventType.STAGE_COMPLETED,
            {
                "stage": "analysis",
                "run_id": run_id,
                "evidence_refs": list(result.evidence_refs),
            },
            actor_ref=self._analyst.actor_ref,
        )
        await self._append_once(
            work_item,
            WorkEventType.CONTRACT_PROPOSED,
            accepted.model_dump(mode="json"),
            actor_ref=self._analyst.actor_ref,
        )
        supplied_ids = {item.id for item in supplied_assumptions}
        for assumption in assumptions:
            if assumption.id not in supplied_ids:
                await self._append_once(
                    work_item,
                    WorkEventType.ASSUMPTION_RECORDED,
                    assumption.model_dump(mode="json"),
                    actor_ref=self._analyst.actor_ref,
                )
        unsupported = self._unsupported_assumption(assumptions)
        if unsupported is not None:
            await self._block_once(
                work_item,
                {
                    "reason": "unsupported_assumption",
                    "assumption_id": unsupported.id,
                    "decision_request": "Provide evidence or revise the contract.",
                },
            )
            return None
        await self._append_once(
            work_item,
            WorkEventType.CONTRACT_ACCEPTED,
            accepted.model_dump(mode="json"),
            actor_ref="software_lifecycle",
        )
        await self._set_status(
            work_item,
            self._post_contract_state(accepted),
            contract_version=accepted.version,
        )
        return accepted, assumptions

    @staticmethod
    def _accepted_analysis_contract(
        draft: WorkContract,
        analysis: WorkAnalysisResult,
        assumptions: tuple[Assumption, ...],
        analysis_evidence_refs: tuple[str, ...],
    ) -> WorkContract:
        proposal = analysis.proposal
        if not proposal.goal.strip():
            raise ValueError("analysis contract goal cannot be empty")
        if not proposal.acceptance_criteria:
            raise ValueError("analysis contract requires acceptance criteria")
        if not proposal.allowed_scope:
            raise ValueError("analysis contract requires an allowed scope")
        for target in proposal.allowed_scope:
            path = PurePosixPath(target)
            if (
                _normalized_target(target) in {"", "."}
                or path.is_absolute()
                or ".." in path.parts
            ):
                raise ValueError(f"analysis contract scope is not surgical: {target}")
        software = SoftwareContractContext.model_validate(draft.profile_context)
        reserved_ids = (
            software.repository_criterion_id,
            *(software.delivery.criterion_ids if software.delivery is not None else ()),
        )
        criteria_by_id = {criterion.id: criterion for criterion in draft.acceptance_criteria}
        preserved = tuple(criteria_by_id[criterion_id] for criterion_id in reserved_ids)
        proposed = tuple(
            AcceptanceCriterion(
                id=f"{draft.id}:analysis:criterion:{index}",
                project_id=draft.project_id,
                statement=statement,
                verification_kind="deterministic",
            )
            for index, statement in enumerate(proposal.acceptance_criteria, start=1)
        )
        accepted = WorkContract(
            id=f"{draft.id}:analysis",
            project_id=draft.project_id,
            work_id=draft.work_id,
            version=draft.version + 1,
            goal=proposal.goal,
            allowed_scope=proposal.allowed_scope,
            acceptance_criteria=(*preserved, *proposed),
            constraints=proposal.constraints,
            non_goals=proposal.non_goals,
            evidence_refs=tuple(
                dict.fromkeys((*draft.evidence_refs, *analysis_evidence_refs))
            ),
            assumption_ids=tuple(item.id for item in assumptions),
            risk=proposal.risk,
            design_required=proposal.design_required,
            profile_context=draft.profile_context,
            supersedes=draft.id,
        )
        software.validate_contract(accepted)
        return accepted

    async def _publish_analysis_claims(
        self,
        work_item: WorkItem,
        claims: tuple[ClassifiedClaim, ...],
        *,
        run_id: str,
        base_sha: str,
    ) -> tuple[str, ...]:
        project_id = work_item.project_id
        assert project_id is not None
        published_ids: list[str] = []
        for index, claim in enumerate(claims, start=1):
            if claim.classification is ClaimClassification.FACT:
                kind = KnowledgeKind.FACT
            elif claim.classification is ClaimClassification.INFERENCE:
                kind = KnowledgeKind.INFERENCE
            elif claim.classification is ClaimClassification.DECISION:
                kind = KnowledgeKind.DECISION
            elif claim.classification is ClaimClassification.UNKNOWN:
                kind = KnowledgeKind.QUESTION
            else:
                continue
            item_id = f"{run_id}:claim:{index}"
            existing = await self._knowledge_store.get(item_id, project_id=project_id)
            item = KnowledgeItem(
                id=item_id,
                project_id=project_id,
                work_id=work_item.id,
                kind=kind,
                statement=claim.statement,
                source_refs=claim.evidence_refs,
                factness_score=(
                    100
                    if kind is KnowledgeKind.FACT
                    and f"git://{base_sha}" in claim.evidence_refs
                    else 0
                ),
                created_by=self._analyst.actor_ref,
                created_at=(
                    existing.created_at if existing is not None else datetime.now(timezone.utc)
                ),
            )
            if existing is None:
                await self._knowledge_store.publish(item)
            elif existing != item:
                raise ValueError(f"analysis knowledge id has conflicting content: {item.id}")
            published_ids.append(item_id)
        return tuple(published_ids)

    async def _run_design(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        assumptions: tuple[Assumption, ...],
        workspace: SoftwareWorkspace,
    ) -> str:
        run_id = f"{work_item.id}:design:1"
        expected_sha = expected_result_sha(await self._events(work_item), workspace.base_sha)
        if not await self._preflight_workspace(
            work_item,
            workspace=workspace,
            stage="design",
            run_id=run_id,
            expected_sha=expected_sha,
        ):
            return "CONTROL_DEGRADED"
        context = SoftwareDesignContext(
            software=self._software_capsule(contract, expected_sha),
            design_result_schema=WorkDesignResult.model_json_schema(),
        )
        capsule = await self._capsule_compiler.compile(
            work_item=work_item,
            contract=contract,
            stage="design",
            search_text=contract.goal,
            open_assumption_ids=tuple(item.id for item in self._open_assumptions(assumptions)),
            prior_result_refs=contract.evidence_refs,
            profile_context=context.model_dump(mode="json"),
        )
        request = WorkRequest(
            project_id=work_item.project_id,
            work_id=work_item.id,
            run_id=run_id,
            stage="design",
            action_scope=ActionScope(
                project_id=work_item.project_id,
                objective="Design the smallest sufficient change within the accepted contract",
                allowed_targets=contract.allowed_scope,
                allowed_capabilities=tuple(
                    grant.name for grant in self._analyst.capabilities.grants
                ),
            ),
            action_intents=(),
            control_preconditions=(
                software_workspace_precondition(project_id=work_item.project_id),
            ),
        )
        diff_before, files_before = await workspace_diff(workspace)
        result = await self._analyst.controller.run(
            runtime=self._analyst.runtime,
            request=request,
            capsule=capsule,
            capabilities=self._analyst.capabilities,
            workspace=workspace,
        )
        diff_after, files_after = await workspace_diff(workspace)
        if diff_after != diff_before or files_after != files_before:
            await self._block_once(
                work_item,
                {
                    "reason": "designer_changed_workspace",
                    "run_id": run_id,
                    "decision_request": (
                        "Investigate the design workspace change and decide whether to "
                        "retry design or stop the work."
                    ),
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._analyst.actor_ref,
            )
            return "WORK_BLOCKED"
        if result.status != "passed":
            if await self._stop_for_control_degradation(work_item, run_id=run_id):
                return "CONTROL_DEGRADED"
            await self._block_once(
                work_item,
                {
                    "reason": "design_failed",
                    "run_id": run_id,
                    "decision_request": (
                        "Inspect the failed design evidence and decide whether to retry "
                        "or stop the work."
                    ),
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._analyst.actor_ref,
            )
            return "WORK_BLOCKED"
        payload = result.profile_context.get("design_result")
        if payload is None:
            await self._block_once(
                work_item,
                {
                    "reason": "design_result_missing",
                    "run_id": run_id,
                    "decision_request": "Retry design with the required structured result.",
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._analyst.actor_ref,
            )
            return "WORK_BLOCKED"
        try:
            design = WorkDesignResult.model_validate(payload)
            if design.attempt_id != run_id:
                raise ValueError("design result belongs to a different attempt")
        except ValueError as exc:
            await self._block_once(
                work_item,
                {
                    "reason": "design_result_invalid",
                    "run_id": run_id,
                    "violations": [str(exc)],
                    "decision_request": "Retry design with a valid structured result.",
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._analyst.actor_ref,
            )
            return "WORK_BLOCKED"
        try:
            design_knowledge_refs = await self._publish_analysis_claims(
                work_item,
                design.claims,
                run_id=run_id,
                base_sha=workspace.base_sha,
            )
        except ValueError as exc:
            await self._block_once(
                work_item,
                {
                    "reason": "design_knowledge_conflict",
                    "run_id": run_id,
                    "violations": [str(exc)],
                    "decision_request": (
                        "Inspect the conflicting design evidence and decide whether to retry."
                    ),
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._analyst.actor_ref,
            )
            return "WORK_BLOCKED"
        design_assumptions = self._assumptions_from_claims(
            design.claims, run_id=run_id, project_id=work_item.project_id
        )
        for assumption in design_assumptions:
            await self._append_once(
                work_item,
                WorkEventType.ASSUMPTION_RECORDED,
                assumption.model_dump(mode="json"),
                actor_ref=self._analyst.actor_ref,
            )
        if design_assumptions:
            await self._block_once(
                work_item,
                {
                    "reason": "design_assumption_unresolved",
                    "assumption_ids": [item.id for item in design_assumptions],
                    "decision_request": "Provide evidence or revise the accepted contract.",
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._analyst.actor_ref,
            )
            return "WORK_BLOCKED"
        await self._append_once(
            work_item,
            WorkEventType.STAGE_COMPLETED,
            {
                "stage": "design",
                "run_id": run_id,
                "evidence_refs": list(result.evidence_refs),
                "artifact_refs": list(result.artifact_refs),
                "knowledge_refs": list(design_knowledge_refs),
            },
            actor_ref=self._analyst.actor_ref,
        )
        await self._set_status(work_item, "READY_TO_IMPLEMENT", active_run_id=run_id)
        return "READY_TO_IMPLEMENT"

    async def _drive(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        assumptions: tuple[Assumption, ...],
        workspace: SoftwareWorkspace,
        state: str,
    ) -> WorkRecord:
        while True:
            if state == "CONTROL_DEGRADED":
                project_id = work_item.project_id
                assert project_id is not None
                degraded = await self.status(
                    work_item.id,
                    project_id=project_id,
                )
                assert degraded is not None
                return degraded
            if state in {"READY_TO_MERGE", "WORK_BLOCKED", "COMPLETE"}:
                return await self._set_status(work_item, state)
            if state == "COMPLETING":
                state = await self._complete_verified_commit(
                    work_item=work_item,
                    contract=contract,
                    workspace=workspace,
                )
                continue
            if state == "DESIGNING":
                state = await self._run_design(
                    work_item=work_item,
                    contract=contract,
                    assumptions=assumptions,
                    workspace=workspace,
                )
                continue
            if state in {"READY_TO_IMPLEMENT", "IMPLEMENTING"}:
                state = await self._run_mutation(
                    stage="implement",
                    assignment=self._implementer,
                    work_item=work_item,
                    contract=contract,
                    assumptions=assumptions,
                    workspace=workspace,
                )
                continue
            if state == "VERIFYING":
                state = await self._run_verification(
                    work_item=work_item,
                    contract=contract,
                    workspace=workspace,
                )
                continue
            if state == "REVIEWING":
                state = await self._run_review(
                    work_item=work_item,
                    contract=contract,
                    assumptions=assumptions,
                    workspace=workspace,
                )
                continue
            if state == "REPAIRING":
                events = await self._events(work_item)
                if self._repair_count(events) >= self._max_repairs:
                    return await self._repair_budget_block(work_item)
                state = await self._run_mutation(
                    stage="repair",
                    assignment=self._repairer,
                    work_item=work_item,
                    contract=contract,
                    assumptions=assumptions,
                    workspace=workspace,
                )
                continue
            raise ValueError(f"unsupported software lifecycle state: {state}")

    async def _run_mutation(
        self,
        *,
        stage: str,
        assignment: SoftwareStageOperator,
        work_item: WorkItem,
        contract: WorkContract,
        assumptions: tuple[Assumption, ...],
        workspace: SoftwareWorkspace,
    ) -> str:
        events = await self._events(work_item)
        repair_number = self._repair_count(events) + 1
        run_id = (
            f"{work_item.id}:implement:1"
            if stage == "implement"
            else f"{work_item.id}:repair:{repair_number}"
        )
        plan = await self._profile.prepare(work_item, contract)
        if len(plan.actions) != 1:
            raise ValueError("software profile requires exactly one change action")
        planned_action = plan.actions[0].model_copy(
            update={"id": f"{run_id}:change"}
        )
        expected_sha = expected_result_sha(events, workspace.base_sha)
        if not await self._preflight_workspace(
            work_item,
            workspace=workspace,
            stage=stage,
            run_id=run_id,
            expected_sha=expected_sha,
        ):
            return "CONTROL_DEGRADED"
        software = self._software_capsule(contract, expected_sha)
        open_assumptions = self._open_assumptions(assumptions)
        prior_refs = self._design_refs(events)
        referenced_artifacts: tuple[ArtifactRef, ...] = ()
        diff_workspace_path: str | None = None
        diff_artifact: ArtifactRef | None = None
        if stage == "repair":
            verification = self._latest_verification(events)
            review = self._latest_review(events)
            if verification is None:
                raise ValueError("repair requires a verification result")
            findings = review.findings if review is not None else ()
            review_refs = review.evidence_refs if review is not None else ()
            triage_event = next(
                (
                    event
                    for event in reversed(events)
                    if event.event_type is WorkEventType.TRIAGE_CREATED
                ),
                None,
            )
            triage = (
                SoftwareDeliveryTriageContext.model_validate(triage_event.payload_json)
                if triage_event is not None
                else None
            )
            triage_refs: tuple[str, ...] = ()
            if triage_event is not None and triage is not None:
                observation_refs = triage.observation.get("evidence_refs", ())
                triage_refs = (
                    *triage.evidence_refs,
                    *(str(ref) for ref in observation_refs),
                    f"work-event://{triage_event.id}",
                )
            raw_diff, relevant_files = await workspace_diff(workspace)
            inline_diff, diff_artifact = _store_diff_context(
                artifact_store=self._artifact_store,
                project_id=work_item.project_id,
                raw_diff=raw_diff,
                max_inline_diff_bytes=self._max_inline_diff_bytes,
            )
            diff_workspace_path = _DIFF_WORKSPACE_PATH if inline_diff is None else None
            context = SoftwareRepairContext(
                software=software,
                diff=inline_diff,
                diff_artifact=diff_artifact,
                diff_workspace_path=diff_workspace_path,
                verification=verification,
                relevant_files=relevant_files,
                open_assumptions=open_assumptions,
                findings=findings,
                triage=triage,
            )
            profile_context = context.model_dump(mode="json")
            prior_refs = tuple(
                dict.fromkeys(
                    (*prior_refs, *verification.evidence_refs, *review_refs, *triage_refs)
                )
            )
            referenced_artifacts = (diff_artifact,)
        else:
            profile_context = software.model_dump(mode="json")

        capsule = await self._capsule_compiler.compile(
            work_item=work_item,
            contract=contract,
            stage=stage,
            search_text=contract.goal,
            open_assumption_ids=tuple(item.id for item in open_assumptions),
            prior_result_refs=prior_refs,
            profile_context=profile_context,
            referenced_artifacts=referenced_artifacts,
        )
        request = WorkRequest(
            project_id=work_item.project_id,
            work_id=work_item.id,
            run_id=run_id,
            stage=stage,
            action_scope=ActionScope(
                project_id=work_item.project_id,
                objective=contract.goal,
                allowed_targets=contract.allowed_scope,
                allowed_capabilities=(planned_action.capability,),
            ),
            action_intents=(
                ActionIntent(
                    project_id=work_item.project_id,
                    action_id=planned_action.id,
                    capability=planned_action.capability,
                    target=contract.allowed_scope[0],
                    expected_effect=planned_action.expected_effect,
                    scope=planned_action.scope,
                    risk=contract.risk,
                    reversibility=planned_action.reversibility,
                    required_permission="workspace.write",
                    evidence_refs=contract.evidence_refs,
                ),
            ),
            control_preconditions=(
                software_workspace_precondition(project_id=work_item.project_id),
            ),
        )
        runtime = assignment.runtime
        if diff_workspace_path is not None:
            assert diff_artifact is not None
            runtime = _DiffMaterializingRuntime(
                delegate=runtime,
                artifact_store=self._artifact_store,
                artifact=diff_artifact,
                relative_path=diff_workspace_path,
            )
        result = await assignment.controller.run(
            runtime=runtime,
            request=request,
            capsule=capsule,
            capabilities=assignment.capabilities,
            workspace=workspace,
        )
        profile_verification: VerificationResult | None = None
        if result.action_results:
            profile_verification = await self._profile.verify(
                work_item,
                contract,
                planned_action.verification,
                result.action_results,
            )
            if profile_verification.attempt_id != run_id:
                raise ValueError("profile verification belongs to a different attempt")
        if result.status != "passed":
            if await self._stop_for_control_degradation(work_item, run_id=run_id):
                return "CONTROL_DEGRADED"
            report = self._discipline_report(
                await self._events(work_item),
                run_id=run_id,
            )
            contract_scope_violations = (
                self._accepted_contract_scope_violations(report) if report is not None else ()
            )
            if contract_scope_violations:
                await self._block_once(
                    work_item,
                    {
                        "reason": "contract_drift",
                        "run_id": run_id,
                        "accepted_contract_version": contract.version,
                        "required_contract_version": contract.version + 1,
                        "violations": list(contract_scope_violations),
                        "decision_request": (
                            "Create and accept a new WorkContract version or stop the work."
                        ),
                        "evidence_refs": list(result.evidence_refs),
                    },
                    actor_ref=assignment.actor_ref,
                )
                return "WORK_BLOCKED"
            failed_stage = "implementation" if stage == "implement" else "repair"
            await self._block_once(
                work_item,
                {
                    "reason": f"{stage}_failed",
                    "run_id": run_id,
                    "decision_request": (
                        f"Inspect the failed {failed_stage} evidence and decide whether to "
                        "retry or stop the work."
                    ),
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=assignment.actor_ref,
            )
            return "WORK_BLOCKED"

        if profile_verification is None:
            raise ValueError("passed software execution requires action verification")

        result_sha = await self._worktree_manager.current_sha(workspace)
        await self._append(
            work_item,
            WorkEventType.STAGE_COMPLETED,
            {
                "stage": stage,
                "run_id": run_id,
                "current_sha": result_sha,
                "evidence_refs": list(result.evidence_refs),
                "artifact_refs": list(result.artifact_refs),
                "profile_context": SoftwareAttemptContext(
                    base_sha=workspace.base_sha,
                    result_sha=result_sha,
                ).model_dump(mode="json"),
                "profile_verification": profile_verification.model_dump(mode="json"),
                "criterion_ids": list(planned_action.verification),
            },
            actor_ref=assignment.actor_ref,
        )
        await self._set_status(work_item, "VERIFYING", active_run_id=run_id)
        return "VERIFYING"

    async def _run_verification(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        workspace: SoftwareWorkspace,
    ) -> str:
        events = await self._events(work_item)
        completed = next(
            event
            for event in reversed(events)
            if event.event_type is WorkEventType.STAGE_COMPLETED
            and event.payload_json.get("stage") in {"implement", "repair"}
        )
        attempt_id = str(completed.payload_json["run_id"])
        criterion_ids = tuple(
            str(criterion_id)
            for criterion_id in completed.payload_json["criterion_ids"]
        )
        profile_result = VerificationResult.model_validate(
            completed.payload_json["profile_verification"]
        )
        if profile_result.attempt_id != attempt_id:
            raise ValueError("profile verification belongs to a different attempt")
        validate_verification_result(contract, criterion_ids, profile_result)
        isolation_was_degraded = (
            SOFTWARE_VERIFICATION_ISOLATION_PRECONDITION_ID
            in active_control_precondition_ids(events)
        )
        try:
            deterministic = await self._verifier.verify(
                work_item=work_item,
                contract=contract,
                criterion_ids=criterion_ids,
                attempt_id=attempt_id,
                workspace=workspace,
                commands=self._verification_commands,
            )
        except VerificationIsolationError as exc:
            if not isolation_was_degraded:
                await self._append(
                    work_item,
                    WorkEventType.CONTROL_DEGRADED,
                    {
                        "run_id": attempt_id,
                        "stage": "verification",
                        "failed_preconditions": [
                            SOFTWARE_VERIFICATION_ISOLATION_PRECONDITION_ID
                        ],
                        "evidence_refs": [workspace.ref],
                        "details": str(exc),
                        "frozen_action_ids": [],
                    },
                    actor_ref="software.verifier",
                )
            await self._set_status(
                work_item,
                "CONTROL_DEGRADED",
                active_run_id=attempt_id,
            )
            return "CONTROL_DEGRADED"
        validate_verification_result(contract, criterion_ids, deterministic)
        if isolation_was_degraded:
            await self._append(
                work_item,
                WorkEventType.CONTROL_RESTORED,
                {
                    "run_id": attempt_id,
                    "stage": "verification",
                    "precondition_ids": [
                        SOFTWARE_VERIFICATION_ISOLATION_PRECONDITION_ID
                    ],
                    "evidence_refs": list(deterministic.evidence_refs),
                },
                actor_ref="software.verifier",
            )
        profile_by_id = {
            result.criterion_id: result for result in profile_result.criterion_results
        }
        deterministic_by_id = {
            result.criterion_id: result for result in deterministic.criterion_results
        }
        criterion_results = tuple(
            CriterionVerification(
                project_id=work_item.project_id,
                contract_id=contract.id,
                criterion_id=criterion_id,
                passed=(
                    profile_by_id[criterion_id].passed
                    and deterministic_by_id[criterion_id].passed
                ),
                evidence_refs=tuple(
                    dict.fromkeys(
                        (
                            *profile_by_id[criterion_id].evidence_refs,
                            *deterministic_by_id[criterion_id].evidence_refs,
                        )
                    )
                ),
            )
            for criterion_id in criterion_ids
        )
        result = VerificationResult(
            project_id=work_item.project_id,
            contract_id=contract.id,
            attempt_id=attempt_id,
            stage="verification",
            passed=all(item.passed for item in criterion_results),
            criterion_results=criterion_results,
            evidence_refs=tuple(
                dict.fromkeys(
                    (*profile_result.evidence_refs, *deterministic.evidence_refs)
                )
            ),
            profile_context={
                **profile_result.profile_context,
                **deterministic.profile_context,
            },
        )
        validate_verification_result(contract, criterion_ids, result)
        await self._append(
            work_item,
            WorkEventType.VERIFICATION_RECORDED,
            result.model_dump(mode="json"),
            actor_ref="software.verifier",
        )
        if result.passed:
            await self._set_status(work_item, "REVIEWING", active_run_id=attempt_id)
            return "REVIEWING"

        if self._repair_count(await self._events(work_item)) >= self._max_repairs:
            await self._repair_budget_block(work_item)
            return "WORK_BLOCKED"
        await self._set_status(work_item, "REPAIRING", active_run_id=attempt_id)
        return "REPAIRING"

    async def _run_review(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        assumptions: tuple[Assumption, ...],
        workspace: SoftwareWorkspace,
    ) -> str:
        events = await self._events(work_item)
        verification = self._latest_verification(events)
        if verification is None or not verification.passed:
            raise ValueError("review requires passing deterministic verification")
        run_id = f"{work_item.id}:review:{self._review_count(events) + 1}"
        expected_sha = expected_result_sha(events, workspace.base_sha)
        if not await self._preflight_workspace(
            work_item,
            workspace=workspace,
            stage="review",
            run_id=run_id,
            expected_sha=expected_sha,
        ):
            return "CONTROL_DEGRADED"
        diff_before, relevant_files = await workspace_diff(workspace)
        inline_diff, diff_artifact = _store_diff_context(
            artifact_store=self._artifact_store,
            project_id=work_item.project_id,
            raw_diff=diff_before,
            max_inline_diff_bytes=self._max_inline_diff_bytes,
        )
        diff_workspace_path = _DIFF_WORKSPACE_PATH if inline_diff is None else None
        context = SoftwareReviewContext(
            software=self._software_capsule(contract, expected_sha),
            diff=inline_diff,
            diff_artifact=diff_artifact,
            diff_workspace_path=diff_workspace_path,
            verification=verification,
            relevant_files=relevant_files,
            open_assumptions=self._open_assumptions(assumptions),
            review_result_schema=ReviewResult.model_json_schema(),
        )
        capsule = await self._capsule_compiler.compile(
            work_item=work_item,
            contract=contract,
            stage="review",
            search_text=contract.goal,
            open_assumption_ids=tuple(item.id for item in self._open_assumptions(assumptions)),
            prior_result_refs=tuple(
                dict.fromkeys((*self._design_refs(events), *verification.evidence_refs))
            ),
            profile_context=context.model_dump(mode="json"),
            referenced_artifacts=(diff_artifact,),
        )
        request = WorkRequest(
            project_id=work_item.project_id,
            work_id=work_item.id,
            run_id=run_id,
            stage="review",
            action_scope=ActionScope(
                project_id=work_item.project_id,
                objective="Independently review the verified software change",
                allowed_targets=contract.allowed_scope,
                allowed_capabilities=tuple(
                    grant.name for grant in self._reviewer.capabilities.grants
                ),
            ),
            action_intents=(),
            control_preconditions=(
                software_workspace_precondition(project_id=work_item.project_id),
            ),
        )
        review_runtime = self._reviewer.runtime
        if diff_workspace_path is not None:
            review_runtime = _DiffMaterializingRuntime(
                delegate=review_runtime,
                artifact_store=self._artifact_store,
                artifact=diff_artifact,
                relative_path=diff_workspace_path,
            )
        result = await self._reviewer.controller.run(
            runtime=review_runtime,
            request=request,
            capsule=capsule,
            capabilities=self._reviewer.capabilities,
            workspace=workspace,
        )
        if result.status != "passed":
            if await self._stop_for_control_degradation(work_item, run_id=run_id):
                return "CONTROL_DEGRADED"
            await self._block_once(
                work_item,
                {
                    "reason": "review_failed",
                    "run_id": run_id,
                    "decision_request": (
                        "Inspect the failed independent review evidence and decide whether to "
                        "retry or stop the work."
                    ),
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._reviewer.actor_ref,
            )
            return "WORK_BLOCKED"
        diff_after, files_after = await workspace_diff(workspace)
        if diff_after != diff_before or files_after != relevant_files:
            await self._block_once(
                work_item,
                {
                    "reason": "reviewer_changed_workspace",
                    "run_id": run_id,
                    "decision_request": (
                        "Investigate the reviewer workspace change and decide whether to retry "
                        "review or stop the work."
                    ),
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._reviewer.actor_ref,
            )
            return "WORK_BLOCKED"
        payload = result.profile_context.get("review_result")
        if payload is None:
            await self._block_once(
                work_item,
                {
                    "reason": "review_result_missing",
                    "run_id": run_id,
                    "decision_request": (
                        "Decide whether to retry the independent review or stop the work."
                    ),
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._reviewer.actor_ref,
            )
            return "WORK_BLOCKED"
        try:
            review = ReviewResult.model_validate(payload)
        except ValueError as exc:
            await self._block_once(
                work_item,
                {
                    "reason": "review_result_invalid",
                    "run_id": run_id,
                    "violations": [str(exc)],
                    "decision_request": (
                        "Retry independent review with all required semantic answers "
                        "or stop the work."
                    ),
                    "evidence_refs": list(result.evidence_refs),
                },
                actor_ref=self._reviewer.actor_ref,
            )
            return "WORK_BLOCKED"
        if review.attempt_id != run_id:
            raise ValueError("review result belongs to a different attempt")
        for finding in review.findings:
            if finding.profile_context:
                SoftwareReviewFindingContext.model_validate(finding.profile_context)
        await self._append(
            work_item,
            WorkEventType.REVIEW_RECORDED,
            review.model_dump(mode="json"),
            actor_ref=self._reviewer.actor_ref,
        )

        if review.verdict == "accept":
            software = SoftwareContractContext.model_validate(contract.profile_context)
            if (
                software.repository_outcome
                is SoftwareRepositoryOutcome.VERIFIED_COMMIT
            ):
                await self._set_status(
                    work_item,
                    "COMPLETING",
                    active_run_id=run_id,
                )
                return "COMPLETING"
            await self._set_status(work_item, "READY_TO_MERGE", active_run_id=run_id)
            return "READY_TO_MERGE"
        if review.verdict == "blocked":
            await self._block_once(
                work_item,
                {
                    "reason": "review_blocked",
                    "run_id": run_id,
                    "decision_request": (
                        "Resolve the independent review blocker or stop the work."
                    ),
                    "evidence_refs": list(review.evidence_refs),
                },
                actor_ref=self._reviewer.actor_ref,
            )
            return "WORK_BLOCKED"
        if self._repair_count(await self._events(work_item)) >= self._max_repairs:
            await self._repair_budget_block(work_item)
            return "WORK_BLOCKED"
        await self._set_status(work_item, "REPAIRING", active_run_id=run_id)
        return "REPAIRING"

    async def _complete_verified_commit(
        self,
        *,
        work_item: WorkItem,
        contract: WorkContract,
        workspace: SoftwareWorkspace,
    ) -> str:
        software = SoftwareContractContext.model_validate(contract.profile_context)
        software.validate_contract(contract)
        if software.repository_outcome is not SoftwareRepositoryOutcome.VERIFIED_COMMIT:
            raise ValueError("local completion requires a verified-commit outcome")

        events = await self._events(work_item)
        repository_result = next(
            (
                VerificationResult.model_validate(event.payload_json)
                for event in reversed(events)
                if event.event_type is WorkEventType.VERIFICATION_RECORDED
                and event.payload_json.get("stage") == "repository"
                and event.payload_json.get("contract_id") == contract.id
            ),
            None,
        )
        if repository_result is None:
            expected_sha = expected_result_sha(events, software.base_sha)
            result_sha = await self._worktree_manager.commit_reviewed(
                workspace,
                expected_sha=expected_sha,
                commit_message=f"sagewai work {work_item.id}",
            )
            read_back_sha = await self._worktree_manager.current_sha(workspace)
            if read_back_sha != result_sha:
                raise WorkspaceStaleError("repository commit read-back changed")
            evidence_refs = (f"git://{result_sha}",)
            repository_result = VerificationResult(
                project_id=work_item.project_id,
                contract_id=contract.id,
                attempt_id=f"{work_item.id}:repository:1",
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
                    "base_sha": expected_sha,
                    "result_sha": result_sha,
                },
            )
            validate_verification_result(
                contract,
                (software.repository_criterion_id,),
                repository_result,
            )
            await self._append_once(
                work_item,
                WorkEventType.VERIFICATION_RECORDED,
                repository_result.model_dump(mode="json"),
                actor_ref="software.lifecycle",
            )
        else:
            validate_verification_result(
                contract,
                (software.repository_criterion_id,),
                repository_result,
            )
            result_sha = str(repository_result.profile_context["result_sha"])
            if await self._worktree_manager.current_sha(workspace) != result_sha:
                raise WorkspaceStaleError("repository verification SHA does not match HEAD")

        current_results = tuple(
            VerificationResult.model_validate(event.payload_json)
            for event in await self._events(work_item)
            if event.event_type is WorkEventType.VERIFICATION_RECORDED
            and event.payload_json.get("contract_id") == contract.id
        )
        try:
            evaluation = evaluate_completion(
                work=work_item,
                contract=contract,
                verification_results=current_results,
                evaluated_at=datetime.now(timezone.utc),
            )
        except ValueError as exc:
            await self._block_once(
                work_item,
                {
                    "reason": "completion_evidence_invalid",
                    "violations": [str(exc)],
                    "decision_request": "Repair the current-contract verification evidence.",
                },
                actor_ref="software.lifecycle",
            )
            return "WORK_BLOCKED"
        if not evaluation.passed:
            await self._block_once(
                work_item,
                {
                    "reason": "completion_criteria_failed",
                    "failed_criterion_ids": [
                        result.criterion_id
                        for result in evaluation.criterion_results
                        if not result.passed
                    ],
                    "decision_request": "Repair the failed acceptance criteria.",
                },
                actor_ref="software.lifecycle",
            )
            return "WORK_BLOCKED"

        await self._append_once(
            work_item,
            WorkEventType.WORK_COMPLETED,
            evaluation.model_dump(mode="json"),
            actor_ref="software.lifecycle",
        )
        await self._set_status(work_item, "COMPLETE")
        return "COMPLETE"

    def _validate_inputs(
        self,
        work_item: WorkItem,
        contract: WorkContract,
        assumptions: tuple[Assumption, ...],
    ) -> SoftwareContractContext:
        if work_item.project_id is None:
            raise ValueError("software lifecycle requires a project")
        if work_item.profile != "software":
            raise ValueError("software lifecycle requires the software profile")
        if contract.project_id != work_item.project_id or contract.work_id != work_item.id:
            raise ValueError("contract belongs to different work")
        if not contract.allowed_scope:
            raise ValueError("software contract requires an allowed scope")
        if any(item.project_id != work_item.project_id for item in assumptions):
            raise ValueError("assumption belongs to a different project")
        provided_ids = tuple(item.id for item in assumptions)
        if provided_ids != contract.assumption_ids:
            raise ValueError("contract assumptions do not match supplied assumptions")
        software = SoftwareContractContext.model_validate(contract.profile_context)
        software.validate_contract(contract)
        return software

    @staticmethod
    def _post_contract_state(contract: WorkContract) -> str:
        return "DESIGNING" if contract.design_required else "READY_TO_IMPLEMENT"

    @staticmethod
    def _assumptions_from_claims(
        claims: tuple[ClassifiedClaim, ...],
        *,
        run_id: str,
        project_id: str | None,
    ) -> tuple[Assumption, ...]:
        return tuple(
            Assumption(
                id=f"{run_id}:assumption:{index}",
                project_id=project_id,
                statement=claim.statement,
                kind=claim.kind,
                evidence_refs=claim.evidence_refs,
                confidence=claim.confidence,
                impact_if_wrong=claim.impact_if_wrong,
                status="open",
            )
            for index, claim in enumerate(claims, start=1)
            if claim.classification is ClaimClassification.UNKNOWN
            or (
                claim.classification
                in {
                    ClaimClassification.FACT,
                    ClaimClassification.INFERENCE,
                    ClaimClassification.REQUIREMENT,
                }
                and not claim.evidence_refs
            )
        )

    @staticmethod
    def _unsupported_assumption(
        assumptions: tuple[Assumption, ...],
    ) -> Assumption | None:
        return next(
            (
                item
                for item in assumptions
                if item.status == "open"
                and not item.evidence_refs
                and (item.kind == "compatibility" or item.impact_if_wrong == "high")
            ),
            None,
        )

    @staticmethod
    def _open_assumptions(
        assumptions: tuple[Assumption, ...],
    ) -> tuple[Assumption, ...]:
        return tuple(item for item in assumptions if item.status == "open")

    def _software_capsule(
        self,
        contract: WorkContract,
        current_sha: str,
    ) -> SoftwareCapsuleContext:
        software = SoftwareContractContext.model_validate(contract.profile_context)
        return SoftwareCapsuleContext(
            base_sha=software.base_sha,
            current_sha=current_sha,
            repo_instructions=self._repo_instructions,
            verification_commands=self._verification_commands,
        )

    async def _events(self, work_item: WorkItem) -> list[WorkEvent]:
        return await self._work_store.read_events(
            work_item.id,
            project_id=work_item.project_id,
        )

    async def _stop_for_control_degradation(
        self,
        work_item: WorkItem,
        *,
        run_id: str,
    ) -> bool:
        events = await self._events(work_item)
        if SOFTWARE_WORKSPACE_PRECONDITION_ID not in active_control_precondition_ids(events):
            return False
        await self._set_status(work_item, "CONTROL_DEGRADED", active_run_id=run_id)
        return True

    async def _preflight_workspace(
        self,
        work_item: WorkItem,
        *,
        workspace: SoftwareWorkspace,
        stage: str,
        run_id: str,
        expected_sha: str,
    ) -> bool:
        try:
            await self._worktree_manager.assert_current(
                workspace,
                expected_sha=expected_sha,
            )
        except WorkspaceStaleError as exc:
            await self._record_workspace_degradation(
                work_item,
                stage=stage,
                run_id=run_id,
                detail=str(exc),
                evidence_refs=(workspace.ref,),
            )
            return False
        return True

    async def _record_workspace_degradation(
        self,
        work_item: WorkItem,
        *,
        stage: str,
        run_id: str,
        detail: str,
        evidence_refs: tuple[str, ...],
    ) -> None:
        events = await self._events(work_item)
        if SOFTWARE_WORKSPACE_PRECONDITION_ID not in active_control_precondition_ids(events):
            await self._append(
                work_item,
                WorkEventType.CONTROL_DEGRADED,
                {
                    "run_id": run_id,
                    "stage": stage,
                    "failed_preconditions": [SOFTWARE_WORKSPACE_PRECONDITION_ID],
                    "evidence_refs": list(evidence_refs),
                    "details": f"{SOFTWARE_WORKSPACE_PRECONDITION_ID}: {detail}",
                    "frozen_action_ids": (
                        [] if stage in {"design", "review"} else [f"{run_id}:change"]
                    ),
                },
                actor_ref="software_lifecycle",
            )
        await self._set_status(work_item, "CONTROL_DEGRADED", active_run_id=run_id)

    @staticmethod
    def _operator_stage(state: str) -> str | None:
        if state == "DESIGNING":
            return "design"
        if state in {"READY_TO_IMPLEMENT", "IMPLEMENTING"}:
            return "implement"
        if state == "REPAIRING":
            return "repair"
        if state == "REVIEWING":
            return "review"
        return None

    def _operator_run_id(
        self,
        work_item: WorkItem,
        *,
        stage: str,
        events: list[WorkEvent],
    ) -> str:
        if stage == "design":
            return f"{work_item.id}:design:1"
        if stage == "implement":
            return f"{work_item.id}:implement:1"
        if stage == "repair":
            return f"{work_item.id}:repair:{self._repair_count(events) + 1}"
        return f"{work_item.id}:review:{self._review_count(events) + 1}"

    async def _append(
        self,
        work_item: WorkItem,
        event_type: WorkEventType,
        payload: dict,
        *,
        actor_ref: str | None,
    ) -> None:
        events = await self._events(work_item)
        await self._work_store.append_event(
            WorkEvent(
                id=str(uuid.uuid4()),
                project_id=work_item.project_id,
                work_id=work_item.id,
                sequence=events[-1].sequence + 1 if events else 1,
                event_type=event_type,
                actor_type="software_lifecycle",
                actor_ref=actor_ref,
                payload_json=payload,
                created_at=datetime.now(timezone.utc),
            )
        )

    async def _append_once(
        self,
        work_item: WorkItem,
        event_type: WorkEventType,
        payload: dict,
        *,
        actor_ref: str | None,
    ) -> None:
        events = await self._events(work_item)
        matching = tuple(
            event
            for event in events
            if event.event_type is event_type and event.payload_json == payload
        )
        if matching:
            return
        await self._append(
            work_item,
            event_type,
            payload,
            actor_ref=actor_ref,
        )

    async def _set_status(
        self,
        work_item: WorkItem,
        status: str,
        *,
        active_run_id: str | None = None,
        contract_version: int | None = None,
    ) -> WorkRecord:
        record = await self._work_store.load_work(
            work_item.id,
            project_id=work_item.project_id,
        )
        if record is None:
            raise ValueError("work projection is missing")
        updated = record.model_copy(
            update={
                "status": status,
                "active_run_id": active_run_id,
                "pending_gate": None,
                "contract_version": (
                    record.contract_version if contract_version is None else contract_version
                ),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await self._work_store.save_work(updated)
        return updated

    async def _block_once(
        self,
        work_item: WorkItem,
        payload: dict,
        *,
        actor_ref: str | None = None,
    ) -> WorkRecord:
        events = await self._events(work_item)
        if not any(event.event_type is WorkEventType.WORK_BLOCKED for event in events):
            await self._append(
                work_item,
                WorkEventType.WORK_BLOCKED,
                payload,
                actor_ref=actor_ref,
            )
        return await self._set_status(work_item, "WORK_BLOCKED")

    async def _repair_budget_block(self, work_item: WorkItem) -> WorkRecord:
        return await self._block_once(
            work_item,
            {
                "reason": "repair_budget_exhausted",
                "repair_attempts": self._max_repairs,
                "decision_request": "Revise the contract or stop the work.",
            },
        )

    @staticmethod
    def _canonical_inputs(
        events: list[WorkEvent],
    ) -> tuple[WorkItem, WorkContract, tuple[Assumption, ...]]:
        created = next(event for event in events if event.event_type is WorkEventType.WORK_CREATED)
        accepted = next(
            event
            for event in reversed(events)
            if event.event_type is WorkEventType.CONTRACT_ACCEPTED
        )
        assumptions = tuple(
            Assumption.model_validate(event.payload_json)
            for event in events
            if event.event_type is WorkEventType.ASSUMPTION_RECORDED
        )
        return (
            WorkItem.model_validate(created.payload_json),
            WorkContract.model_validate(accepted.payload_json),
            assumptions,
        )

    @staticmethod
    def _analysis_inputs(
        events: list[WorkEvent],
    ) -> tuple[WorkItem, WorkContract, tuple[Assumption, ...]]:
        created = next(event for event in events if event.event_type is WorkEventType.WORK_CREATED)
        proposed = next(
            event for event in events if event.event_type is WorkEventType.CONTRACT_PROPOSED
        )
        draft = WorkContract.model_validate(proposed.payload_json)
        supplied = {
            event.payload_json["id"]: Assumption.model_validate(event.payload_json)
            for event in events
            if event.event_type is WorkEventType.ASSUMPTION_RECORDED
            and event.payload_json.get("id") in draft.assumption_ids
        }
        return (
            WorkItem.model_validate(created.payload_json),
            draft,
            tuple(supplied[item_id] for item_id in draft.assumption_ids),
        )

    @staticmethod
    def _state_from_events(events: list[WorkEvent]) -> str:
        accepted = next(
            (
                WorkContract.model_validate(event.payload_json)
                for event in reversed(events)
                if event.event_type is WorkEventType.CONTRACT_ACCEPTED
            ),
            None,
        )
        state = (
            SoftwareLifecycle._post_contract_state(accepted)
            if accepted is not None
            else "READY_TO_IMPLEMENT"
        )
        verified_commit = (
            accepted is not None
            and SoftwareContractContext.model_validate(
                accepted.profile_context
            ).repository_outcome
            is SoftwareRepositoryOutcome.VERIFIED_COMMIT
        )
        for event in events:
            if event.event_type is WorkEventType.WORK_BLOCKED:
                state = "WORK_BLOCKED"
            elif event.event_type is WorkEventType.CONTROL_DEGRADED:
                stage = event.payload_json.get("stage")
                if stage == "design":
                    state = "DESIGNING"
                elif stage == "implement":
                    state = "IMPLEMENTING"
                elif stage == "repair":
                    state = "REPAIRING"
                elif stage == "verification":
                    state = "VERIFYING"
                elif stage == "review":
                    state = "REVIEWING"
            elif event.event_type is WorkEventType.STAGE_STARTED:
                stage = event.payload_json.get("stage")
                if stage == "design":
                    state = "DESIGNING"
                elif stage == "implement":
                    state = "IMPLEMENTING"
                elif stage == "repair":
                    state = "REPAIRING"
                elif stage == "review":
                    state = "REVIEWING"
            elif event.event_type is WorkEventType.STAGE_COMPLETED:
                stage = event.payload_json.get("stage")
                if stage == "design":
                    state = "READY_TO_IMPLEMENT"
                elif stage in {"implement", "repair"}:
                    state = "VERIFYING"
            elif event.event_type is WorkEventType.VERIFICATION_RECORDED:
                result = VerificationResult.model_validate(event.payload_json)
                if result.stage == "repository":
                    state = "COMPLETING"
                else:
                    state = "REVIEWING" if result.passed else "REPAIRING"
            elif event.event_type is WorkEventType.REVIEW_RECORDED:
                review = ReviewResult.model_validate(event.payload_json)
                if review.verdict == "accept":
                    state = "COMPLETING" if verified_commit else "READY_TO_MERGE"
                elif review.verdict == "repair":
                    state = "REPAIRING"
                else:
                    state = "WORK_BLOCKED"
            elif event.event_type is WorkEventType.TRIAGE_CREATED:
                state = "REPAIRING"
            elif event.event_type is WorkEventType.WORK_COMPLETED:
                state = "COMPLETE"
        return state

    @staticmethod
    def _repair_count(events: list[WorkEvent]) -> int:
        return sum(
            event.event_type is WorkEventType.STAGE_COMPLETED
            and event.payload_json.get("stage") == "repair"
            for event in events
        )

    @staticmethod
    def _review_count(events: list[WorkEvent]) -> int:
        return sum(event.event_type is WorkEventType.REVIEW_RECORDED for event in events)

    @staticmethod
    def _design_refs(events: list[WorkEvent]) -> tuple[str, ...]:
        completed = next(
            (
                event
                for event in reversed(events)
                if event.event_type is WorkEventType.STAGE_COMPLETED
                and event.payload_json.get("stage") == "design"
            ),
            None,
        )
        if completed is None:
            return ()
        return tuple(
            dict.fromkeys(
                (
                    *(str(ref) for ref in completed.payload_json.get("knowledge_refs", ())),
                    *(str(ref) for ref in completed.payload_json.get("evidence_refs", ())),
                    *(str(ref) for ref in completed.payload_json.get("artifact_refs", ())),
                )
            )
        )

    @staticmethod
    def _accepted_contract_scope_violations(
        report: OperatorDisciplineReport,
    ) -> tuple[str, ...]:
        """Select only accepted-contract target-boundary violations."""
        suffixes = (" is outside allowed targets", " is forbidden")
        return tuple(
            violation for violation in report.scope_violations if violation.endswith(suffixes)
        )

    @staticmethod
    def _discipline_report(
        events: list[WorkEvent],
        *,
        run_id: str,
    ) -> OperatorDisciplineReport | None:
        return next(
            (
                OperatorDisciplineReport.model_validate(event.payload_json)
                for event in reversed(events)
                if event.event_type is WorkEventType.OPERATOR_DISCIPLINE_RECORDED
                and event.payload_json.get("run_id") == run_id
            ),
            None,
        )

    @staticmethod
    def _latest_verification(
        events: list[WorkEvent],
    ) -> VerificationResult | None:
        return next(
            (
                VerificationResult.model_validate(event.payload_json)
                for event in reversed(events)
                if event.event_type is WorkEventType.VERIFICATION_RECORDED
            ),
            None,
        )

    @staticmethod
    def _latest_review(events: list[WorkEvent]) -> ReviewResult | None:
        return next(
            (
                ReviewResult.model_validate(event.payload_json)
                for event in reversed(events)
                if event.event_type is WorkEventType.REVIEW_RECORDED
            ),
            None,
        )
