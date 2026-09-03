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
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from sagewai.core.durability import run_with_heartbeat
from sagewai.work.control import (
    ControlCheckResult,
    ControlDegradedError,
)
from sagewai.work.events import (
    WorkEvent,
    WorkEventType,
    active_control_degradations,
    active_control_precondition_ids,
)
from sagewai.work.models import (
    ActionRequest,
    ControlPrecondition,
    ExternalOutcomeIncident,
    GateDecision,
    Reversibility,
    WorkRecord,
)
from sagewai.work.store import WorkStore

T = TypeVar("T")


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
    """Immutable, verified artifact promoted unchanged between environments.

    ``artifact_digest`` is a content digest for artifacts Sagewai builds and an
    identity digest for an externally registered immutable provider reference.
    The corresponding ``artifact_ref`` selects those semantics.
    """

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
    deployment: Deployment | None = None
    target_exposure: BlastRadius | None = None
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
        known_good_candidate: ReleaseCandidate,
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


def default_delivery_action_policy(request: ActionRequest) -> GateDecision:
    """Deny unapproved irreversible/critical delivery actions by default."""

    if request.reversibility is Reversibility.IRREVERSIBLE or request.risk == "critical":
        return GateDecision.DENY
    if request.reversibility is Reversibility.PURE:
        return GateDecision.ALLOW
    return GateDecision.REQUIRE_APPROVAL


class DeliveryControlLostError(RuntimeError):
    """A provider lost deterministic control during an active delivery action."""

    def __init__(
        self,
        precondition_id: str,
        detail: str,
        *,
        evidence_refs: tuple[str, ...] = (),
    ) -> None:
        super().__init__(detail)
        self.precondition_id = precondition_id
        self.detail = detail
        self.evidence_refs = evidence_refs


class _RollbackProviderError(RuntimeError):
    """A failure raised specifically by the rollback provider operation."""


_DELIVERY_STATUS_EVENTS = {
    WorkEventType.RELEASE_CREATED,
    WorkEventType.DEPLOYMENT_RECORDED,
    WorkEventType.OBSERVATION_RECORDED,
    WorkEventType.ROLLBACK_RECORDED,
    WorkEventType.TRIAGE_CREATED,
    WorkEventType.WORK_COMPLETED,
}


def _production_traffic_percent(deployment: Deployment) -> Decimal | None:
    if (
        deployment.environment != "production"
        or deployment.exposure.dimension != "traffic"
        or not deployment.exposure.value.endswith("%")
    ):
        return None
    try:
        return Decimal(deployment.exposure.value[:-1])
    except InvalidOperation:
        return None


def _project_delivery_status(events: list[WorkEvent]) -> str | None:
    """Fold canonical delivery events into the current software Work phase."""

    status: str | None = None
    deployments: dict[str, Deployment] = {}
    rollback_deployment_ids: set[str] = set()

    for event in sorted(events, key=lambda item: item.sequence):
        if event.event_type is WorkEventType.RELEASE_CREATED:
            if event.payload_json.get("known_good_baseline") is not True:
                status = "RELEASING"
        elif event.event_type is WorkEventType.DEPLOYMENT_RECORDED:
            deployment = Deployment.model_validate(event.payload_json["deployment"])
            deployments[deployment.id] = deployment
            traffic = _production_traffic_percent(deployment)
            if deployment.environment == "staging":
                status = "STAGING"
            elif traffic is not None and Decimal(0) <= traffic < Decimal(100):
                status = "PRODUCTION_CANARY"
            elif traffic == Decimal(100):
                status = "PRODUCTION_ROLLOUT"
        elif event.event_type is WorkEventType.OBSERVATION_RECORDED:
            payload = event.payload_json.get("observation")
            if payload is None:
                continue
            observation = ObservationResult.model_validate(payload)
            observed_deployment = deployments.get(observation.deployment_id)
            if observation.deployment_id in rollback_deployment_ids:
                status = "ROLLING_BACK"
            elif (
                observation.verdict is HealthVerdict.PASS
                and observed_deployment is not None
                and _production_traffic_percent(observed_deployment) == Decimal(100)
            ):
                status = "SOAKING"
        elif event.event_type is WorkEventType.ROLLBACK_RECORDED:
            deployment = Deployment.model_validate(event.payload_json["deployment"])
            deployments[deployment.id] = deployment
            rollback_deployment_ids.add(deployment.id)
            status = "ROLLING_BACK"
        elif event.event_type is WorkEventType.TRIAGE_CREATED:
            status = "TRIAGING"
        elif event.event_type is WorkEventType.WORK_COMPLETED:
            status = "COMPLETE"

    return status


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
        heartbeat_interval: float = 30,
    ) -> None:
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._work_store = work_store
        self._release_provider = release_provider
        self._deployment_provider = deployment_provider
        self._observation_provider = observation_provider
        self._control_probe = control_probe
        self._control_preconditions = control_preconditions
        self._action_policy = action_policy
        self._heartbeat_interval = heartbeat_interval

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
        existing = await self._candidate_by_commit(work_id, project_id, commit_sha)
        if existing is not None:
            return existing
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

    async def register_known_good(
        self,
        candidate: ReleaseCandidate,
        *,
        evidence_refs: tuple[str, ...],
    ) -> ReleaseCandidate:
        """Persist an explicitly configured pre-existing rollback candidate."""

        await self._require_work(candidate.work_id, candidate.project_id)
        try:
            existing = await self._candidate_by_id(
                candidate.work_id,
                candidate.project_id,
                candidate.id,
            )
        except ValueError:
            existing = None
        if existing is not None:
            if existing != candidate:
                raise ValueError("known-good candidate id conflicts with canonical state")
            return existing
        await self._append(
            project_id=candidate.project_id,
            work_id=candidate.work_id,
            event_type=WorkEventType.RELEASE_CREATED,
            payload={
                "release_candidate": candidate.model_dump(mode="json"),
                "known_good_baseline": True,
                "evidence_refs": list(evidence_refs),
            },
            actor_ref="delivery_configuration",
        )
        return candidate

    async def approve(
        self,
        work_id: str,
        *,
        project_id: str,
        gate_id: str,
        actor_ref: str,
    ) -> WorkRecord:
        """Approve one canonical delivery action gate without executing it."""

        await self._require_work(work_id, project_id)
        record = await self._work_store.load_work(work_id, project_id=project_id)
        if record is None:
            raise KeyError(work_id)
        if record.status == "COMPLETE":
            return record
        if record.status not in {
            "READY_TO_DELIVER",
            "RELEASING",
            "STAGING",
            "PRODUCTION_CANARY",
            "PRODUCTION_ROLLOUT",
            "SOAKING",
            "ROLLING_BACK",
        }:
            raise DeliveryActionDeniedError(
                f"delivery approval cannot resume from Work status {record.status}"
            )
        events = await self._work_store.read_events(work_id, project_id=project_id)
        requested = self._gate_event(events, WorkEventType.GATE_REQUESTED, gate_id)
        if requested is None:
            raise ValueError("delivery gate was not requested for this WorkItem")
        decided = self._gate_event(events, WorkEventType.GATE_DECIDED, gate_id)
        if decided is not None and not self._same_authorized_action(
            decided.payload_json.get("action"),
            requested.payload_json.get("action"),
        ):
            decided = None
        if record.pending_gate != gate_id:
            if record.pending_gate is None and decided is not None:
                if decided.payload_json.get("decision") == GateDecision.ALLOW.value:
                    return record
            raise ValueError("delivery gate is not pending for this WorkItem")
        if decided is not None:
            if decided.payload_json.get("decision") != GateDecision.ALLOW.value:
                raise ValueError("delivery gate already has a non-allow decision")
        else:
            await self._append(
                project_id=project_id,
                work_id=work_id,
                event_type=WorkEventType.GATE_DECIDED,
                payload={
                    "gate_id": gate_id,
                    "decision": GateDecision.ALLOW.value,
                    "action": requested.payload_json["action"],
                    "approved_by": actor_ref,
                },
                actor_ref=actor_ref,
            )
        updated = record.model_copy(
            update={
                "pending_gate": None,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await self._work_store.save_work(updated)
        return updated

    async def deploy(
        self,
        candidate: ReleaseCandidate,
        *,
        environment: str,
        exposure: BlastRadius,
        known_good_candidate: ReleaseCandidate,
        evidence_refs: tuple[str, ...],
        expected_duration_seconds: int,
        risk: str,
        reversibility: Reversibility,
    ) -> Deployment:
        """Deploy a candidate only while authority, observation, and rollback pass."""

        await self._require_candidate(candidate)
        if await self._candidate_was_rolled_back(candidate):
            raise DeliveryActionDeniedError("rolled-back candidate cannot be deployed")
        existing = await self._deployment_for(candidate, environment, exposure)
        if existing is not None:
            return existing
        if await self._has_deployment(candidate, environment):
            raise DeliveryActionDeniedError(
                "candidate already deployed to environment; use promote"
            )
        if await self._candidate_has_nonpassing_delivery(candidate):
            raise DeliveryActionDeniedError(
                "candidate has a non-passing observation or unobserved deployment"
            )
        control_request = DeliveryControlRequest(
            project_id=candidate.project_id,
            work_id=candidate.work_id,
            action="deploy",
            candidate=candidate,
            known_good_candidate=known_good_candidate,
            target_exposure=exposure,
            expected_duration_seconds=expected_duration_seconds,
        )
        await self._preflight(control_request)
        action_name = "deploy_production" if environment == "production" else "deploy_staging"
        await self._authorize(
            ActionRequest(
                project_id=candidate.project_id,
                action=action_name,
                work_id=candidate.work_id,
                risk=risk,
                reversibility=reversibility,
                scope=(f"{candidate.id}:{environment}:{exposure.dimension}:{exposure.value}"),
                evidence_refs=evidence_refs,
            )
        )
        deployment = await self._run_controlled(
            self._deployment_provider.deploy(
                candidate,
                environment,
                exposure,
                known_good_candidate,
            ),
            control_request,
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
        control_request = DeliveryControlRequest(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            action="observe",
            candidate=candidate,
            known_good_candidate=None,
            deployment=deployment,
            target_exposure=deployment.exposure,
            expected_duration_seconds=window_seconds,
        )
        await self._preflight(control_request)
        result = await self._run_controlled(
            self._observation_provider.observe(
                deployment,
                gates,
                window_seconds,
            ),
            control_request,
        )
        self._validate_observation(result, deployment, gates)
        if deployment.environment == "production" and result.verdict is HealthVerdict.FAIL:
            incident_deployment = await self._external_incident_source(deployment)
            await self._append_with_external_outcome(
                project_id=deployment.project_id,
                work_id=deployment.work_id,
                source_event_type=WorkEventType.OBSERVATION_RECORDED,
                source_payload={"observation": result.model_dump(mode="json")},
                source_actor_ref="observation_provider",
                incident_deployment=incident_deployment,
                severity="high",
                evidence_refs=result.evidence_refs,
            )
        else:
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
        existing = await self._deployment_for(
            candidate,
            deployment.environment,
            exposure,
        )
        if existing is not None:
            return existing
        if await self._candidate_has_nonpassing_delivery(candidate):
            raise DeliveryActionDeniedError(
                "candidate has a non-passing observation or unobserved deployment"
            )
        observation = await self._latest_observation(deployment)
        if observation is None or observation.verdict is not HealthVerdict.PASS:
            raise DeliveryActionDeniedError("promotion requires a passing observation")
        control_request = DeliveryControlRequest(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            action="promote",
            candidate=candidate,
            known_good_candidate=known_good_candidate,
            deployment=deployment,
            target_exposure=exposure,
            expected_duration_seconds=expected_duration_seconds,
        )
        await self._preflight(control_request)
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
        promoted = await self._run_controlled(
            self._deployment_provider.promote(deployment, exposure),
            control_request,
        )
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
        events = await self._work_store.read_events(
            deployment.work_id,
            project_id=deployment.project_id,
        )
        provider_failure = active_control_degradations(events).get("rollback-provider")
        if (
            provider_failure is not None
            and provider_failure.payload_json.get("deployment_id") == deployment.id
        ):
            raise DeliveryActionDeniedError(
                "rollback provider failed; explicit recovery is required"
            )
        existing = await self._rollback_for(deployment.id, deployment)
        if existing is not None:
            return existing
        control_request = DeliveryControlRequest(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            action="rollback",
            candidate=candidate,
            known_good_candidate=known_good_candidate,
            deployment=deployment,
            expected_duration_seconds=expected_duration_seconds,
        )
        await self._preflight(control_request)
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
        try:
            rolled_back = await self._run_controlled(
                self._run_rollback_provider(
                    deployment,
                    known_good_candidate,
                ),
                control_request,
            )
        except _RollbackProviderError as exc:
            await self.record_rollback_failure(
                deployment,
                failure_id="rollback-provider",
                detail=str(exc),
                evidence_refs=evidence_refs,
            )
            raise DeliveryActionDeniedError(
                "rollback provider failed; explicit recovery is required"
            ) from exc
        if (
            rolled_back.project_id != deployment.project_id
            or rolled_back.work_id != deployment.work_id
            or rolled_back.release_candidate_id != known_good_candidate.id
            or rolled_back.environment != deployment.environment
            or rolled_back.status != "rolled_back"
        ):
            raise ValueError("rollback result belongs to a different deployment")
        rollback_payload = {
            "source_deployment_id": deployment.id,
            "source_release_candidate_id": candidate.id,
            "deployment": rolled_back.model_dump(mode="json"),
            "known_good_release_candidate": known_good_candidate.model_dump(mode="json"),
            "evidence_refs": list(evidence_refs),
        }
        if deployment.environment == "production":
            await self._append_with_external_outcome(
                project_id=deployment.project_id,
                work_id=deployment.work_id,
                source_event_type=WorkEventType.ROLLBACK_RECORDED,
                source_payload=rollback_payload,
                source_actor_ref="deployment_provider",
                incident_deployment=deployment,
                severity="high",
                evidence_refs=evidence_refs,
            )
        else:
            await self._append(
                project_id=deployment.project_id,
                work_id=deployment.work_id,
                event_type=WorkEventType.ROLLBACK_RECORDED,
                payload=rollback_payload,
                actor_ref="deployment_provider",
            )
        return rolled_back

    async def _run_rollback_provider(
        self,
        deployment: Deployment,
        known_good_candidate: ReleaseCandidate,
    ) -> Deployment:
        try:
            return await self._deployment_provider.rollback(
                deployment,
                known_good_candidate,
            )
        except (ControlDegradedError, DeliveryControlLostError):
            raise
        except Exception as exc:
            raise _RollbackProviderError(
                f"{type(exc).__name__}: {exc}"
            ) from exc

    async def record_rollback_failure(
        self,
        deployment: Deployment,
        *,
        failure_id: Literal["rollback-provider", "rollback-verification"],
        detail: str,
        evidence_refs: tuple[str, ...],
    ) -> None:
        """Freeze a failed production rollback as one critical incident."""

        candidate = await self._candidate_for(deployment)
        request = DeliveryControlRequest(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            action="rollback",
            candidate=candidate,
            known_good_candidate=None,
            deployment=deployment,
            expected_duration_seconds=1,
        )
        await self._record_control_degradation(
            request,
            (
                ControlCheckResult(
                    project_id=deployment.project_id,
                    precondition_id=failure_id,
                    passed=False,
                    evidence_refs=evidence_refs,
                    detail=detail,
                    checked_at=datetime.now(timezone.utc),
                ),
            ),
        )

    async def triage(
        self,
        deployment: Deployment,
        *,
        observation: ObservationResult,
        summary: str,
        evidence_refs: tuple[str, ...],
    ) -> WorkRecord:
        """Persist triage only for the canonical failed deployment observation."""

        await self._candidate_for(deployment)
        latest = await self._latest_observation(deployment)
        if latest != observation or observation.verdict not in {
            HealthVerdict.HOLD,
            HealthVerdict.FAIL,
        }:
            raise DeliveryActionDeniedError("triage requires the latest HOLD or FAIL observation")
        if (
            observation.verdict is HealthVerdict.FAIL
            and await self._rollback_for(deployment.id, deployment) is None
        ):
            raise DeliveryActionDeniedError("triage requires a recorded rollback")
        if await self._has_event_for_deployment(
            deployment,
            WorkEventType.TRIAGE_CREATED,
        ):
            return await self._project_work_status(deployment.work_id, deployment.project_id)
        await self._append(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            event_type=WorkEventType.TRIAGE_CREATED,
            payload={
                "deployment_id": deployment.id,
                "observation": observation.model_dump(mode="json"),
                "summary": summary,
                "evidence_refs": list(evidence_refs),
            },
            actor_ref="delivery_lifecycle",
        )
        return await self._project_work_status(deployment.work_id, deployment.project_id)

    async def complete(
        self,
        deployment: Deployment,
        *,
        required_exposure: BlastRadius,
        observation: ObservationResult,
        evidence_refs: tuple[str, ...],
    ) -> WorkRecord:
        """Complete Work only after the configured exposure has a PASS receipt."""

        candidate = await self._candidate_for(deployment)
        latest = await self._latest_observation(deployment)
        if deployment.status != "active" or deployment.exposure != required_exposure:
            raise DeliveryActionDeniedError("configured delivery exposure is not reached")
        if await self._candidate_was_rolled_back(candidate):
            raise DeliveryActionDeniedError("rolled-back candidate cannot complete Work")
        if latest != observation or observation.verdict is not HealthVerdict.PASS:
            raise DeliveryActionDeniedError("completion requires the latest PASS observation")
        if await self._has_event_for_deployment(
            deployment,
            WorkEventType.WORK_COMPLETED,
        ):
            return await self._project_work_status(deployment.work_id, deployment.project_id)
        await self._append(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            event_type=WorkEventType.WORK_COMPLETED,
            payload={
                "deployment_id": deployment.id,
                "release_candidate_id": candidate.id,
                "deployment": deployment.model_dump(mode="json"),
                "observation": observation.model_dump(mode="json"),
                "evidence_refs": list(evidence_refs),
            },
            actor_ref="delivery_lifecycle",
        )
        return await self._project_work_status(deployment.work_id, deployment.project_id)

    async def _run_controlled(
        self,
        operation: Awaitable[T],
        request: DeliveryControlRequest,
    ) -> T:
        async def _heartbeat() -> None:
            await self._preflight(request)

        try:
            return await run_with_heartbeat(
                operation,
                heartbeat=_heartbeat,
                interval=self._heartbeat_interval,
            )
        except DeliveryControlLostError as exc:
            await self._record_control_degradation(
                request,
                (
                    ControlCheckResult(
                        project_id=request.project_id,
                        precondition_id=exc.precondition_id,
                        passed=False,
                        evidence_refs=exc.evidence_refs,
                        detail=exc.detail,
                        checked_at=datetime.now(timezone.utc),
                    ),
                ),
            )
            raise ControlDegradedError(exc.precondition_id) from exc

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
            await self._record_control_degradation(request, failed)
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

    async def _record_control_degradation(
        self,
        request: DeliveryControlRequest,
        failed: tuple[ControlCheckResult, ...],
    ) -> None:
        active = await self._active_degradations(request.work_id, request.project_id)
        newly_failed = tuple(result for result in failed if result.precondition_id not in active)
        failures_to_record = newly_failed
        production_rollback = (
            request.action == "rollback"
            and request.deployment is not None
            and request.deployment.environment == "production"
        )
        if production_rollback:
            events = await self._work_store.read_events(
                request.work_id,
                project_id=request.project_id,
            )
            covered = {
                precondition_id
                for precondition_id, event in active_control_degradations(events).items()
                if event.payload_json.get("severity") == "critical"
                and event.payload_json.get("action") == "rollback"
                and event.payload_json.get("deployment_id") == request.deployment.id
            }
            failures_to_record = tuple(
                result for result in failed if result.precondition_id not in covered
            )
        if not failures_to_record:
            return
        payload: dict[str, object] = {
            "failed_preconditions": [
                result.precondition_id for result in failures_to_record
            ],
            "evidence_refs": [
                ref for result in failures_to_record for ref in result.evidence_refs
            ],
            "details": "; ".join(
                f"{result.precondition_id}: {result.detail or 'failed'}"
                for result in failures_to_record
            ),
            "frozen_action_ids": [request.action],
            "severity": "critical" if production_rollback else "high",
            "action": request.action,
        }
        if request.deployment is not None:
            payload["deployment_id"] = request.deployment.id
        if production_rollback and request.deployment is not None:
            failed_ids = tuple(
                result.precondition_id for result in failures_to_record
            )
            details = str(payload["details"])
            failed_summary = ", ".join(failed_ids)
            await self._append_with_external_outcome(
                project_id=request.project_id,
                work_id=request.work_id,
                source_event_type=WorkEventType.CONTROL_DEGRADED,
                source_payload=payload,
                source_actor_ref="delivery_control",
                incident_deployment=request.deployment,
                severity="critical",
                evidence_refs=tuple(
                    ref for result in failures_to_record for ref in result.evidence_refs
                ),
                link_source_control_event=True,
                cause=(
                    f"failed preconditions: {failed_summary}; "
                    f"details: {details}"
                ),
            )
        else:
            await self._append(
                project_id=request.project_id,
                work_id=request.work_id,
                event_type=WorkEventType.CONTROL_DEGRADED,
                payload=payload,
                actor_ref="delivery_control",
            )

    async def _external_incident_source(
        self,
        deployment: Deployment,
    ) -> Deployment:
        """Keep a rollback observation on its source deployment incident."""

        events = await self._work_store.read_events(
            deployment.work_id, project_id=deployment.project_id
        )
        for event in reversed(events):
            if event.event_type is not WorkEventType.ROLLBACK_RECORDED:
                continue
            recorded = Deployment.model_validate(event.payload_json["deployment"])
            if recorded.id == deployment.id:
                return await self._deployment_by_id(
                    deployment.work_id,
                    deployment.project_id,
                    str(event.payload_json["source_deployment_id"]),
                )
        return deployment

    async def _append_with_external_outcome(
        self,
        *,
        project_id: str,
        work_id: str,
        source_event_type: WorkEventType,
        source_payload: dict,
        source_actor_ref: str,
        incident_deployment: Deployment,
        severity: Literal["high", "critical"],
        evidence_refs: tuple[str, ...],
        link_source_control_event: bool = False,
        cause: str | None = None,
    ) -> WorkEvent:
        """Atomically persist a delivery outcome and its generic interrupt receipt."""

        events = await self._work_store.read_events(work_id, project_id=project_id)
        sequence = events[-1].sequence + 1 if events else 1
        created_at = datetime.now(timezone.utc)
        source_event = WorkEvent(
            id=str(uuid.uuid4()),
            project_id=project_id,
            work_id=work_id,
            sequence=sequence,
            event_type=source_event_type,
            actor_type="delivery",
            actor_ref=source_actor_ref,
            payload_json=source_payload,
            created_at=created_at,
        )
        summary = f"production incident for deployment {incident_deployment.id}"
        if cause is not None:
            summary = f"{summary}; {cause}"
        incident = ExternalOutcomeIncident(
            incident_id=f"software-delivery:{incident_deployment.id}",
            summary=summary[:500],
            severity=severity,
            evidence_refs=tuple(dict.fromkeys(evidence_refs))[:32],
            active_control_event_ids=((source_event.id,) if link_source_control_event else ()),
        )
        receipt_event = WorkEvent(
            id=str(uuid.uuid4()),
            project_id=project_id,
            work_id=work_id,
            sequence=sequence + 1,
            event_type=WorkEventType.EXTERNAL_OUTCOME_RECORDED,
            actor_type="delivery",
            actor_ref="software_delivery",
            payload_json={"incident": incident.model_dump(mode="json")},
            created_at=created_at,
        )
        await self._work_store.append_events((source_event, receipt_event))
        if source_event_type in _DELIVERY_STATUS_EVENTS:
            await self._project_work_status(work_id, project_id)
        return source_event

    async def _authorize(self, request: ActionRequest) -> None:
        gate_id = f"{request.action}:{request.work_id}:{request.scope}"
        action = request.model_dump(mode="json")
        events = await self._work_store.read_events(
            request.work_id,
            project_id=request.project_id,
        )
        decided = self._gate_event(events, WorkEventType.GATE_DECIDED, gate_id)
        requested = self._gate_event(events, WorkEventType.GATE_REQUESTED, gate_id)
        if decided is not None and not self._same_authorized_action(
            decided.payload_json.get("action"), action
        ):
            decided = None
        if requested is not None and not self._same_authorized_action(
            requested.payload_json.get("action"), action
        ):
            requested = None
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
                        "action": action,
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
                    "action": action,
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

    async def _candidate_by_commit(
        self,
        work_id: str,
        project_id: str,
        commit_sha: str,
    ) -> ReleaseCandidate | None:
        events = await self._work_store.read_events(work_id, project_id=project_id)
        for event in reversed(events):
            if event.event_type is not WorkEventType.RELEASE_CREATED:
                continue
            candidate = ReleaseCandidate.model_validate(event.payload_json["release_candidate"])
            if candidate.commit_sha == commit_sha:
                return candidate
        return None

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

    async def _deployment_for(
        self,
        candidate: ReleaseCandidate,
        environment: str,
        exposure: BlastRadius,
    ) -> Deployment | None:
        events = await self._work_store.read_events(
            candidate.work_id,
            project_id=candidate.project_id,
        )
        for event in reversed(events):
            if event.event_type is not WorkEventType.DEPLOYMENT_RECORDED:
                continue
            deployment = Deployment.model_validate(event.payload_json["deployment"])
            if (
                deployment.release_candidate_id == candidate.id
                and deployment.environment == environment
                and deployment.exposure == exposure
            ):
                return deployment
        return None

    async def _rollback_for(
        self,
        source_deployment_id: str,
        source: Deployment,
    ) -> Deployment | None:
        events = await self._work_store.read_events(
            source.work_id,
            project_id=source.project_id,
        )
        for event in reversed(events):
            if event.event_type is not WorkEventType.ROLLBACK_RECORDED:
                continue
            if event.payload_json.get("source_deployment_id") == source_deployment_id:
                return Deployment.model_validate(event.payload_json["deployment"])
        return None

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
                payload = event.payload_json.get("observation")
                if payload is None:
                    continue
                observation = ObservationResult.model_validate(payload)
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
    def _same_authorized_action(left: object, right: object) -> bool:
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        return {
            key: value for key, value in left.items() if key != "evidence_refs"
        } == {key: value for key, value in right.items() if key != "evidence_refs"}

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

    async def _has_event_for_deployment(
        self,
        deployment: Deployment,
        event_type: WorkEventType,
    ) -> bool:
        events = await self._work_store.read_events(
            deployment.work_id,
            project_id=deployment.project_id,
        )
        return any(
            event.event_type is event_type
            and event.payload_json.get("deployment_id") == deployment.id
            for event in events
        )

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

    async def _project_work_status(
        self,
        work_id: str,
        project_id: str,
    ) -> WorkRecord:
        record = await self._work_store.load_work(work_id, project_id=project_id)
        if record is None:
            raise KeyError(work_id)
        if record.status == "COMPLETE":
            return record
        events = await self._work_store.read_events(work_id, project_id=project_id)
        status = _project_delivery_status(events)
        if status is None or status == record.status:
            return record
        updated = record.model_copy(
            update={
                "status": status,
                "pending_gate": None,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await self._work_store.save_work(updated)
        return updated

    async def _append(
        self,
        *,
        project_id: str,
        work_id: str,
        event_type: WorkEventType,
        payload: dict,
        actor_ref: str,
    ) -> WorkEvent:
        events = await self._work_store.read_events(work_id, project_id=project_id)
        event = WorkEvent(
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
        await self._work_store.append_event(event)
        if event_type in _DELIVERY_STATUS_EVENTS:
            await self._project_work_status(work_id, project_id)
        return event

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
    "DeliveryControlLostError",
    "DeliveryControlProbe",
    "DeliveryControlRequest",
    "DeliveryLifecycle",
    "default_delivery_action_policy",
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
