# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Planning stage: run one planning Work through the kernel's operator controller."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sagewai.work.capsule import TaskCapsuleCompiler
from sagewai.work.contract import AcceptanceCriterion, WorkContract
from sagewai.work.control import OperatorController
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import TASK_PLAN_PROFILE, ActionScope, TaskCapsule, WorkItem, WorkRecord
from sagewai.work.profiles.software.scm import SoftwareWorktreeManager
from sagewai.work.runtime import (
    CapabilitySet,
    OperatorResult,
    OperatorRuntime,
    WorkRequest,
    Workspace,
)
from sagewai.work.store import WorkStore
from sagewai.work.tasks.models import SoftwareTarget, Task
from sagewai.work.tasks.plan import PlanRejectedError, TaskPlanResult, accept_plan
from sagewai.work.tasks.scratch import ScratchWorkspaceManager

_MAX_BRIEF_CHARS = 20_000
_PLAN_ATTEMPTS = 3


class PlanningFailedError(RuntimeError):
    """The planning stage did not produce a valid TaskPlanResult."""


class TaskPlanner:
    """Create a planning Work per cycle and plan version, run it, return the result."""

    def __init__(
        self,
        *,
        work_store: WorkStore,
        capsule_compiler: TaskCapsuleCompiler,
        controller: OperatorController,
        runtime: OperatorRuntime,
        capabilities: CapabilitySet,
        worktree_manager: SoftwareWorktreeManager,
        scratch_manager: ScratchWorkspaceManager,
        actor_ref: str = "runtime:claude:planner",
    ) -> None:
        self._work_store = work_store
        self._capsule_compiler = capsule_compiler
        self._controller = controller
        self._runtime = runtime
        self._capabilities = capabilities
        self._worktree_manager = worktree_manager
        self._scratch_manager = scratch_manager
        self._actor_ref = actor_ref

    @staticmethod
    def work_id(task: Task, *, cycle: int, plan_version: int) -> str:
        return f"{task.id}:plan:{cycle}:{plan_version}"

    async def plan(
        self,
        task: Task,
        *,
        cycle: int,
        plan_version: int,
        base_sha: str | None,
        brief_text: str,
        amendments: tuple[str, ...] = (),
        assumptions: tuple[str, ...] = (),
    ) -> TaskPlanResult:
        """Run planning and return a validated result.

        A rejected result is re-asked at most ``_PLAN_ATTEMPTS`` times with the validator's error
        appended to the capsule; a failed stage is a runtime failure and is not repaired
        (section 9.2). Each re-ask is a full stage attempt with its own run id, so it reserves
        and settles its own spend and counts against ``max_stage_attempts_per_cycle``. A failed
        attempt blocks the planning Work; the coordinator retries by bumping plan_version, which
        creates a new planning Work.
        """
        work_id = self.work_id(task, cycle=cycle, plan_version=plan_version)
        run_id = f"{work_id}:plan:1"
        work_item, contract = self._planning_work(task, work_id, cycle, plan_version)
        await self._ensure_created(work_item, contract)
        workspace = await self._workspace(task, work_id, base_sha)
        capsule = await self._capsule_compiler.compile(
            work_item=work_item,
            contract=contract,
            stage="plan",
            search_text=f"{task.title} {task.brief_summary}",
            referenced_artifacts=(task.brief_ref,),
            profile_context={
                "task_plan_result_schema": TaskPlanResult.model_json_schema(),
                "task": {
                    "id": task.id,
                    "kind": task.kind.value,
                    "profile": task.profile,
                    "template_id": task.template_id,
                    "template_version": task.template_version,
                    "slots": task.slots,
                    "budget": task.budget.model_dump(mode="json"),
                },
                "brief": brief_text[:_MAX_BRIEF_CHARS],
                "amendments": list(amendments),
                "assumptions": list(assumptions),
                "verification_commands": (
                    list(task.target.verification_commands)
                    if isinstance(task.target, SoftwareTarget)
                    else []
                ),
            },
        )
        request = WorkRequest(
            project_id=task.project_id,
            work_id=work_id,
            run_id=run_id,
            stage="plan",
            action_scope=ActionScope(
                project_id=task.project_id,
                objective=(
                    "Decompose the brief into a dependency-ordered plan with an "
                    "acceptance matrix; a plan may carry clarifications only when every "
                    "clarification is defaultable with a default, and any question without a "
                    "default means ask first with steps empty"
                ),
                allowed_targets=(".",),
                allowed_capabilities=tuple(grant.name for grant in self._capabilities.grants),
            ),
            action_intents=(),
            control_preconditions=(),
        )
        rejection: str | None = None
        for attempt in range(1, _PLAN_ATTEMPTS + 1):
            attempt_run_id = f"{work_id}:plan:{attempt}"
            result = await self._controller.run(
                runtime=self._runtime,
                request=request.model_copy(update={"run_id": attempt_run_id}),
                capsule=self._with_rejection(capsule, rejection),
                capabilities=self._capabilities,
                workspace=workspace,
            )
            if result.status != "passed":
                detail = f"planning stage {result.status}: {result.summary[:500]}"
                await self._block(work_item, "planning_failed", detail)
                raise PlanningFailedError(detail)
            plan, rejection = self._validate(result, attempt_run_id, task, plan_version)
            if plan is not None:
                await self._complete(work_item, attempt_run_id, result.evidence_refs)
                return plan
            await self._observe(work_item, attempt_run_id, rejection)
        detail = f"invalid task_plan_result after {_PLAN_ATTEMPTS} attempts: {rejection[:500]}"
        await self._block(work_item, "plan_result_invalid", detail)
        raise PlanningFailedError(detail)

    @staticmethod
    def _with_rejection(capsule: TaskCapsule, rejection: str | None) -> TaskCapsule:
        """Feed the validator's own words back, the way the blueprint service repairs."""
        if rejection is None:
            return capsule
        return capsule.model_copy(
            update={
                "profile_context": {
                    **capsule.profile_context,
                    "task_plan_result_rejected": (
                        f"your previous result was rejected: {rejection}; "
                        "return a corrected task_plan_result"
                    ),
                }
            }
        )

    @staticmethod
    def _validate(
        result: OperatorResult, run_id: str, task: Task, version: int
    ) -> tuple[TaskPlanResult | None, str]:
        """Everything the contract rejects — schema and §7 acceptance alike — is repairable."""
        payload = result.profile_context.get("task_plan_result")
        if payload is None:
            return None, "planning stage returned no task_plan_result"
        try:
            plan = TaskPlanResult.model_validate(payload)
        except ValueError as exc:
            return None, f"invalid task_plan_result: {exc}"
        if plan.attempt_id != run_id:
            return None, "plan result belongs to a different attempt"
        if not plan.asks_first:
            try:
                accept_plan(plan, budget=task.budget, target=task.target, version=version)
            except PlanRejectedError as exc:
                return None, f"plan rejected: {exc}"
        return plan, ""

    async def _observe(self, work_item: WorkItem, run_id: str, rejection: str) -> None:
        events = await self._work_store.read_events(work_item.id, project_id=work_item.project_id)
        if any(
            event.event_type is WorkEventType.OBSERVATION_RECORDED
            and event.payload_json["run_id"] == run_id
            for event in events
        ):
            return
        await self._append(
            work_item,
            WorkEventType.OBSERVATION_RECORDED,
            {"run_id": run_id, "check": "task_plan_result", "passed": False, "detail": rejection},
        )

    def _planning_work(
        self,
        task: Task,
        work_id: str,
        cycle: int,
        plan_version: int,
    ) -> tuple[WorkItem, WorkContract]:
        now = datetime.now(timezone.utc)
        work_item = WorkItem(
            id=work_id,
            project_id=task.project_id,
            profile=TASK_PLAN_PROFILE,
            source="task",
            source_ref=task.id,
            title=f"Plan: {task.title}",
            description=task.brief_summary,
            target_systems=("repository",) if task.profile == "software" else ("report",),
            created_at=now,
        )
        contract = WorkContract(
            id=f"{work_id}:contract",
            project_id=task.project_id,
            work_id=work_id,
            version=1,
            goal=task.title,
            allowed_scope=(".",),
            acceptance_criteria=(
                AcceptanceCriterion(
                    id=f"{work_id}:plan-accepted",
                    project_id=task.project_id,
                    statement="a plan is accepted deterministically",
                    verification_kind="policy",
                ),
            ),
            constraints=(),
            non_goals=(),
            evidence_refs=(task.brief_ref.storage_ref,),
            assumption_ids=(),
            risk="low",
            design_required=False,
            profile_context={"task_id": task.id, "cycle": cycle, "plan_version": plan_version},
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
        if await self._work_store.load_work(work_item.id, project_id=work_item.project_id) is not None:
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

    async def _workspace(self, task: Task, work_id: str, base_sha: str | None) -> Workspace:
        if isinstance(task.target, SoftwareTarget):
            if base_sha is None:
                raise ValueError("software planning requires a base SHA")
            return await self._worktree_manager.prepare(
                repository=Path(task.target.repository_path),
                project_id=task.project_id,
                work_id=work_id,
                attempt_id="plan",
                base_sha=base_sha,
            )
        return await self._scratch_manager.prepare(
            project_id=task.project_id,
            work_id=work_id,
            attempt_id="plan",
        )

    async def _complete(self, work_item: WorkItem, run_id: str, evidence_refs: tuple[str, ...]) -> None:
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
                    {"stage": "plan", "run_id": run_id, "evidence_refs": list(evidence_refs)},
                ),
                self._event(work_item, sequence + 1, WorkEventType.WORK_COMPLETED, {"run_id": run_id}),
            )
        )
        record = await self._work_store.load_work(work_item.id, project_id=work_item.project_id)
        if record is None:
            raise PlanningFailedError("planning work projection is missing")
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
            raise PlanningFailedError("planning work projection is missing")
        record.status = "WORK_BLOCKED"
        record.updated_at = datetime.now(timezone.utc)
        await self._work_store.save_work(record)

    async def _append(self, work_item: WorkItem, event_type: WorkEventType, payload: dict[str, Any]) -> None:
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


__all__ = ["PlanningFailedError", "TaskPlanner"]
