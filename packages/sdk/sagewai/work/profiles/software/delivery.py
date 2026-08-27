# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Release, delivery, observation, and rollback contracts for software Work.

Release builds are policy-gated local PURE actions. Every provider action that
can change configured exposure has deterministic control preconditions.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from sagewai.work.control import (
    ControlCheckResult,
    ControlDegradedError,
)
from sagewai.work.events import (
    WorkEvent,
    WorkEventType,
    active_control_precondition_ids,
)
from sagewai.work.models import (
    ActionRequest,
    ControlPrecondition,
    GateDecision,
    Reversibility,
)
from sagewai.work.store import WorkStore


class HealthVerdict(str, Enum):
    """Deterministic control verdict from one observation window."""

    PASS = "pass"
    HOLD = "hold"
    FAIL = "fail"


class BlastRadius(BaseModel):
    """One explicit dimension and value controlling deployment exposure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: Literal[
        "traffic",
        "instances",
        "tenant",
        "cohort",
        "region",
        "availability_zone",
        "feature_flag",
        "custom",
    ]
    value: str = Field(min_length=1)


class ReleaseCandidate(BaseModel):
    """Immutable, verified artifact promoted unchanged between environments."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    work_id: str
    commit_sha: str
    artifact_ref: str
    artifact_digest: str
    config_revision: str | None
    verification_ref: str
    review_ref: str


class Deployment(BaseModel):
    """Immutable receipt for one candidate at one exposure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    work_id: str
    release_candidate_id: str
    environment: str
    exposure: BlastRadius
    provider_ref: str
    status: Literal["active", "rolled_back"]


class HealthGate(BaseModel):
    """Project-scoped health rule and its deterministic failure severity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    description: str
    check_ref: str
    failure_verdict: Literal[HealthVerdict.HOLD, HealthVerdict.FAIL]


class HealthGateResult(BaseModel):
    """Immutable result for one health gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    gate_id: str
    passed: bool
    evidence_refs: tuple[str, ...]


class ObservationResult(BaseModel):
    """Immutable aggregate verdict for one deployment observation window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    work_id: str
    deployment_id: str
    verdict: HealthVerdict
    gate_results: tuple[HealthGateResult, ...]
    evidence_refs: tuple[str, ...]


class DeliveryControlRequest(BaseModel):
    """Bounded inputs for deterministic delivery pre-flight checks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    work_id: str
    action: Literal["deploy", "promote", "observe", "rollback"]
    candidate: ReleaseCandidate
    known_good_candidate: ReleaseCandidate | None
    expected_duration_seconds: int = Field(gt=0)


class ReleaseProvider(Protocol):
    """Build exactly one immutable release candidate from a commit."""

    async def build(self, commit_sha: str) -> ReleaseCandidate: ...


class DeploymentProvider(Protocol):
    """Execute deployment-platform mutations behind lifecycle control."""

    async def deploy(
        self,
        candidate: ReleaseCandidate,
        environment: str,
        exposure: BlastRadius,
    ) -> Deployment: ...

    async def promote(
        self,
        deployment: Deployment,
        exposure: BlastRadius,
    ) -> Deployment: ...

    async def rollback(
        self,
        deployment: Deployment,
        known_good_candidate: ReleaseCandidate,
    ) -> Deployment: ...


class ObservationProvider(Protocol):
    """Read deterministic health evidence for one deployment."""

    async def observe(
        self,
        deployment: Deployment,
        gates: tuple[HealthGate, ...],
        window_seconds: int,
    ) -> ObservationResult: ...


class DeliveryControlProbe(Protocol):
    """Evaluate all control preconditions required by one delivery action."""

    async def evaluate(
        self,
        request: DeliveryControlRequest,
        preconditions: tuple[ControlPrecondition, ...],
    ) -> tuple[ControlCheckResult, ...]: ...


class DeliveryActionDeniedError(RuntimeError):
    """A delivery action was denied before a provider side effect."""


class DeliveryApprovalRequiredError(RuntimeError):
    """A delivery action is waiting at a policy approval boundary."""


class DeliveryLifecycle:
    """Persist and enforce release promotion using provider contracts."""

    def __init__(
        self,
        *,
        work_store: WorkStore,
        release_provider: ReleaseProvider,
        deployment_provider: DeploymentProvider,
        observation_provider: ObservationProvider,
        control_probe: DeliveryControlProbe,
        control_preconditions: tuple[ControlPrecondition, ...],
        action_policy: Callable[[ActionRequest], GateDecision],
    ) -> None:
        self._work_store = work_store
        self._release_provider = release_provider
        self._deployment_provider = deployment_provider
        self._observation_provider = observation_provider
        self._control_probe = control_probe
        self._control_preconditions = control_preconditions
        self._action_policy = action_policy

    async def build(
        self,
        *,
        work_id: str,
        project_id: str,
        commit_sha: str,
        evidence_refs: tuple[str, ...],
    ) -> ReleaseCandidate:
        """Build and record one immutable candidate after action-policy approval."""

        await self._require_work(work_id, project_id)
        await self._authorize(
            ActionRequest(
                project_id=project_id,
                action="build_release",
                work_id=work_id,
                risk="low",
                reversibility=Reversibility.PURE,
                scope=commit_sha,
                evidence_refs=evidence_refs,
            )
        )
        candidate = await self._release_provider.build(commit_sha)
        if (
            candidate.project_id != project_id
            or candidate.work_id != work_id
            or candidate.commit_sha != commit_sha
        ):
            raise ValueError("release candidate belongs to different work")
        await self._append(
            project_id=project_id,
            work_id=work_id,
            event_type=WorkEventType.RELEASE_CREATED,
            payload={"release_candidate": candidate.model_dump(mode="json")},
            actor_ref="release_provider",
        )
        return candidate

    async def deploy(
        self,
        candidate: ReleaseCandidate,
        *,
        environment: str,
        exposure: BlastRadius,
        known_good_candidate: ReleaseCandidate,
        evidence_refs: tuple[str, ...],
        expected_duration_seconds: int,
    ) -> Deployment:
        """Deploy a candidate only while authority, observation, and rollback pass."""

        await self._require_candidate(candidate)
        if await self._has_deployment(candidate, environment):
            raise DeliveryActionDeniedError(
                "candidate already deployed to environment; use promote"
            )
        if await self._candidate_was_rolled_back(candidate):
            raise DeliveryActionDeniedError("rolled-back candidate cannot be deployed")
        if await self._candidate_has_nonpassing_delivery(candidate):
            raise DeliveryActionDeniedError(
                "candidate has a non-passing observation or unobserved deployment"
            )
        await self._preflight(
            DeliveryControlRequest(
                project_id=candidate.project_id,
                work_id=candidate.work_id,
                action="deploy",
                candidate=candidate,
                known_good_candidate=known_good_candidate,
                expected_duration_seconds=expected_duration_seconds,
            )
        )
        action_name = "deploy_production" if environment == "production" else "deploy_staging"
        await self._authorize(
            ActionRequest(
                project_id=candidate.project_id,
                action=action_name,
                work_id=candidate.work_id,
                risk="high" if environment == "production" else "medium",
                reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
                scope=(f"{candidate.id}:{environment}:" f"{exposure.dimension}:{exposure.value}"),
                evidence_refs=evidence_refs,
            )
        )
        deployment = await self._deployment_provider.deploy(
            candidate,
            environment,
            exposure,
        )
        self._validate_deployment(deployment, candidate, environment, exposure)
        await self._record_deployment(deployment, action_name)
        return deployment

    async def observe(
        self,
        deployment: Deployment,
        *,
        gates: tuple[HealthGate, ...],
        window_seconds: int,
    ) -> ObservationResult:
        """Observe one immutable deployment and persist its deterministic verdict."""

        if not gates:
            raise ValueError("at least one health gate is required")
        candidate = await self._candidate_for(deployment)
        if any(gate.project_id != deployment.project_id for gate in gates):
            raise ValueError("health gate belongs to a different project")
        await self._preflight(
            DeliveryControlRequest(
                project_id=deployment.project_id,
                work_id=deployment.work_id,
                action="observe",
                candidate=candidate,
                known_good_candidate=None,
                expected_duration_seconds=window_seconds,
            )
        )
        result = await self._observation_provider.observe(
            deployment,
            gates,
            window_seconds,
        )
        self._validate_observation(result, deployment, gates)
        await self._append(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            event_type=WorkEventType.OBSERVATION_RECORDED,
            payload={"observation": result.model_dump(mode="json")},
            actor_ref="observation_provider",
        )
        return result

    async def promote(
        self,
        deployment: Deployment,
        *,
        exposure: BlastRadius,
        known_good_candidate: ReleaseCandidate,
        evidence_refs: tuple[str, ...],
        expected_duration_seconds: int,
    ) -> Deployment:
        """Increase exposure only after the latest health verdict is PASS."""

        candidate = await self._candidate_for(deployment)
        if deployment.status != "active" or await self._candidate_was_rolled_back(candidate):
            raise DeliveryActionDeniedError("rolled-back deployment cannot be promoted")
        observation = await self._latest_observation(deployment)
        if observation is None or observation.verdict is not HealthVerdict.PASS:
            raise DeliveryActionDeniedError("promotion requires a passing observation")
        await self._preflight(
            DeliveryControlRequest(
                project_id=deployment.project_id,
                work_id=deployment.work_id,
                action="promote",
                candidate=candidate,
                known_good_candidate=known_good_candidate,
                expected_duration_seconds=expected_duration_seconds,
            )
        )
        await self._authorize(
            ActionRequest(
                project_id=deployment.project_id,
                action="promote_rollout",
                work_id=deployment.work_id,
                risk="high",
                reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
                scope=(
                    f"{candidate.id}:{deployment.id}:{deployment.environment}:"
                    f"{exposure.dimension}:{exposure.value}"
                ),
                evidence_refs=evidence_refs,
            )
        )
        promoted = await self._deployment_provider.promote(deployment, exposure)
        self._validate_deployment(
            promoted,
            candidate,
            deployment.environment,
            exposure,
        )
        await self._record_deployment(promoted, "promote_rollout")
        return promoted

    async def rollback(
        self,
        deployment: Deployment,
        *,
        known_good_candidate: ReleaseCandidate,
        evidence_refs: tuple[str, ...],
        expected_duration_seconds: int,
    ) -> Deployment:
        """Rollback only when the rollback action's own preconditions pass."""

        candidate = await self._candidate_for(deployment)
        if deployment.status == "rolled_back":
            raise DeliveryActionDeniedError("deployment is already rolled back")
        await self._preflight(
            DeliveryControlRequest(
                project_id=deployment.project_id,
                work_id=deployment.work_id,
                action="rollback",
                candidate=candidate,
                known_good_candidate=known_good_candidate,
                expected_duration_seconds=expected_duration_seconds,
            )
        )
        await self._authorize(
            ActionRequest(
                project_id=deployment.project_id,
                action="rollback",
                work_id=deployment.work_id,
                risk="medium",
                reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
                scope=(
                    f"{candidate.id}:{known_good_candidate.id}:"
                    f"{deployment.id}:{deployment.provider_ref}"
                ),
                evidence_refs=evidence_refs,
            )
        )
        rolled_back = await self._deployment_provider.rollback(
            deployment,
            known_good_candidate,
        )
        if (
            rolled_back.project_id != deployment.project_id
            or rolled_back.work_id != deployment.work_id
            or rolled_back.release_candidate_id != known_good_candidate.id
            or rolled_back.environment != deployment.environment
            or rolled_back.status != "rolled_back"
        ):
            raise ValueError("rollback result belongs to a different deployment")
        await self._append(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            event_type=WorkEventType.ROLLBACK_RECORDED,
            payload={
                "source_deployment_id": deployment.id,
                "source_release_candidate_id": candidate.id,
                "deployment": rolled_back.model_dump(mode="json"),
                "known_good_release_candidate": known_good_candidate.model_dump(mode="json"),
            },
            actor_ref="deployment_provider",
        )
        return rolled_back

    async def _preflight(self, request: DeliveryControlRequest) -> None:
        preconditions = tuple(
            precondition
            for precondition in self._control_preconditions
            if request.action in precondition.required_for
        )
        if not preconditions:
            raise ValueError("delivery action has no configured control preconditions")
        if any(precondition.project_id != request.project_id for precondition in preconditions):
            raise ValueError("delivery precondition belongs to a different project")
        results = await self._control_probe.evaluate(request, preconditions)
        known_good_problem = await self._known_good_problem(request)
        if known_good_problem is not None:
            reversibility_ids = {
                precondition.id
                for precondition in preconditions
                if precondition.kind.value == "reversibility"
            }
            if not reversibility_ids:
                raise ValueError("delivery action has no reversibility control precondition")
            results = tuple(
                result.model_copy(
                    update={
                        "passed": False,
                        "detail": known_good_problem,
                    }
                )
                if result.precondition_id in reversibility_ids
                else result
                for result in results
            )
        expected_ids = {precondition.id for precondition in preconditions}
        if (
            len(results) != len(preconditions)
            or {result.precondition_id for result in results} != expected_ids
        ):
            raise ValueError("delivery control probe did not cover every precondition")
        if any(result.project_id != request.project_id for result in results):
            raise ValueError("delivery precondition belongs to a different project")

        active = await self._active_degradations(request.work_id, request.project_id)
        failed = tuple(result for result in results if not result.passed)
        if failed:
            newly_failed = tuple(
                result for result in failed if result.precondition_id not in active
            )
            if newly_failed:
                await self._append(
                    project_id=request.project_id,
                    work_id=request.work_id,
                    event_type=WorkEventType.CONTROL_DEGRADED,
                    payload={
                        "failed_preconditions": [result.precondition_id for result in newly_failed],
                        "evidence_refs": [
                            ref for result in newly_failed for ref in result.evidence_refs
                        ],
                        "details": "; ".join(
                            f"{result.precondition_id}: {result.detail or 'failed'}"
                            for result in newly_failed
                        ),
                        "frozen_action_ids": [request.action],
                    },
                    actor_ref="delivery_control",
                )
            failed_ids = ", ".join(result.precondition_id for result in failed)
            raise ControlDegradedError(failed_ids)

        passed_ids = {result.precondition_id for result in results}
        if request.action not in {"observe", "rollback"} and active - passed_ids:
            raise ControlDegradedError(", ".join(sorted(active - passed_ids)))
        restored = active & passed_ids
        if restored:
            await self._append(
                project_id=request.project_id,
                work_id=request.work_id,
                event_type=WorkEventType.CONTROL_RESTORED,
                payload={
                    "precondition_ids": sorted(restored),
                    "evidence_refs": [ref for result in results for ref in result.evidence_refs],
                },
                actor_ref="delivery_control",
            )

    async def _authorize(self, request: ActionRequest) -> None:
        gate_id = f"{request.action}:{request.work_id}:{request.scope}"
        events = await self._work_store.read_events(
            request.work_id,
            project_id=request.project_id,
        )
        decided = self._gate_event(events, WorkEventType.GATE_DECIDED, gate_id)
        requested = self._gate_event(events, WorkEventType.GATE_REQUESTED, gate_id)
        if decided is not None:
            decision = GateDecision(decided.payload_json["decision"])
        elif requested is not None:
            await self._set_pending_gate(request, gate_id)
            raise DeliveryApprovalRequiredError(request.action)
        else:
            decision = GateDecision(self._action_policy(request))
            if decision is GateDecision.REQUIRE_APPROVAL:
                await self._append(
                    project_id=request.project_id,
                    work_id=request.work_id,
                    event_type=WorkEventType.GATE_REQUESTED,
                    payload={
                        "gate_id": gate_id,
                        "question": f"Approve {request.action} for {request.scope}.",
                        "action": request.model_dump(mode="json"),
                        "evidence_refs": list(request.evidence_refs),
                    },
                    actor_ref="delivery_policy",
                )
                await self._set_pending_gate(request, gate_id)
                raise DeliveryApprovalRequiredError(request.action)
            await self._append(
                project_id=request.project_id,
                work_id=request.work_id,
                event_type=WorkEventType.GATE_DECIDED,
                payload={
                    "gate_id": gate_id,
                    "decision": decision.value,
                    "action": request.model_dump(mode="json"),
                },
                actor_ref="delivery_policy",
            )
        if decision is GateDecision.ALLOW:
            await self._clear_pending_gate(request, gate_id)
            return
        await self._append(
            project_id=request.project_id,
            work_id=request.work_id,
            event_type=WorkEventType.WORK_BLOCKED,
            payload={
                "reason": "delivery_policy_denied",
                "decision_request": f"Revise policy for {request.action} or stop the work.",
                "evidence_refs": list(request.evidence_refs),
            },
            actor_ref="delivery_policy",
        )
        record = await self._work_store.load_work(
            request.work_id,
            project_id=request.project_id,
        )
        if record is None:
            raise KeyError(request.work_id)
        await self._work_store.save_work(
            record.model_copy(
                update={
                    "status": "WORK_BLOCKED",
                    "pending_gate": None,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        )
        raise DeliveryActionDeniedError(request.action)

    async def _set_pending_gate(self, request: ActionRequest, gate_id: str) -> None:
        record = await self._work_store.load_work(
            request.work_id,
            project_id=request.project_id,
        )
        if record is None:
            raise KeyError(request.work_id)
        if record.pending_gate == gate_id:
            return
        await self._work_store.save_work(
            record.model_copy(
                update={
                    "pending_gate": gate_id,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        )

    async def _clear_pending_gate(self, request: ActionRequest, gate_id: str) -> None:
        record = await self._work_store.load_work(
            request.work_id,
            project_id=request.project_id,
        )
        if record is None:
            raise KeyError(request.work_id)
        if record.pending_gate != gate_id:
            return
        await self._work_store.save_work(
            record.model_copy(
                update={
                    "pending_gate": None,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        )

    async def _require_work(self, work_id: str, project_id: str) -> None:
        if await self._work_store.load_work(work_id, project_id=project_id) is None:
            raise KeyError(work_id)

    async def _require_candidate(self, candidate: ReleaseCandidate) -> None:
        await self._require_work(candidate.work_id, candidate.project_id)
        canonical = await self._candidate_by_id(
            candidate.work_id,
            candidate.project_id,
            candidate.id,
        )
        if canonical != candidate:
            raise ValueError("release candidate is not canonical for this WorkItem")

    async def _candidate_for(self, deployment: Deployment) -> ReleaseCandidate:
        await self._require_work(deployment.work_id, deployment.project_id)
        canonical_deployment = await self._deployment_by_id(
            deployment.work_id,
            deployment.project_id,
            deployment.id,
        )
        if canonical_deployment != deployment:
            raise ValueError("deployment is not canonical for this WorkItem")
        try:
            return await self._candidate_by_id(
                deployment.work_id,
                deployment.project_id,
                deployment.release_candidate_id,
            )
        except ValueError:
            events = await self._work_store.read_events(
                deployment.work_id,
                project_id=deployment.project_id,
            )
            for event in reversed(events):
                if event.event_type is not WorkEventType.ROLLBACK_RECORDED:
                    continue
                recorded = Deployment.model_validate(event.payload_json["deployment"])
                if recorded.id != deployment.id:
                    continue
                candidate = ReleaseCandidate.model_validate(
                    event.payload_json["known_good_release_candidate"]
                )
                if candidate.id == deployment.release_candidate_id:
                    return candidate
            raise

    async def _candidate_by_id(
        self,
        work_id: str,
        project_id: str,
        candidate_id: str,
    ) -> ReleaseCandidate:
        events = await self._work_store.read_events(work_id, project_id=project_id)
        for event in reversed(events):
            if event.event_type is not WorkEventType.RELEASE_CREATED:
                continue
            candidate = ReleaseCandidate.model_validate(event.payload_json["release_candidate"])
            if candidate.id == candidate_id:
                return candidate
        raise ValueError("release candidate is not recorded for this WorkItem")

    async def _deployment_by_id(
        self,
        work_id: str,
        project_id: str,
        deployment_id: str,
    ) -> Deployment:
        events = await self._work_store.read_events(work_id, project_id=project_id)
        for event in reversed(events):
            if event.event_type not in {
                WorkEventType.DEPLOYMENT_RECORDED,
                WorkEventType.ROLLBACK_RECORDED,
            }:
                continue
            deployment = Deployment.model_validate(event.payload_json["deployment"])
            if deployment.id == deployment_id:
                return deployment
        raise ValueError("deployment is not recorded for this WorkItem")

    async def _has_deployment(
        self,
        candidate: ReleaseCandidate,
        environment: str,
    ) -> bool:
        events = await self._work_store.read_events(
            candidate.work_id,
            project_id=candidate.project_id,
        )
        for event in events:
            if event.event_type is not WorkEventType.DEPLOYMENT_RECORDED:
                continue
            deployment = Deployment.model_validate(event.payload_json["deployment"])
            if (
                deployment.release_candidate_id == candidate.id
                and deployment.environment == environment
            ):
                return True
        return False

    async def _candidate_was_rolled_back(
        self,
        candidate: ReleaseCandidate,
    ) -> bool:
        events = await self._work_store.read_events(
            candidate.work_id,
            project_id=candidate.project_id,
        )
        return any(
            event.event_type is WorkEventType.ROLLBACK_RECORDED
            and event.payload_json.get("source_release_candidate_id") == candidate.id
            for event in events
        )

    async def _candidate_has_nonpassing_delivery(
        self,
        candidate: ReleaseCandidate,
    ) -> bool:
        events = await self._work_store.read_events(
            candidate.work_id,
            project_id=candidate.project_id,
        )
        candidate_deployments: set[str] = set()
        latest_verdicts: dict[str, HealthVerdict] = {}
        for event in events:
            if event.event_type is WorkEventType.DEPLOYMENT_RECORDED:
                deployment = Deployment.model_validate(event.payload_json["deployment"])
                if deployment.release_candidate_id == candidate.id:
                    candidate_deployments.add(deployment.id)
            elif event.event_type is WorkEventType.OBSERVATION_RECORDED:
                observation = ObservationResult.model_validate(event.payload_json["observation"])
                if observation.deployment_id not in candidate_deployments:
                    continue
                if observation.verdict is HealthVerdict.FAIL:
                    return True
                latest_verdicts[observation.deployment_id] = observation.verdict
        return any(
            latest_verdicts.get(deployment_id) is not HealthVerdict.PASS
            for deployment_id in candidate_deployments
        )

    @staticmethod
    def _gate_event(
        events: list[WorkEvent],
        event_type: WorkEventType,
        gate_id: str,
    ) -> WorkEvent | None:
        return next(
            (
                event
                for event in reversed(events)
                if event.event_type is event_type and event.payload_json.get("gate_id") == gate_id
            ),
            None,
        )

    async def _latest_observation(
        self,
        deployment: Deployment,
    ) -> ObservationResult | None:
        events = await self._work_store.read_events(
            deployment.work_id,
            project_id=deployment.project_id,
        )
        deployment_sequence = max(
            (
                event.sequence
                for event in events
                if event.event_type
                in {
                    WorkEventType.DEPLOYMENT_RECORDED,
                    WorkEventType.ROLLBACK_RECORDED,
                }
                and Deployment.model_validate(event.payload_json["deployment"]) == deployment
            ),
            default=0,
        )
        for event in reversed(events):
            if event.sequence <= deployment_sequence:
                break
            if event.event_type is not WorkEventType.OBSERVATION_RECORDED:
                continue
            observation = ObservationResult.model_validate(event.payload_json["observation"])
            if observation.deployment_id == deployment.id:
                return observation
        return None

    async def _active_degradations(self, work_id: str, project_id: str) -> set[str]:
        events = await self._work_store.read_events(work_id, project_id=project_id)
        return active_control_precondition_ids(events)

    async def _record_deployment(self, deployment: Deployment, action: str) -> None:
        await self._append(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            event_type=WorkEventType.DEPLOYMENT_RECORDED,
            payload={
                "action": action,
                "deployment": deployment.model_dump(mode="json"),
            },
            actor_ref="deployment_provider",
        )

    async def _append(
        self,
        *,
        project_id: str,
        work_id: str,
        event_type: WorkEventType,
        payload: dict,
        actor_ref: str,
    ) -> None:
        events = await self._work_store.read_events(work_id, project_id=project_id)
        await self._work_store.append_event(
            WorkEvent(
                id=str(uuid.uuid4()),
                project_id=project_id,
                work_id=work_id,
                sequence=events[-1].sequence + 1 if events else 1,
                event_type=event_type,
                actor_type="delivery",
                actor_ref=actor_ref,
                payload_json=payload,
                created_at=datetime.now(timezone.utc),
            )
        )

    async def _known_good_problem(
        self,
        request: DeliveryControlRequest,
    ) -> str | None:
        if request.action == "observe":
            return None
        candidate = request.candidate
        known_good_candidate = request.known_good_candidate
        if known_good_candidate is None:
            return "known-good release candidate is missing"
        if known_good_candidate.project_id != candidate.project_id:
            return "known-good candidate belongs to a different project"
        if known_good_candidate.id == candidate.id:
            return "known-good candidate must differ from the candidate under delivery"
        try:
            canonical = await self._candidate_by_id(
                known_good_candidate.work_id,
                known_good_candidate.project_id,
                known_good_candidate.id,
            )
        except ValueError:
            return "known-good release candidate is not recorded"
        if canonical != known_good_candidate:
            return "known-good release candidate is not canonical"
        return None

    @staticmethod
    def _validate_deployment(
        deployment: Deployment,
        candidate: ReleaseCandidate,
        environment: str,
        exposure: BlastRadius,
    ) -> None:
        if (
            deployment.project_id != candidate.project_id
            or deployment.work_id != candidate.work_id
            or deployment.release_candidate_id != candidate.id
            or deployment.environment != environment
            or deployment.exposure != exposure
            or deployment.status != "active"
        ):
            raise ValueError("deployment result does not match the requested candidate")

    @staticmethod
    def _validate_observation(
        result: ObservationResult,
        deployment: Deployment,
        gates: tuple[HealthGate, ...],
    ) -> None:
        if (
            result.project_id != deployment.project_id
            or result.work_id != deployment.work_id
            or result.deployment_id != deployment.id
        ):
            raise ValueError("observation result belongs to a different deployment")
        gate_by_id = {gate.id: gate for gate in gates}
        if {item.gate_id for item in result.gate_results} != set(gate_by_id):
            raise ValueError("observation result does not cover the requested health gates")
        if any(item.project_id != deployment.project_id for item in result.gate_results):
            raise ValueError("health gate result belongs to a different project")
        failed = [item for item in result.gate_results if not item.passed]
        expected = HealthVerdict.PASS
        if failed:
            expected = (
                HealthVerdict.FAIL
                if any(
                    gate_by_id[item.gate_id].failure_verdict is HealthVerdict.FAIL
                    for item in failed
                )
                else HealthVerdict.HOLD
            )
        if result.verdict is not expected:
            raise ValueError("observation verdict conflicts with health gate results")


__all__ = [
    "BlastRadius",
    "DeliveryActionDeniedError",
    "DeliveryApprovalRequiredError",
    "DeliveryControlProbe",
    "DeliveryControlRequest",
    "DeliveryLifecycle",
    "Deployment",
    "DeploymentProvider",
    "HealthGate",
    "HealthGateResult",
    "HealthVerdict",
    "ObservationProvider",
    "ObservationResult",
    "ReleaseCandidate",
    "ReleaseProvider",
]
