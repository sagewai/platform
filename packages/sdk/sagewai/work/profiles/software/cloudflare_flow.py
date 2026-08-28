# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Durable delivery-phase driver for Sagewai's Cloudflare docs path."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import Reversibility, WorkRecord
from sagewai.work.profiles.software.delivery import (
    BlastRadius,
    DeliveryActionDeniedError,
    DeliveryLifecycle,
    Deployment,
    HealthGate,
    HealthVerdict,
    ObservationResult,
    ReleaseCandidate,
)
from sagewai.work.store import WorkStore

_ACTIVE_DELIVERY_STATUSES = {
    "READY_TO_DELIVER",
    "RELEASING",
    "STAGING",
    "PRODUCTION_CANARY",
    "PRODUCTION_ROLLOUT",
    "SOAKING",
    "ROLLING_BACK",
}


class CloudflareRolloutStep(BaseModel):
    """One configured production exposure and observation duration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exposure: BlastRadius
    observation_window_seconds: int = Field(gt=0)


class CloudflareDocsDeliveryPolicy(BaseModel):
    """Explicit project policy for the exact docs production path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rollout: tuple[CloudflareRolloutStep, ...]
    rollback_observation_window_seconds: int = Field(gt=0)
    evidence_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rollout(self) -> CloudflareDocsDeliveryPolicy:
        if not self.rollout:
            raise ValueError("Cloudflare docs rollout policy is empty")
        percentages: list[Decimal] = []
        for step in self.rollout:
            if step.exposure.dimension != "traffic" or not step.exposure.value.endswith("%"):
                raise ValueError("Cloudflare docs rollout must use traffic percentages")
            try:
                percentage = Decimal(step.exposure.value.removesuffix("%"))
            except InvalidOperation as exc:
                raise ValueError("Cloudflare docs rollout percentage is invalid") from exc
            percentages.append(percentage)
        if any(current <= 0 or current > 100 for current in percentages):
            raise ValueError("Cloudflare docs rollout percentage is outside 0-100")
        if any(current >= following for current, following in zip(percentages, percentages[1:])):
            raise ValueError("Cloudflare docs rollout percentages must increase")
        if percentages[-1] != Decimal(100):
            raise ValueError("Cloudflare docs rollout must end at 100%")
        return self


class CloudflareDocsDeliveryFlow:
    """Advance one WorkItem through delivery using durable receipts."""

    def __init__(
        self,
        *,
        work_store: WorkStore,
        lifecycle: DeliveryLifecycle,
        policy: CloudflareDocsDeliveryPolicy,
        known_good_candidate: ReleaseCandidate,
        health_gates: tuple[HealthGate, ...],
        merged_sha: str,
        release_evidence_refs: tuple[str, ...],
    ) -> None:
        if not health_gates:
            raise ValueError("Cloudflare docs delivery requires a health gate")
        self._work_store = work_store
        self._lifecycle = lifecycle
        self._policy = policy
        self._known_good = known_good_candidate
        self._health_gates = health_gates
        self._merged_sha = merged_sha
        self._release_evidence_refs = release_evidence_refs

    async def resume(self, work_id: str, *, project_id: str) -> WorkRecord:
        """Advance until completion, triage, degradation, or an approval gate."""

        record = await self._load(work_id, project_id)
        if record.status == "COMPLETE":
            return record
        if record.status not in _ACTIVE_DELIVERY_STATUSES:
            raise DeliveryActionDeniedError(
                f"delivery cannot resume from Work status {record.status}"
            )
        if self._known_good.work_id != work_id or self._known_good.project_id != project_id:
            raise ValueError("known-good candidate belongs to different Work")

        await self._lifecycle.register_known_good(
            self._known_good,
            evidence_refs=(self._policy.evidence_ref,),
        )
        candidate = await self._lifecycle.build(
            work_id=work_id,
            project_id=project_id,
            commit_sha=self._merged_sha,
            evidence_refs=self._release_evidence_refs,
        )

        for _ in range(2 * len(self._policy.rollout) + 1):
            events = await self._work_store.read_events(work_id, project_id=project_id)
            deployment_event = _latest_candidate_deployment(events, candidate.id)
            if deployment_event is None:
                await self._lifecycle.deploy(
                    candidate,
                    environment="production",
                    risk="high",
                    reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
                    exposure=self._policy.rollout[0].exposure,
                    known_good_candidate=self._known_good,
                    evidence_refs=(self._policy.evidence_ref,),
                    expected_duration_seconds=self._policy.rollout[0].observation_window_seconds,
                )
                continue

            deployment = Deployment.model_validate(deployment_event.payload_json["deployment"])
            observation = _latest_observation(events, deployment_event, deployment.id)
            if observation is None:
                step = self._step_for(deployment.exposure)
                await self._lifecycle.observe(
                    deployment,
                    gates=self._health_gates,
                    window_seconds=step.observation_window_seconds,
                )
                continue
            if observation.verdict is HealthVerdict.HOLD:
                return await self._lifecycle.triage(
                    deployment,
                    observation=observation,
                    summary="Cloudflare docs rollout held by a configured health gate.",
                    evidence_refs=observation.evidence_refs,
                )
            if observation.verdict is HealthVerdict.FAIL:
                return await self._restore_and_triage(
                    deployment,
                    observation,
                )

            step_index = self._policy.rollout.index(self._step_for(deployment.exposure))
            if step_index == len(self._policy.rollout) - 1:
                return await self._lifecycle.complete(
                    deployment,
                    required_exposure=self._policy.rollout[-1].exposure,
                    observation=observation,
                    evidence_refs=observation.evidence_refs,
                )
            await self._lifecycle.promote(
                deployment,
                exposure=self._policy.rollout[step_index + 1].exposure,
                known_good_candidate=self._known_good,
                evidence_refs=(self._policy.evidence_ref,),
                expected_duration_seconds=self._policy.rollout[
                    step_index + 1
                ].observation_window_seconds,
            )

        raise RuntimeError("Cloudflare docs delivery made no terminal progress")

    async def approve(
        self,
        work_id: str,
        *,
        project_id: str,
        gate_id: str,
        actor_ref: str,
    ) -> WorkRecord:
        return await self._lifecycle.approve(
            work_id,
            project_id=project_id,
            gate_id=gate_id,
            actor_ref=actor_ref,
        )

    async def _restore_and_triage(
        self,
        deployment: Deployment,
        failed: ObservationResult,
    ) -> WorkRecord:
        events = await self._work_store.read_events(
            deployment.work_id,
            project_id=deployment.project_id,
        )
        rollback_event = _rollback_for(events, deployment.id)
        if rollback_event is None:
            await self._lifecycle.rollback(
                deployment,
                known_good_candidate=self._known_good,
                evidence_refs=failed.evidence_refs,
                expected_duration_seconds=(self._policy.rollback_observation_window_seconds),
            )
            events = await self._work_store.read_events(
                deployment.work_id,
                project_id=deployment.project_id,
            )
            rollback_event = _rollback_for(events, deployment.id)
            if rollback_event is None:
                raise RuntimeError("rollback receipt was not persisted")
        rollback = Deployment.model_validate(rollback_event.payload_json["deployment"])
        rollback_observation = _latest_observation(
            events,
            rollback_event,
            rollback.id,
        )
        if rollback_observation is None:
            rollback_observation = await self._lifecycle.observe(
                rollback,
                gates=self._health_gates,
                window_seconds=self._policy.rollback_observation_window_seconds,
            )
        if rollback_observation.verdict is not HealthVerdict.PASS:
            await self._lifecycle.record_rollback_failure(
                deployment,
                failure_id="rollback-verification",
                detail=(
                    "rollback verification verdict "
                    f"{rollback_observation.verdict.value}"
                ),
                evidence_refs=rollback_observation.evidence_refs,
            )
            raise DeliveryActionDeniedError("rollback verification did not pass")
        return await self._lifecycle.triage(
            deployment,
            observation=failed,
            summary="Cloudflare docs rollout failed; known-good state was restored.",
            evidence_refs=(*failed.evidence_refs, *rollback_observation.evidence_refs),
        )

    def _step_for(self, exposure: BlastRadius) -> CloudflareRolloutStep:
        return next(
            (step for step in self._policy.rollout if step.exposure == exposure),
            None,
        ) or _raise_unknown_exposure(exposure)

    async def _load(self, work_id: str, project_id: str) -> WorkRecord:
        record = await self._work_store.load_work(work_id, project_id=project_id)
        if record is None:
            raise KeyError(work_id)
        return record


def _latest_candidate_deployment(
    events: list[WorkEvent],
    candidate_id: str,
) -> WorkEvent | None:
    return next(
        (
            event
            for event in reversed(events)
            if event.event_type is WorkEventType.DEPLOYMENT_RECORDED
            and event.payload_json["deployment"]["release_candidate_id"] == candidate_id
        ),
        None,
    )


def _latest_observation(
    events: list[WorkEvent],
    deployment_event: WorkEvent,
    deployment_id: str,
) -> ObservationResult | None:
    for event in reversed(events):
        if event.sequence <= deployment_event.sequence:
            return None
        if event.event_type is not WorkEventType.OBSERVATION_RECORDED:
            continue
        observation = ObservationResult.model_validate(event.payload_json["observation"])
        if observation.deployment_id == deployment_id:
            return observation
    return None


def _rollback_for(events: list[WorkEvent], deployment_id: str) -> WorkEvent | None:
    return next(
        (
            event
            for event in reversed(events)
            if event.event_type is WorkEventType.ROLLBACK_RECORDED
            and event.payload_json.get("source_deployment_id") == deployment_id
        ),
        None,
    )


def _raise_unknown_exposure(exposure: BlastRadius):
    raise ValueError(f"deployment exposure is outside configured rollout: {exposure}")


__all__ = [
    "CloudflareDocsDeliveryFlow",
    "CloudflareDocsDeliveryPolicy",
    "CloudflareRolloutStep",
]
