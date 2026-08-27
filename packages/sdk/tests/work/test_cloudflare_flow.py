# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Durable Cloudflare docs delivery-flow tests driven by provider fakes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from sagewai.fleet.execution import WorkerProcessResult
from sagewai.work import (
    ControlCheckResult,
    ControlPrecondition,
    ControlPreconditionKind,
    GateDecision,
    WorkEventType,
    WorkRecord,
    WorkStore,
)
from sagewai.work.profiles.software.cloudflare_adapter import (
    CloudflareDocsDeploymentProvider,
    CloudflareDocsObservationProvider,
)
from sagewai.work.profiles.software.cloudflare_flow import (
    CloudflareDocsDeliveryFlow,
    CloudflareDocsDeliveryPolicy,
    CloudflareRolloutStep,
)
from sagewai.work.profiles.software.delivery import (
    BlastRadius,
    DeliveryApprovalRequiredError,
    DeliveryLifecycle,
    HealthGate,
    HealthVerdict,
    ReleaseCandidate,
)
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.fakes_delivery import (
    DeterministicFakeDeploymentProvider,
    DeterministicFakeObservationProvider,
    DeterministicFakeReleaseProvider,
)
from tests.work.test_cloudflare_adapter import (
    NEW_VERSION_ID,
    OLD_VERSION_ID,
    CloudflareState,
    FakeCommandRunner,
    _local_candidate,
)
from tests.work.test_cloudflare_adapter import (
    _config as _adapter_config,
)
from tests.work.test_cloudflare_adapter import (
    _known_good as _adapter_known_good,
)

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
PROJECT_ID = "project-a"
WORK_ID = "work-1"


def _candidate(candidate_id: str, commit_sha: str) -> ReleaseCandidate:
    return ReleaseCandidate(
        id=candidate_id,
        project_id=PROJECT_ID,
        work_id=WORK_ID,
        commit_sha=commit_sha,
        artifact_ref=f"artifact://{candidate_id}",
        artifact_digest=candidate_id[-1] * 64,
        config_revision="config-1",
        verification_ref=f"verification://{candidate_id}",
        review_ref=f"review://{candidate_id}",
    )


def _known_good() -> ReleaseCandidate:
    return _candidate("known-good-2", "b" * 40)


def _policy() -> CloudflareDocsDeliveryPolicy:
    return CloudflareDocsDeliveryPolicy(
        rollout=(
            CloudflareRolloutStep(
                exposure=BlastRadius(dimension="traffic", value="5%"),
                observation_window_seconds=30,
            ),
            CloudflareRolloutStep(
                exposure=BlastRadius(dimension="traffic", value="100%"),
                observation_window_seconds=60,
            ),
        ),
        rollback_observation_window_seconds=30,
        evidence_ref="policy://docs-production",
    )


def _gate() -> HealthGate:
    return HealthGate(
        id="availability",
        project_id=PROJECT_ID,
        description="docs availability",
        check_ref="https://docs.sagewai.ai",
        failure_verdict=HealthVerdict.FAIL,
    )


class PassingProbe:
    async def evaluate(self, request, preconditions):
        return tuple(
            ControlCheckResult(
                project_id=request.project_id,
                precondition_id=precondition.id,
                passed=True,
                evidence_refs=(f"check://{precondition.id}",),
                checked_at=NOW,
            )
            for precondition in preconditions
        )


@pytest.fixture
async def store(dialect_engine) -> WorkStore:  # noqa: F811
    result = WorkStore(engine=dialect_engine)
    await result.init()
    await result.save_work(
        WorkRecord(
            work_id=WORK_ID,
            project_id=PROJECT_ID,
            source_ref="https://github.com/sagewai/platform/issues/1",
            profile="software",
            status="READY_TO_DELIVER",
            contract_version=1,
            active_run_id="review-1",
            pending_gate=None,
            profile_context={},
            created_at=NOW,
            updated_at=NOW,
        )
    )
    return result


def _flow(
    store: WorkStore,
    candidate: ReleaseCandidate,
    *,
    observations,
):
    deployment = DeterministicFakeDeploymentProvider()
    lifecycle = DeliveryLifecycle(
        work_store=store,
        release_provider=DeterministicFakeReleaseProvider(candidate),
        deployment_provider=deployment,
        observation_provider=DeterministicFakeObservationProvider(observations),
        control_probe=PassingProbe(),
        control_preconditions=(
            ControlPrecondition(
                id="control",
                project_id=PROJECT_ID,
                kind=ControlPreconditionKind.OBSERVABILITY,
                description="delivery control",
                check_ref="fake.control",
                required_for=("deploy", "promote", "observe", "rollback"),
            ),
            ControlPrecondition(
                id="rollback",
                project_id=PROJECT_ID,
                kind=ControlPreconditionKind.REVERSIBILITY,
                description="rollback control",
                check_ref="fake.rollback",
                required_for=("deploy", "promote", "rollback"),
            ),
        ),
        action_policy=lambda request: GateDecision.ALLOW,
    )
    return (
        CloudflareDocsDeliveryFlow(
            work_store=store,
            lifecycle=lifecycle,
            policy=_policy(),
            known_good_candidate=_known_good(),
            health_gates=(_gate(),),
            merged_sha=candidate.commit_sha,
            release_evidence_refs=("merge://sha", "review://accepted"),
        ),
        deployment,
    )


@pytest.mark.asyncio
async def test_flow_reaches_complete_with_same_candidate_promoted(store: WorkStore) -> None:
    candidate = _candidate("candidate-1", "a" * 40)
    flow, deployment = _flow(
        store,
        candidate,
        observations=({"availability": True}, {"availability": True}),
    )

    completed = await flow.resume(WORK_ID, project_id=PROJECT_ID)

    assert completed.status == "COMPLETE"
    assert [item.release_candidate_id for item in deployment.deployments] == [candidate.id]
    assert [item.release_candidate_id for item in deployment.promotions] == [candidate.id]
    assert [item.exposure.value for item in deployment.deployments] == ["5%"]
    assert [item.exposure.value for item in deployment.promotions] == ["100%"]


@pytest.mark.asyncio
async def test_flow_persists_and_resumes_an_explicit_delivery_approval(
    store: WorkStore,
) -> None:
    candidate = _candidate("candidate-1", "a" * 40)
    deployment = DeterministicFakeDeploymentProvider()
    lifecycle = DeliveryLifecycle(
        work_store=store,
        release_provider=DeterministicFakeReleaseProvider(candidate),
        deployment_provider=deployment,
        observation_provider=DeterministicFakeObservationProvider(
            ({"availability": True}, {"availability": True})
        ),
        control_probe=PassingProbe(),
        control_preconditions=(
            ControlPrecondition(
                id="control",
                project_id=PROJECT_ID,
                kind=ControlPreconditionKind.OBSERVABILITY,
                description="delivery control",
                check_ref="fake.control",
                required_for=("deploy", "promote", "observe", "rollback"),
            ),
            ControlPrecondition(
                id="rollback",
                project_id=PROJECT_ID,
                kind=ControlPreconditionKind.REVERSIBILITY,
                description="rollback control",
                check_ref="fake.rollback",
                required_for=("deploy", "promote", "rollback"),
            ),
        ),
        action_policy=lambda request: (
            GateDecision.ALLOW
            if request.action == "build_release"
            else GateDecision.REQUIRE_APPROVAL
        ),
    )
    flow = CloudflareDocsDeliveryFlow(
        work_store=store,
        lifecycle=lifecycle,
        policy=_policy(),
        known_good_candidate=_known_good(),
        health_gates=(_gate(),),
        merged_sha=candidate.commit_sha,
        release_evidence_refs=("merge://sha", "review://accepted"),
    )

    with pytest.raises(DeliveryApprovalRequiredError):
        await flow.resume(WORK_ID, project_id=PROJECT_ID)
    gated = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert gated is not None and gated.pending_gate is not None

    approved = await flow.approve(
        WORK_ID,
        project_id=PROJECT_ID,
        gate_id=gated.pending_gate,
        actor_ref="operator:arda",
    )
    assert approved.pending_gate is None

    with pytest.raises(DeliveryApprovalRequiredError):
        await flow.resume(WORK_ID, project_id=PROJECT_ID)
    assert len(deployment.deployments) == 1


@pytest.mark.asyncio
async def test_failure_restores_triages_and_new_candidate_redeploys(
    store: WorkStore,
) -> None:
    failed_candidate = _candidate("candidate-1", "a" * 40)
    failed_flow, first_provider = _flow(
        store,
        failed_candidate,
        observations=({"availability": False}, {"availability": True}),
    )

    triaged = await failed_flow.resume(WORK_ID, project_id=PROJECT_ID)

    assert triaged.status == "TRIAGE"
    assert first_provider.rollbacks

    repaired_candidate = _candidate("candidate-3", "c" * 40)
    repaired_flow, second_provider = _flow(
        store,
        repaired_candidate,
        observations=({"availability": True}, {"availability": True}),
    )
    completed = await repaired_flow.resume(WORK_ID, project_id=PROJECT_ID)

    assert completed.status == "COMPLETE"
    assert [item.release_candidate_id for item in second_provider.deployments] == [
        repaired_candidate.id
    ]
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    assert WorkEventType.TRIAGE_CREATED in [event.event_type for event in events]
    assert events[-1].event_type is WorkEventType.WORK_COMPLETED


@pytest.mark.parametrize(
    ("first_status", "expected_status"),
    ((200, "COMPLETE"), (503, "TRIAGE")),
)
@pytest.mark.asyncio
async def test_real_adapter_runs_through_lifecycle_and_flow(
    store: WorkStore,
    tmp_path: Path,
    first_status: int,
    expected_status: str,
) -> None:
    config = _adapter_config(tmp_path)
    candidate = _local_candidate(config)
    known_good = _adapter_known_good().model_copy(update={"work_id": WORK_ID})
    state = CloudflareState()
    state.deployments.append(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "created_on": "2026-08-27T09:59:00Z",
            "strategy": "percentage",
            "versions": [{"version_id": OLD_VERSION_ID, "percentage": 100}],
            "annotations": {},
        }
    )
    state.target_statuses = [first_status]
    tag = candidate.artifact_ref.removeprefix("cloudflare-version-tag://")
    runner = FakeCommandRunner(
        (
            WorkerProcessResult(
                returncode=0,
                stdout=f"Worker Version ID: {NEW_VERSION_ID}\n",
                stderr="",
            ),
        ),
        on_run=lambda args: state.add_candidate_version(tag),
    )
    current = 0.0

    async def advance(seconds: float) -> None:
        nonlocal current
        current += seconds

    async with httpx.AsyncClient(transport=httpx.MockTransport(state)) as client:
        lifecycle = DeliveryLifecycle(
            work_store=store,
            release_provider=DeterministicFakeReleaseProvider(candidate),
            deployment_provider=CloudflareDocsDeploymentProvider(
                config=config,
                api_token="token",
                client=client,
                process_runner=runner,
            ),
            observation_provider=CloudflareDocsObservationProvider(
                config=config,
                api_token="token",
                client=client,
                monotonic=lambda: current,
                sleep=advance,
            ),
            control_probe=PassingProbe(),
            control_preconditions=(
                ControlPrecondition(
                    id="control",
                    project_id=PROJECT_ID,
                    kind=ControlPreconditionKind.OBSERVABILITY,
                    description="delivery control",
                    check_ref="fake.control",
                    required_for=("deploy", "promote", "observe", "rollback"),
                ),
                ControlPrecondition(
                    id="rollback",
                    project_id=PROJECT_ID,
                    kind=ControlPreconditionKind.REVERSIBILITY,
                    description="rollback control",
                    check_ref="fake.rollback",
                    required_for=("deploy", "promote", "rollback"),
                ),
            ),
            action_policy=lambda request: GateDecision.ALLOW,
        )
        flow = CloudflareDocsDeliveryFlow(
            work_store=store,
            lifecycle=lifecycle,
            policy=_policy(),
            known_good_candidate=known_good,
            health_gates=(_gate(),),
            merged_sha=candidate.commit_sha,
            release_evidence_refs=("merge://sha", "review://accepted"),
        )

        completed = await flow.resume(WORK_ID, project_id=PROJECT_ID)

    assert completed.status == expected_status
    assert len(runner.calls) == 1
    expected_posts = [
        [
            {"version_id": OLD_VERSION_ID, "percentage": 95},
            {"version_id": NEW_VERSION_ID, "percentage": 5},
        ],
    ]
    expected_posts.append(
        [{"version_id": NEW_VERSION_ID, "percentage": 100}]
        if expected_status == "COMPLETE"
        else [{"version_id": OLD_VERSION_ID, "percentage": 100}]
    )
    assert [post["versions"] for post in state.posts] == expected_posts
    if expected_status == "TRIAGE":
        events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
        event_types = [event.event_type for event in events]
        assert WorkEventType.ROLLBACK_RECORDED in event_types
        assert event_types[-1] is WorkEventType.TRIAGE_CREATED
