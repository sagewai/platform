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

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol

from sagewai.work.capsule import TaskCapsuleCompiler
from sagewai.work.contract import WorkContract
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
    OperatorDisciplineReport,
    Reversibility,
    ReviewResult,
    VerificationResult,
    WorkAnalysisResult,
    WorkItem,
    WorkRecord,
)
from sagewai.work.profiles.software.models import (
    SoftwareAnalysisContext,
    SoftwareAttemptContext,
    SoftwareCapsuleContext,
    SoftwareContractContext,
    SoftwareDeliveryTriageContext,
    SoftwareRepairContext,
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
from sagewai.work.profiles.software.verification import _normalized_target
from sagewai.work.runtime import (
    CapabilitySet,
    OperatorRuntime,
    WorkRequest,
)
from sagewai.work.store import WorkStore


def expected_result_sha(events: list[WorkEvent], base_sha: str) -> str:
    """Return the latest recorded workspace HEAD, including publication commits."""
    result_sha = base_sha
    for event in events:
        if event.event_type is not WorkEventType.STAGE_COMPLETED:
            continue
        stage = event.payload_json.get("stage")
        if stage in {"implement", "repair"}:
            result_sha = str(event.payload_json["current_sha"])
        elif stage == "branch_published":
            result_sha = str(event.payload_json["branch_sha"])
    return result_sha


class _Verifier(Protocol):
    async def verify(
        self,
        *,
        work_item: WorkItem,
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
    ) -> None:
        if reviewer.actor_ref in {implementer.actor_ref, repairer.actor_ref}:
            raise ValueError("reviewer cannot review its own result")
        if not verification_commands:
            raise ValueError("at least one verification command is required")
        self._work_store = work_store
        self._knowledge_store = knowledge_store
        self._capsule_compiler = capsule_compiler
        self._worktree_manager = worktree_manager
        self._verifier = verifier
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
            state="READY_TO_IMPLEMENT",
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
                state="READY_TO_IMPLEMENT",
            )
        work_item, contract, assumptions = self._canonical_inputs(events)
        software = self._validate_inputs(work_item, contract, assumptions)
        state = self._state_from_events(events)
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
            "READY_TO_IMPLEMENT",
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
        if proposal.design_required:
            raise ValueError("design-required contract cannot proceed without a design stage")
        for target in proposal.allowed_scope:
            path = PurePosixPath(target)
            if (
                _normalized_target(target) in {"", "."}
                or path.is_absolute()
                or ".." in path.parts
            ):
                raise ValueError(f"analysis contract scope is not surgical: {target}")
        return WorkContract(
            id=f"{draft.id}:analysis",
            project_id=draft.project_id,
            work_id=draft.work_id,
            version=draft.version + 1,
            goal=proposal.goal,
            allowed_scope=proposal.allowed_scope,
            acceptance_criteria=proposal.acceptance_criteria,
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

    async def _publish_analysis_claims(
        self,
        work_item: WorkItem,
        claims: tuple[ClassifiedClaim, ...],
        *,
        run_id: str,
        base_sha: str,
    ) -> None:
        project_id = work_item.project_id
        assert project_id is not None
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
            if state in {"READY_TO_MERGE", "WORK_BLOCKED"}:
                return await self._set_status(work_item, state)
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
        prior_refs: tuple[str, ...] = ()
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
            diff, relevant_files = await workspace_diff(workspace)
            context = SoftwareRepairContext(
                software=software,
                diff=diff,
                verification=verification,
                relevant_files=relevant_files,
                open_assumptions=open_assumptions,
                findings=findings,
                triage=triage,
            )
            profile_context = context.model_dump(mode="json")
            prior_refs = tuple(
                dict.fromkeys((*verification.evidence_refs, *review_refs, *triage_refs))
            )
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
        )
        action_id = f"{run_id}:change"
        request = WorkRequest(
            project_id=work_item.project_id,
            work_id=work_item.id,
            run_id=run_id,
            stage=stage,
            action_scope=ActionScope(
                objective=contract.goal,
                allowed_targets=contract.allowed_scope,
                allowed_capabilities=("filesystem.write",),
            ),
            action_intents=(
                ActionIntent(
                    project_id=work_item.project_id,
                    action_id=action_id,
                    capability="filesystem.write",
                    target=contract.allowed_scope[0],
                    expected_effect=contract.goal,
                    scope={"allowed_targets": list(contract.allowed_scope)},
                    risk=contract.risk,
                    reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
                    required_permission="workspace.write",
                    evidence_refs=contract.evidence_refs,
                ),
            ),
            control_preconditions=(
                software_workspace_precondition(project_id=work_item.project_id),
            ),
        )
        result = await assignment.controller.run(
            runtime=assignment.runtime,
            request=request,
            capsule=capsule,
            capabilities=assignment.capabilities,
            workspace=workspace,
        )
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
            },
            actor_ref=assignment.actor_ref,
        )
        await self._set_status(work_item, "VERIFYING", active_run_id=run_id)
        return "VERIFYING"

    async def _run_verification(
        self,
        *,
        work_item: WorkItem,
        workspace: SoftwareWorkspace,
    ) -> str:
        events = await self._events(work_item)
        attempt_id = f"{work_item.id}:verify:{self._verification_count(events) + 1}"
        result = await self._verifier.verify(
            work_item=work_item,
            attempt_id=attempt_id,
            workspace=workspace,
            commands=self._verification_commands,
        )
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
        context = SoftwareReviewContext(
            software=self._software_capsule(contract, expected_sha),
            diff=diff_before,
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
            prior_result_refs=verification.evidence_refs,
            profile_context=context.model_dump(mode="json"),
        )
        request = WorkRequest(
            project_id=work_item.project_id,
            work_id=work_item.id,
            run_id=run_id,
            stage="review",
            action_scope=ActionScope(
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
        result = await self._reviewer.controller.run(
            runtime=self._reviewer.runtime,
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
        provided_ids = tuple(item.id for item in assumptions)
        if provided_ids != contract.assumption_ids:
            raise ValueError("contract assumptions do not match supplied assumptions")
        return SoftwareContractContext.model_validate(contract.profile_context)

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
                        [] if stage == "review" else [f"{run_id}:change"]
                    ),
                },
                actor_ref="software_lifecycle",
            )
        await self._set_status(work_item, "CONTROL_DEGRADED", active_run_id=run_id)

    @staticmethod
    def _operator_stage(state: str) -> str | None:
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
        state = "READY_TO_IMPLEMENT"
        for event in events:
            if event.event_type is WorkEventType.WORK_BLOCKED:
                state = "WORK_BLOCKED"
            elif event.event_type is WorkEventType.CONTROL_DEGRADED:
                stage = event.payload_json.get("stage")
                if stage == "implement":
                    state = "IMPLEMENTING"
                elif stage == "repair":
                    state = "REPAIRING"
                elif stage == "review":
                    state = "REVIEWING"
            elif event.event_type is WorkEventType.STAGE_STARTED:
                stage = event.payload_json.get("stage")
                if stage == "implement":
                    state = "IMPLEMENTING"
                elif stage == "repair":
                    state = "REPAIRING"
                elif stage == "review":
                    state = "REVIEWING"
            elif event.event_type is WorkEventType.STAGE_COMPLETED:
                if event.payload_json.get("stage") in {"implement", "repair"}:
                    state = "VERIFYING"
            elif event.event_type is WorkEventType.VERIFICATION_RECORDED:
                result = VerificationResult.model_validate(event.payload_json)
                state = "REVIEWING" if result.passed else "REPAIRING"
            elif event.event_type is WorkEventType.REVIEW_RECORDED:
                review = ReviewResult.model_validate(event.payload_json)
                if review.verdict == "accept":
                    state = "READY_TO_MERGE"
                elif review.verdict == "repair":
                    state = "REPAIRING"
                else:
                    state = "WORK_BLOCKED"
            elif event.event_type is WorkEventType.TRIAGE_CREATED:
                state = "REPAIRING"
        return state

    @staticmethod
    def _repair_count(events: list[WorkEvent]) -> int:
        return sum(
            event.event_type is WorkEventType.STAGE_COMPLETED
            and event.payload_json.get("stage") == "repair"
            for event in events
        )

    @staticmethod
    def _verification_count(events: list[WorkEvent]) -> int:
        return sum(event.event_type is WorkEventType.VERIFICATION_RECORDED for event in events)

    @staticmethod
    def _review_count(events: list[WorkEvent]) -> int:
        return sum(event.event_type is WorkEventType.REVIEW_RECORDED for event in events)

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
