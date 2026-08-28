# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""v1 ActionIntent policy and deterministic control-precondition enforcement."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from sagewai.core.durability import run_with_heartbeat
from sagewai.core.state import StepStatus, WorkflowRun, WorkflowStore
from sagewai.safety.permissions import PermissionPolicy
from sagewai.work.events import (
    WorkEvent,
    WorkEventType,
    active_control_precondition_ids,
)
from sagewai.work.models import (
    ControlPrecondition,
    OperatorDisciplineReport,
    TaskCapsule,
)
from sagewai.work.runtime import (
    CapabilitySet,
    OperatorResult,
    OperatorRuntime,
    WorkRequest,
    Workspace,
)
from sagewai.work.store import WorkStore


class ControlCheckResult(BaseModel):
    """Immutable receipt from one deterministic control check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    precondition_id: str
    passed: bool
    evidence_refs: tuple[str, ...]
    detail: str | None = None
    checked_at: datetime


class ControlCheckContext(BaseModel):
    """Inputs available to a deterministic control check."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    request: WorkRequest
    precondition: ControlPrecondition
    capsule: TaskCapsule
    capabilities: CapabilitySet
    workspace: Workspace | None


class ControlCheck(Protocol):
    """Deterministic control-precondition evaluator."""

    async def evaluate(self, context: ControlCheckContext) -> ControlCheckResult: ...


class ResultValidator(Protocol):
    """Deterministic post-run result validator."""

    async def validate(
        self,
        *,
        request: WorkRequest,
        result: OperatorResult,
        workspace: Workspace | None,
    ) -> OperatorDisciplineReport: ...


class ControlDegradedError(RuntimeError):
    """An in-flight precondition failed and execution was frozen."""


class OperatorController:
    """Enforce v1 pre-run, in-flight, and post-run boundaries."""

    def __init__(
        self,
        *,
        work_store: WorkStore,
        durability_store: WorkflowStore,
        permission_policy: PermissionPolicy,
        control_checks: Mapping[str, ControlCheck],
        result_validator: ResultValidator,
        heartbeat_interval: float = 30,
    ) -> None:
        self._work_store = work_store
        self._durability_store = durability_store
        self._permission_policy = permission_policy
        self._control_checks = dict(control_checks)
        self._result_validator = result_validator
        self._heartbeat_interval = heartbeat_interval

    async def run(
        self,
        *,
        runtime: OperatorRuntime,
        request: WorkRequest,
        capsule: TaskCapsule,
        capabilities: CapabilitySet,
        workspace: Workspace | None,
    ) -> OperatorResult:
        self._validate_boundaries(request, capsule, capabilities, workspace)
        workflow_name = f"work:{request.work_id}:{request.stage}"
        durable = await self._durability_store.load_run(
            workflow_name,
            request.run_id,
        )
        if durable is not None and durable.status is StepStatus.COMPLETED:
            if durable.output_data is None:
                raise ValueError("completed durable run has no output")
            persisted = OperatorResult.model_validate(durable.output_data)
            if (
                persisted.project_id != request.project_id
                or persisted.work_id != request.work_id
                or persisted.run_id != request.run_id
            ):
                raise ValueError("completed durable result belongs to different work")
            return persisted

        risk_mismatches = self._risk_mismatches(request, capsule)
        scoped, permission_violations = await self._evaluate_intents(request, capabilities)
        if permission_violations or risk_mismatches:
            return await self._record_intent_block(
                request,
                permission_violations=tuple(permission_violations),
                risk_mismatches=risk_mismatches,
            )

        preconditions = self._applicable_preconditions(request)
        frozen_precondition_ids = await self._frozen_precondition_ids(request)
        frozen = bool(frozen_precondition_ids)
        current_precondition_ids = {precondition.id for precondition in preconditions}
        if frozen_precondition_ids - current_precondition_ids:
            return _blocked_result(
                request,
                "frozen control preconditions are missing from the request",
            )
        checks = await self._evaluate_preconditions(
            request=request,
            capsule=capsule,
            capabilities=scoped,
            workspace=workspace,
            preconditions=preconditions,
        )
        failed = tuple(result for result in checks if not result.passed)
        if failed:
            if not frozen:
                await self._record_degraded(request, failed)
            return _blocked_result(request, "control preconditions failed")
        if frozen:
            await self._append_event(
                request,
                WorkEventType.CONTROL_RESTORED,
                {
                    "precondition_ids": [precondition.id for precondition in preconditions],
                    "evidence_refs": [
                        evidence for result in checks for evidence in result.evidence_refs
                    ],
                },
            )

        if durable is None:
            durable = WorkflowRun(
                workflow_name=workflow_name,
                run_id=request.run_id,
                project_id=request.project_id,
                input_data=request.model_dump(mode="json"),
                started_at=time.time(),
            )
        durable.status = StepStatus.RUNNING
        await self._durability_store.save_run(durable)
        await self._append_event(
            request,
            WorkEventType.STAGE_STARTED,
            {
                "run_id": request.run_id,
                "stage": request.stage,
                "runtime": runtime.name,
                "action_intents": [
                    intent.model_dump(mode="json") for intent in request.action_intents
                ],
                "capability_names": [grant.name for grant in scoped.grants],
            },
        )

        async def _heartbeat() -> None:
            await self._durability_store.heartbeat(workflow_name, request.run_id)
            in_flight_checks = await self._evaluate_preconditions(
                request=request,
                capsule=capsule,
                capabilities=scoped,
                workspace=workspace,
                preconditions=preconditions,
            )
            in_flight_failed = tuple(result for result in in_flight_checks if not result.passed)
            if in_flight_failed:
                durable.status = StepStatus.WAITING
                await self._durability_store.save_run(durable)
                await self._record_degraded(request, in_flight_failed)
                raise ControlDegradedError("control preconditions failed in flight")

        try:
            result = await run_with_heartbeat(
                runtime.run(request, capsule, scoped, workspace),
                heartbeat=_heartbeat,
                interval=self._heartbeat_interval,
            )
        except ControlDegradedError:
            return _blocked_result(request, "control degraded during execution")

        report = await self._result_validator.validate(
            request=request,
            result=result,
            workspace=workspace,
        )
        await self._append_event(
            request,
            WorkEventType.OPERATOR_DISCIPLINE_RECORDED,
            report.model_dump(mode="json"),
        )
        if report.verdict != "pass":
            result = result.model_copy(update={"status": "blocked"})

        durable.status = StepStatus.COMPLETED if result.status == "passed" else StepStatus.FAILED
        durable.output_data = result.model_dump(mode="json")
        durable.completed_at = time.time()
        await self._durability_store.save_run(durable)
        await self._append_event(
            request,
            WorkEventType.EXECUTION_RECORDED,
            result.model_dump(mode="json"),
        )
        return result

    @staticmethod
    def _risk_mismatches(
        request: WorkRequest,
        capsule: TaskCapsule,
    ) -> tuple[str, ...]:
        risk_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        accepted_risk = capsule.contract.risk
        return tuple(
            f"{intent.action_id} risk {intent.risk} exceeds accepted contract risk {accepted_risk}"
            for intent in request.action_intents
            if risk_rank[intent.risk] > risk_rank[accepted_risk]
        )

    async def _record_intent_block(
        self,
        request: WorkRequest,
        *,
        permission_violations: tuple[str, ...],
        risk_mismatches: tuple[str, ...],
    ) -> OperatorResult:
        report = OperatorDisciplineReport(
            project_id=request.project_id,
            work_id=request.work_id,
            run_id=request.run_id,
            unsupported_claims=(),
            scope_violations=(),
            permission_violations=permission_violations,
            risk_mismatches=risk_mismatches,
            unnecessary_changes=(),
            output_tokens=None,
            changed_files=None,
            diff_lines=None,
            verdict="blocked",
        )
        await self._append_event(
            request,
            WorkEventType.OPERATOR_DISCIPLINE_RECORDED,
            report.model_dump(mode="json"),
        )
        violations = (*permission_violations, *risk_mismatches)
        await self._append_event(
            request,
            WorkEventType.WORK_BLOCKED,
            {"reason": "action_intent_policy", "violations": list(violations)},
        )
        return _blocked_result(request, "; ".join(violations))

    async def _evaluate_intents(
        self,
        request: WorkRequest,
        capabilities: CapabilitySet,
    ) -> tuple[CapabilitySet, list[str]]:
        scoped = capabilities.for_names(request.action_scope.allowed_capabilities)
        grants = {grant.name: grant for grant in scoped.grants}
        violations: list[str] = []
        for intent in request.action_intents:
            if intent.capability not in request.action_scope.allowed_capabilities:
                violations.append(f"{intent.action_id} capability is outside the action scope")
                continue
            grant = grants.get(intent.capability)
            if grant is None:
                violations.append(f"{intent.action_id} has no capability grant")
                continue
            if intent.required_permission not in grant.permissions:
                violations.append(f"{intent.action_id} permission is outside the capability grant")
                continue
            decision = await self._permission_policy.check_and_approve(
                intent.required_permission,
                intent.model_dump(mode="json"),
            )
            if not decision.allowed:
                violations.append(decision.reason)
        return scoped, violations

    @staticmethod
    def _applicable_preconditions(
        request: WorkRequest,
    ) -> tuple[ControlPrecondition, ...]:
        required = {
            request.stage,
            *(intent.capability for intent in request.action_intents),
        }
        return tuple(
            precondition
            for precondition in request.control_preconditions
            if required.intersection(precondition.required_for)
        )

    async def _evaluate_preconditions(
        self,
        *,
        request: WorkRequest,
        capsule: TaskCapsule,
        capabilities: CapabilitySet,
        workspace: Workspace | None,
        preconditions: tuple[ControlPrecondition, ...],
    ) -> tuple[ControlCheckResult, ...]:
        results: list[ControlCheckResult] = []
        for precondition in preconditions:
            check = self._control_checks.get(precondition.check_ref)
            if check is None:
                results.append(
                    ControlCheckResult(
                        project_id=request.project_id,
                        precondition_id=precondition.id,
                        passed=False,
                        evidence_refs=(),
                        checked_at=datetime.now(timezone.utc),
                    )
                )
                continue
            result = await check.evaluate(
                ControlCheckContext(
                    request=request,
                    precondition=precondition,
                    capsule=capsule,
                    capabilities=capabilities,
                    workspace=workspace,
                )
            )
            if result.project_id != request.project_id or result.precondition_id != precondition.id:
                raise ValueError("control check result belongs to another precondition")
            results.append(result)
        return tuple(results)

    async def _record_degraded(
        self,
        request: WorkRequest,
        failed: tuple[ControlCheckResult, ...],
    ) -> None:
        await self._append_event(
            request,
            WorkEventType.CONTROL_DEGRADED,
            {
                "run_id": request.run_id,
                "stage": request.stage,
                "failed_preconditions": [result.precondition_id for result in failed],
                "evidence_refs": [
                    evidence for result in failed for evidence in result.evidence_refs
                ],
                "details": "; ".join(
                    f"{result.precondition_id}: {result.detail or 'failed'}"
                    for result in failed
                ),
                "frozen_action_ids": [intent.action_id for intent in request.action_intents],
            },
        )

    async def _frozen_precondition_ids(
        self,
        request: WorkRequest,
    ) -> set[str]:
        events = await self._work_store.read_events(
            request.work_id,
            project_id=request.project_id,
        )
        return active_control_precondition_ids(events)

    async def _append_event(
        self,
        request: WorkRequest,
        event_type: WorkEventType,
        payload: dict,
    ) -> None:
        events = await self._work_store.read_events(
            request.work_id,
            project_id=request.project_id,
        )
        await self._work_store.append_event(
            WorkEvent(
                id=str(uuid.uuid4()),
                project_id=request.project_id,
                work_id=request.work_id,
                sequence=events[-1].sequence + 1 if events else 1,
                event_type=event_type,
                actor_type="operator_controller",
                actor_ref=None,
                payload_json=payload,
                created_at=datetime.now(timezone.utc),
            )
        )

    @staticmethod
    def _validate_boundaries(
        request: WorkRequest,
        capsule: TaskCapsule,
        capabilities: CapabilitySet,
        workspace: Workspace | None,
    ) -> None:
        if (
            capsule.project_id != request.project_id
            or capsule.work_id != request.work_id
            or capsule.stage != request.stage
            or capabilities.project_id != request.project_id
        ):
            raise ValueError("operator inputs belong to different work")
        if workspace is not None and (
            workspace.project_id != request.project_id or workspace.work_id != request.work_id
        ):
            raise ValueError("workspace belongs to different work")


def _blocked_result(request: WorkRequest, summary: str) -> OperatorResult:
    return OperatorResult(
        project_id=request.project_id,
        work_id=request.work_id,
        run_id=request.run_id,
        status="blocked",
        summary=summary,
        evidence_refs=(),
        artifact_refs=(),
        changes=(),
        verification=(),
        risks=(),
        action_results=(),
        profile_context={},
    )
