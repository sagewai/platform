# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Assessment stage: judge one cycle at the merged head (spec section 11)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from sagewai.work.capsule import TaskCapsuleCompiler
from sagewai.work.contract import AcceptanceCriterion, WorkContract
from sagewai.work.control import OperatorController
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import TASK_ASSESS_PROFILE, ActionScope, WorkItem, WorkRecord
from sagewai.work.runtime import CapabilitySet, OperatorRuntime, WorkRequest, Workspace
from sagewai.work.store import WorkStore
from sagewai.work.tasks.assessment import MatrixResult, TaskAssessmentResult, merge_assessment
from sagewai.work.tasks.models import Task
from sagewai.work.tasks.plan import AcceptedPlan

DeterministicCheck = Callable[
    [WorkItem, WorkContract, Workspace, str], Awaitable[tuple[MatrixResult, ...]]
]


class AssessmentFailedError(RuntimeError):
    """The assessor stage did not produce a valid TaskAssessmentResult."""


class TaskAssessor:
    """Create one read-only assessor Work per cycle, run it, and merge its verdict."""

    def __init__(
        self,
        *,
        work_store: WorkStore,
        capsule_compiler: TaskCapsuleCompiler,
        controller: OperatorController,
        runtime: OperatorRuntime,
        capabilities: CapabilitySet,
        actor_ref: str = "runtime:claude:assessor",
    ) -> None:
        self._work_store = work_store
        self._capsule_compiler = capsule_compiler
        self._controller = controller
        self._runtime = runtime
        self._capabilities = capabilities
        self._actor_ref = actor_ref

    @staticmethod
    def work_id(task: Task, *, cycle: int, plan_version: int) -> str:
        """One assessor Work per plan version: a replan re-assesses the same cycle at a new head."""
        return f"{task.id}:assess:{cycle}:{plan_version}"

    async def assess(
        self,
        task: Task,
        *,
        cycle: int,
        plan_version: int,
        plan: AcceptedPlan,
        outcomes: Mapping[str, str],
        workspace: Workspace,
        evidence: tuple[str, ...],
        profile_context: dict[str, Any],
        deterministic: DeterministicCheck | None = None,
    ) -> TaskAssessmentResult:
        work_id = self.work_id(task, cycle=cycle, plan_version=plan_version)
        run_id = f"{work_id}:assess:1"
        work_item, contract = self._assessment_work(task, work_id, cycle, plan, profile_context)
        await self._ensure_created(work_item, contract)
        checked: tuple[MatrixResult, ...] = ()
        if deterministic is not None:
            try:
                checked = await deterministic(work_item, contract, workspace, f"{work_id}:verify:1")
            except Exception as exc:
                detail = f"assessment verification failed: {type(exc).__name__}: {exc}"[:500]
                await self._block(work_item, "assessment_verification_failed", detail)
                raise AssessmentFailedError(detail) from exc
        capsule = await self._capsule_compiler.compile(
            work_item=work_item,
            contract=contract,
            stage="assess",
            search_text=f"{task.title} {task.brief_summary}",
            referenced_artifacts=(task.brief_ref,),
            profile_context={
                "task_assessment_result_schema": TaskAssessmentResult.model_json_schema(),
                "attempt_id": run_id,
                "acceptance_matrix": [
                    item.model_dump(mode="json") for item in plan.acceptance_matrix
                ],
                "steps": [step.model_dump(mode="json") for step in plan.steps],
                "step_outcomes": dict(outcomes),
                "deterministic_results": [item.model_dump(mode="json") for item in checked],
                "evidence_refs": list(evidence),
            },
        )
        request = WorkRequest(
            project_id=task.project_id,
            work_id=work_id,
            run_id=run_id,
            stage="assess",
            action_scope=ActionScope(
                project_id=task.project_id,
                objective=(
                    "Judge every policy matrix item against the delivered result and "
                    "the cited evidence; propose gaps, never changes"
                ),
                allowed_targets=(".",),
                allowed_capabilities=tuple(grant.name for grant in self._capabilities.grants),
            ),
            action_intents=(),
            control_preconditions=(),
        )
        result = await self._controller.run(
            runtime=self._runtime,
            request=request,
            capsule=capsule,
            capabilities=self._capabilities,
            workspace=workspace,
        )
        if result.status != "passed":
            detail = f"assessor stage {result.status}: {result.summary[:500]}"
            await self._block(work_item, "assessment_failed", detail)
            raise AssessmentFailedError(detail)
        payload = result.profile_context.get("task_assessment_result")
        if payload is None:
            detail = "assessor stage returned no task_assessment_result"
            await self._block(work_item, "assessment_result_missing", detail)
            raise AssessmentFailedError(detail)
        try:
            judged = TaskAssessmentResult.model_validate(payload)
        except ValueError as exc:
            detail = f"invalid task_assessment_result: {exc}"
            await self._block(work_item, "assessment_result_invalid", detail)
            raise AssessmentFailedError(detail) from exc
        if judged.attempt_id != run_id:
            detail = "assessment result belongs to a different attempt"
            await self._block(work_item, "assessment_result_attempt_mismatch", detail)
            raise AssessmentFailedError(detail)
        await self._complete(work_item, run_id, result.evidence_refs)
        return merge_assessment(
            plan,
            attempt_id=run_id,
            outcomes=outcomes,
            deterministic=checked,
            assessor=judged,
        )

    @staticmethod
    def matrix_criterion_id(work_id: str, item_id: str) -> str:
        return f"{work_id}:matrix:{item_id}"

    def _assessment_work(
        self,
        task: Task,
        work_id: str,
        cycle: int,
        plan: AcceptedPlan,
        profile_context: dict[str, Any],
    ) -> tuple[WorkItem, WorkContract]:
        now = datetime.now(timezone.utc)
        work_item = WorkItem(
            id=work_id,
            project_id=task.project_id,
            profile=TASK_ASSESS_PROFILE,
            source="task",
            source_ref=task.id,
            title=f"Assess: {task.title}",
            description=task.brief_summary,
            target_systems=("repository",) if task.profile == "software" else ("report",),
            created_at=now,
        )
        criteria = [
            AcceptanceCriterion(
                id=f"{work_id}:assessment",
                project_id=task.project_id,
                statement="the cycle is judged against its acceptance matrix",
                verification_kind="policy",
            )
        ]
        criteria.extend(
            AcceptanceCriterion(
                id=self.matrix_criterion_id(work_id, item.id),
                project_id=task.project_id,
                statement=item.statement,
                verification_kind="deterministic",
            )
            for item in plan.acceptance_matrix
            if item.verification_kind == "deterministic"
        )
        contract = WorkContract(
            id=f"{work_id}:contract",
            project_id=task.project_id,
            work_id=work_id,
            version=1,
            goal=f"Assess cycle {cycle} of {task.title}",
            allowed_scope=(".",),
            acceptance_criteria=tuple(criteria),
            constraints=(),
            non_goals=(),
            evidence_refs=(task.brief_ref.storage_ref,),
            assumption_ids=(),
            risk="low",
            design_required=False,
            profile_context=profile_context,
        )
        return work_item, contract

    async def _ensure_created(self, work_item: WorkItem, contract: WorkContract) -> None:
        events = await self._work_store.read_events(work_item.id, project_id=work_item.project_id)
        if not any(event.event_type is WorkEventType.WORK_CREATED for event in events):
            sequence = events[-1].sequence + 1 if events else 1
            await self._work_store.append_events(
                (
                    self._event(
                        work_item,
                        sequence,
                        WorkEventType.WORK_CREATED,
                        work_item.model_dump(mode="json"),
                    ),
                    self._event(
                        work_item,
                        sequence + 1,
                        WorkEventType.CONTRACT_PROPOSED,
                        contract.model_dump(mode="json"),
                    ),
                )
            )
        if (
            await self._work_store.load_work(work_item.id, project_id=work_item.project_id)
            is not None
        ):
            return
        now = datetime.now(timezone.utc)
        await self._work_store.save_work(
            WorkRecord(
                work_id=work_item.id,
                project_id=work_item.project_id,
                source_ref=work_item.source_ref,
                profile=work_item.profile,
                status="PLANNING",
                contract_version=1,
                active_run_id=None,
                pending_gate=None,
                profile_context=dict(contract.profile_context),
                created_at=now,
                updated_at=now,
            )
        )

    async def _complete(
        self, work_item: WorkItem, run_id: str, evidence_refs: tuple[str, ...]
    ) -> None:
        events = await self._work_store.read_events(work_item.id, project_id=work_item.project_id)
        if any(event.event_type is WorkEventType.WORK_COMPLETED for event in events):
            return
        sequence = events[-1].sequence + 1 if events else 1
        await self._work_store.append_events(
            (
                self._event(
                    work_item,
                    sequence,
                    WorkEventType.STAGE_COMPLETED,
                    {"stage": "assess", "run_id": run_id, "evidence_refs": list(evidence_refs)},
                ),
                self._event(
                    work_item, sequence + 1, WorkEventType.WORK_COMPLETED, {"run_id": run_id}
                ),
            )
        )
        record = await self._work_store.load_work(work_item.id, project_id=work_item.project_id)
        if record is None:
            raise AssessmentFailedError("assessment work projection is missing")
        record.status = "COMPLETE"
        record.updated_at = datetime.now(timezone.utc)
        await self._work_store.save_work(record)

    async def _block(self, work_item: WorkItem, reason: str, detail: str) -> None:
        events = await self._work_store.read_events(work_item.id, project_id=work_item.project_id)
        if any(event.event_type is WorkEventType.WORK_BLOCKED for event in events):
            return
        await self._append(
            work_item,
            WorkEventType.WORK_BLOCKED,
            {"reason": reason, "decision_request": detail},
        )
        record = await self._work_store.load_work(work_item.id, project_id=work_item.project_id)
        if record is None:
            raise AssessmentFailedError("assessment work projection is missing")
        record.status = "WORK_BLOCKED"
        record.updated_at = datetime.now(timezone.utc)
        await self._work_store.save_work(record)

    async def _append(
        self, work_item: WorkItem, event_type: WorkEventType, payload: dict[str, Any]
    ) -> None:
        events = await self._work_store.read_events(work_item.id, project_id=work_item.project_id)
        sequence = events[-1].sequence + 1 if events else 1
        await self._work_store.append_event(self._event(work_item, sequence, event_type, payload))

    def _event(
        self,
        work_item: WorkItem,
        sequence: int,
        event_type: WorkEventType,
        payload: dict[str, Any],
    ) -> WorkEvent:
        return WorkEvent(
            id=str(uuid.uuid4()),
            project_id=work_item.project_id,
            work_id=work_item.id,
            sequence=sequence,
            event_type=event_type,
            actor_type="system",
            actor_ref=self._actor_ref,
            payload_json=payload,
            created_at=datetime.now(timezone.utc),
        )


__all__ = ["AssessmentFailedError", "DeterministicCheck", "TaskAssessor"]
