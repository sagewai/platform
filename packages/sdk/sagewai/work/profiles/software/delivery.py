# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Release, delivery, observation, and rollback contracts for software Work."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import ActionRequest, GateDecision, Reversibility
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
    status: str


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


class DeliveryPreconditionResult(BaseModel):
    """Immutable result from one delivery control-precondition probe."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    precondition_id: str
    passed: bool
    evidence_refs: tuple[str, ...]
    detail: str | None
    checked_at: datetime


class DeliveryControlRequest(BaseModel):
    """Bounded inputs for deterministic delivery pre-flight checks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    work_id: str
    action: Literal["deploy", "promote", "observe", "rollback"]
    candidate: ReleaseCandidate
    deployment: Deployment | None
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

    async def rollback(self, deployment: Deployment) -> Deployment: ...


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
    ) -> tuple[DeliveryPreconditionResult, ...]: ...


class DeliveryControlDegradedError(RuntimeError):
    """Delivery is frozen because one or more control preconditions failed."""


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
        action_policy: Callable[[ActionRequest], GateDecision],
    ) -> None:
        self._work_store = work_store
        self._release_provider = release_provider
        self._deployment_provider = deployment_provider
        self._observation_provider = observation_provider
        self._control_probe = control_probe
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
        self._validate_known_good(candidate, known_good_candidate)
        await self._preflight(
            DeliveryControlRequest(
                project_id=candidate.project_id,
                work_id=candidate.work_id,
                action="deploy",
                candidate=candidate,
                deployment=None,
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
                scope=f"{environment}:{exposure.dimension}:{exposure.value}",
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
                deployment=deployment,
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
        self._validate_known_good(candidate, known_good_candidate)
        observation = await self._latest_observation(deployment)
        if observation is None or observation.verdict is not HealthVerdict.PASS:
            raise DeliveryActionDeniedError("promotion requires a passing observation")
        await self._preflight(
            DeliveryControlRequest(
                project_id=deployment.project_id,
                work_id=deployment.work_id,
                action="promote",
                candidate=candidate,
                deployment=deployment,
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
                scope=f"{deployment.environment}:{exposure.dimension}:{exposure.value}",
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
        self._validate_known_good(candidate, known_good_candidate)
        await self._preflight(
            DeliveryControlRequest(
                project_id=deployment.project_id,
                work_id=deployment.work_id,
                action="rollback",
                candidate=candidate,
                deployment=deployment,
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
                scope=deployment.provider_ref,
                evidence_refs=evidence_refs,
            )
        )
        rolled_back = await self._deployment_provider.rollback(deployment)
        if (
            rolled_back.project_id != deployment.project_id
            or rolled_back.work_id != deployment.work_id
            or rolled_back.release_candidate_id != deployment.release_candidate_id
            or rolled_back.environment != deployment.environment
        ):
            raise ValueError("rollback result belongs to a different deployment")
        await self._append(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            event_type=WorkEventType.ROLLBACK_RECORDED,
            payload={
                "deployment": rolled_back.model_dump(mode="json"),
                "known_good_release_candidate_id": known_good_candidate.id,
            },
            actor_ref="deployment_provider",
        )
        return rolled_back

    async def _preflight(self, request: DeliveryControlRequest) -> None:
        results = await self._control_probe.evaluate(request)
        if not results:
            raise ValueError("delivery control probe returned no precondition results")
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
            raise DeliveryControlDegradedError(failed_ids)

        passed_ids = {result.precondition_id for result in results}
        if request.action != "rollback" and active - passed_ids:
            raise DeliveryControlDegradedError(", ".join(sorted(active - passed_ids)))
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
        decision = GateDecision(self._action_policy(request))
        if decision is GateDecision.ALLOW:
            return
        if decision is GateDecision.REQUIRE_APPROVAL:
            gate_id = f"{request.action}:{request.work_id}:{request.scope}"
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
            record = await self._work_store.load_work(
                request.work_id,
                project_id=request.project_id,
            )
            if record is None:
                raise KeyError(request.work_id)
            await self._work_store.save_work(
                record.model_copy(
                    update={
                        "pending_gate": gate_id,
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
            )
            raise DeliveryApprovalRequiredError(request.action)
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
        return await self._candidate_by_id(
            deployment.work_id,
            deployment.project_id,
            deployment.release_candidate_id,
        )

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

    async def _latest_observation(
        self,
        deployment: Deployment,
    ) -> ObservationResult | None:
        events = await self._work_store.read_events(
            deployment.work_id,
            project_id=deployment.project_id,
        )
        for event in reversed(events):
            if event.event_type is not WorkEventType.OBSERVATION_RECORDED:
                continue
            observation = ObservationResult.model_validate(event.payload_json["observation"])
            if observation.deployment_id == deployment.id:
                return observation
        return None

    async def _active_degradations(self, work_id: str, project_id: str) -> set[str]:
        pending = await self._work_store.pending_attention(project_id=project_id)
        return {
            item.attention_id
            for item in pending
            if item.work_id == work_id and item.kind.value == "CONTROL_DEGRADED"
        }

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

    @staticmethod
    def _validate_known_good(
        candidate: ReleaseCandidate,
        known_good_candidate: ReleaseCandidate,
    ) -> None:
        if known_good_candidate.project_id != candidate.project_id:
            raise ValueError("known-good candidate belongs to a different project")

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


class DeterministicFakeReleaseProvider:
    """Return one configured candidate and record the exact build input."""

    def __init__(self, candidate: ReleaseCandidate) -> None:
        self._candidate = candidate
        self.builds: list[str] = []

    async def build(self, commit_sha: str) -> ReleaseCandidate:
        self.builds.append(commit_sha)
        if commit_sha != self._candidate.commit_sha:
            raise ValueError("fake release candidate commit does not match")
        return self._candidate


class DeterministicFakeDeploymentProvider:
    """Record deterministic deploy, promotion, and rollback receipts."""

    def __init__(self) -> None:
        self.deployments: list[Deployment] = []
        self.promotions: list[Deployment] = []
        self.rollbacks: list[Deployment] = []

    async def deploy(
        self,
        candidate: ReleaseCandidate,
        environment: str,
        exposure: BlastRadius,
    ) -> Deployment:
        deployment = Deployment(
            id=f"deployment-{len(self.deployments) + 1}",
            project_id=candidate.project_id,
            work_id=candidate.work_id,
            release_candidate_id=candidate.id,
            environment=environment,
            exposure=exposure,
            provider_ref=f"fake://deployment/{len(self.deployments) + 1}",
            status="active",
        )
        self.deployments.append(deployment)
        return deployment

    async def promote(
        self,
        deployment: Deployment,
        exposure: BlastRadius,
    ) -> Deployment:
        index = len(self.deployments) + len(self.promotions) + 1
        promoted = deployment.model_copy(
            update={
                "id": f"deployment-{index}",
                "exposure": exposure,
                "provider_ref": f"fake://deployment/{index}",
            }
        )
        self.promotions.append(promoted)
        return promoted

    async def rollback(self, deployment: Deployment) -> Deployment:
        self.rollbacks.append(deployment)
        return deployment.model_copy(update={"status": "rolled_back"})


class DeterministicFakeObservationProvider:
    """Evaluate configured boolean outcomes using each gate's failure verdict."""

    def __init__(self, outcomes: Sequence[Mapping[str, bool]]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, int]] = []

    async def observe(
        self,
        deployment: Deployment,
        gates: tuple[HealthGate, ...],
        window_seconds: int,
    ) -> ObservationResult:
        if not self._outcomes:
            raise ValueError("no fake observation outcome configured")
        outcomes = self._outcomes.pop(0)
        if set(outcomes) != {gate.id for gate in gates}:
            raise ValueError("fake outcome does not match requested health gates")
        self.calls.append((deployment.id, window_seconds))
        gate_results = tuple(
            HealthGateResult(
                project_id=deployment.project_id,
                gate_id=gate.id,
                passed=outcomes[gate.id],
                evidence_refs=(f"fake-observation://{deployment.id}/{gate.id}",),
            )
            for gate in gates
        )
        failed = [gate for gate in gates if not outcomes[gate.id]]
        verdict = HealthVerdict.PASS
        if failed:
            verdict = (
                HealthVerdict.FAIL
                if any(gate.failure_verdict is HealthVerdict.FAIL for gate in failed)
                else HealthVerdict.HOLD
            )
        return ObservationResult(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            deployment_id=deployment.id,
            verdict=verdict,
            gate_results=gate_results,
            evidence_refs=tuple(ref for result in gate_results for ref in result.evidence_refs),
        )


class DeterministicFakeControlProbe:
    """Return explicitly configured precondition results for each action."""

    def __init__(
        self,
        results: Mapping[str, tuple[DeliveryPreconditionResult, ...]],
    ) -> None:
        self._results = dict(results)
        self.requests: list[DeliveryControlRequest] = []

    async def evaluate(
        self,
        request: DeliveryControlRequest,
    ) -> tuple[DeliveryPreconditionResult, ...]:
        self.requests.append(request)
        return self._results[request.action]


__all__ = [
    "BlastRadius",
    "DeliveryActionDeniedError",
    "DeliveryApprovalRequiredError",
    "DeliveryControlDegradedError",
    "DeliveryControlProbe",
    "DeliveryControlRequest",
    "DeliveryLifecycle",
    "DeliveryPreconditionResult",
    "Deployment",
    "DeploymentProvider",
    "DeterministicFakeControlProbe",
    "DeterministicFakeDeploymentProvider",
    "DeterministicFakeObservationProvider",
    "DeterministicFakeReleaseProvider",
    "HealthGate",
    "HealthGateResult",
    "HealthVerdict",
    "ObservationProvider",
    "ObservationResult",
    "ReleaseCandidate",
    "ReleaseProvider",
]
