# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Deterministic delivery-provider fakes used only by the test suite."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sagewai.work.control import ControlCheckResult
from sagewai.work.models import ControlPrecondition
from sagewai.work.profiles.software.delivery import (
    BlastRadius,
    DeliveryControlRequest,
    Deployment,
    HealthGate,
    HealthGateResult,
    HealthVerdict,
    ObservationResult,
    ReleaseCandidate,
)


class DeterministicFakeReleaseProvider:
    def __init__(self, candidate: ReleaseCandidate) -> None:
        self._candidate = candidate
        self.builds: list[str] = []

    async def build(self, commit_sha: str) -> ReleaseCandidate:
        self.builds.append(commit_sha)
        if commit_sha != self._candidate.commit_sha:
            raise ValueError("fake release candidate commit does not match")
        return self._candidate


class DeterministicFakeDeploymentProvider:
    def __init__(self) -> None:
        self.deployments: list[Deployment] = []
        self.promotions: list[Deployment] = []
        self.rollbacks: list[Deployment] = []

    async def deploy(
        self,
        candidate: ReleaseCandidate,
        environment: str,
        exposure: BlastRadius,
        known_good_candidate: ReleaseCandidate,
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

    async def rollback(
        self,
        deployment: Deployment,
        known_good_candidate: ReleaseCandidate,
    ) -> Deployment:
        self.rollbacks.append(deployment)
        return Deployment(
            id=f"rollback-{len(self.rollbacks)}",
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            release_candidate_id=known_good_candidate.id,
            environment=deployment.environment,
            exposure=deployment.exposure,
            provider_ref=f"fake://rollback/{len(self.rollbacks)}",
            status="rolled_back",
        )


class DeterministicFakeObservationProvider:
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
    def __init__(
        self,
        results: Mapping[str, tuple[ControlCheckResult, ...]],
    ) -> None:
        self._results = dict(results)
        self.requests: list[DeliveryControlRequest] = []

    async def evaluate(
        self,
        request: DeliveryControlRequest,
        preconditions: tuple[ControlPrecondition, ...],
    ) -> tuple[ControlCheckResult, ...]:
        self.requests.append(request)
        results = self._results[request.action]
        if {result.precondition_id for result in results} != {
            precondition.id for precondition in preconditions
        }:
            raise ValueError("fake results do not match configured preconditions")
        return results
