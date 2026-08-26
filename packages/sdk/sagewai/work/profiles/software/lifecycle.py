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
from pathlib import Path
from typing import Protocol

from sagewai.work.capsule import TaskCapsuleCompiler
from sagewai.work.contract import WorkContract
from sagewai.work.control import OperatorController
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import (
    ActionIntent,
    ActionScope,
    Assumption,
    Reversibility,
    ReviewResult,
    VerificationResult,
    WorkItem,
    WorkRecord,
)
from sagewai.work.profiles.software.models import (
    SoftwareAttemptContext,
    SoftwareCapsuleContext,
    SoftwareContractContext,
    SoftwareRepairContext,
    SoftwareReviewContext,
    SoftwareReviewFindingContext,
    SoftwareWorkspace,
)
from sagewai.work.profiles.software.scm import (
    SoftwareWorktreeManager,
    workspace_diff,
)
from sagewai.work.runtime import (
    CapabilitySet,
    OperatorRuntime,
    WorkRequest,
)
from sagewai.work.store import WorkStore


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
        capsule_compiler: TaskCapsuleCompiler,
        worktree_manager: SoftwareWorktreeManager,
        verifier: _Verifier,
        repository: Path,
        implementer: SoftwareStageOperator,
        reviewer: SoftwareStageOperator,
        repairer: SoftwareStageOperator,
        repo_instructions: tuple[str, ...],
        verification_commands: tuple[str, ...],
    ) -> None:
        if implementer.actor_ref == reviewer.actor_ref:
            raise ValueError("implementer cannot review its own result")
        if not verification_commands:
            raise ValueError("at least one verification command is required")
        self._work_store = work_store
        self._capsule_compiler = capsule_compiler
        self._worktree_manager = worktree_manager
        self._verifier = verifier
        self._repository = repository.resolve()
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
            WorkEventType.CONTRACT_ACCEPTED,
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
            status="READY_TO_IMPLEMENT",
            contract_version=contract.version,
            active_run_id=None,
            pending_gate=None,
            profile_context={"base_sha": software.base_sha},
            created_at=now,
            updated_at=now,
        )
        await self._work_store.save_work(record)

        unsupported = self._unsupported_assumption(assumptions)
        if unsupported is not None:
            return await self._block_once(
                work_item,
                {
                    "reason": "unsupported_assumption",
                    "assumption_id": unsupported.id,
                    "decision_request": "Provide evidence or revise the contract.",
                },
            )

        project_id = work_item.project_id
        assert project_id is not None
        workspace = await self._worktree_manager.prepare(
            repository=self._repository,
            project_id=project_id,
            work_id=work_item.id,
            attempt_id=self._workspace_attempt_id,
            base_sha=software.base_sha,
        )
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
        if record.status in {"READY_TO_MERGE", "WORK_BLOCKED"}:
            return record

        events = await self._work_store.read_events(work_id, project_id=project_id)
        work_item, contract, assumptions = self._canonical_inputs(events)
        software = self._validate_inputs(work_item, contract, assumptions)
        state = self._state_from_events(events)
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
                expected_sha=self._expected_sha(events, software.base_sha),
            )
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
        current_sha = await self._worktree_manager.current_sha(workspace)
        software = self._software_capsule(contract, current_sha)
        open_assumptions = self._open_assumptions(assumptions)
        prior_refs: tuple[str, ...] = ()
        if stage == "repair":
            verification = self._latest_verification(events)
            review = self._latest_review(events)
            if verification is None:
                raise ValueError("repair requires a verification result")
            findings = review.findings if review is not None else ()
            review_refs = review.evidence_refs if review is not None else ()
            diff, relevant_files = await workspace_diff(workspace)
            context = SoftwareRepairContext(
                software=software,
                diff=diff,
                verification=verification,
                relevant_files=relevant_files,
                open_assumptions=open_assumptions,
                findings=findings,
            )
            profile_context = context.model_dump(mode="json")
            prior_refs = (*verification.evidence_refs, *review_refs)
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
            control_preconditions=(),
        )
        result = await assignment.controller.run(
            runtime=assignment.runtime,
            request=request,
            capsule=capsule,
            capabilities=assignment.capabilities,
            workspace=workspace,
        )
        if result.status != "passed":
            await self._block_once(
                work_item,
                {"reason": f"{stage}_failed", "run_id": run_id},
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
        current_sha = await self._worktree_manager.current_sha(workspace)
        diff_before, relevant_files = await workspace_diff(workspace)
        context = SoftwareReviewContext(
            software=self._software_capsule(contract, current_sha),
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
            control_preconditions=(),
        )
        result = await self._reviewer.controller.run(
            runtime=self._reviewer.runtime,
            request=request,
            capsule=capsule,
            capabilities=self._reviewer.capabilities,
            workspace=workspace,
        )
        diff_after, files_after = await workspace_diff(workspace)
        if diff_after != diff_before or files_after != relevant_files:
            await self._block_once(
                work_item,
                {"reason": "reviewer_changed_workspace", "run_id": run_id},
                actor_ref=self._reviewer.actor_ref,
            )
            return "WORK_BLOCKED"
        if result.status != "passed":
            await self._block_once(
                work_item,
                {"reason": "review_failed", "run_id": run_id},
                actor_ref=self._reviewer.actor_ref,
            )
            return "WORK_BLOCKED"

        payload = result.profile_context.get("review_result")
        if payload is None:
            await self._block_once(
                work_item,
                {"reason": "review_result_missing", "run_id": run_id},
                actor_ref=self._reviewer.actor_ref,
            )
            return "WORK_BLOCKED"
        review = ReviewResult.model_validate(payload)
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
                {"reason": "review_blocked", "run_id": run_id},
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

    async def _set_status(
        self,
        work_item: WorkItem,
        status: str,
        *,
        active_run_id: str | None = None,
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
    def _state_from_events(events: list[WorkEvent]) -> str:
        state = "READY_TO_IMPLEMENT"
        for event in events:
            if event.event_type is WorkEventType.WORK_BLOCKED:
                state = "WORK_BLOCKED"
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
        return state

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
