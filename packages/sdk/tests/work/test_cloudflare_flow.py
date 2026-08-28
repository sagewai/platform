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
    ControlDegradedError,
    ControlPrecondition,
    ControlPreconditionKind,
    GateDecision,
    Reversibility,
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
    DeliveryActionDeniedError,
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


def _policy(
    exposures: tuple[str, ...] = ("5%", "100%"),
) -> CloudflareDocsDeliveryPolicy:
    return CloudflareDocsDeliveryPolicy(
        rollout=tuple(
            CloudflareRolloutStep(
                exposure=BlastRadius(dimension="traffic", value=exposure),
                observation_window_seconds=30,
            )
            for exposure in exposures
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
    policy: CloudflareDocsDeliveryPolicy | None = None,
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
            policy=policy or _policy(),
            known_good_candidate=_known_good(),
            health_gates=(_gate(),),
            merged_sha=candidate.commit_sha,
            release_evidence_refs=("merge://sha", "review://accepted"),
        ),
        deployment,
    )


def _gated_flow(
    store: WorkStore,
    candidate: ReleaseCandidate,
    *,
    observations,
    policy: CloudflareDocsDeliveryPolicy | None = None,
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
        action_policy=lambda request: (
            GateDecision.ALLOW
            if request.reversibility is Reversibility.PURE
            else GateDecision.REQUIRE_APPROVAL
        ),
    )
    flow = CloudflareDocsDeliveryFlow(
        work_store=store,
        lifecycle=lifecycle,
        policy=policy or _policy(),
        known_good_candidate=_known_good(),
        health_gates=(_gate(),),
        merged_sha=candidate.commit_sha,
        release_evidence_refs=("merge://sha", "review://accepted"),
    )
    return flow, deployment, lifecycle


@pytest.mark.asyncio
async def test_flow_reaches_complete_with_same_candidate_promoted(store: WorkStore) -> None:
    candidate = _candidate("candidate-1", "a" * 40)
    flow, deployment = _flow(
        store,
        candidate,
        observations=({"availability": True}, {"availability": True}),
    )

    completed = await flow.resume(WORK_ID, project_id=PROJECT_ID)
    replayed_approval = await flow.approve(
        WORK_ID,
        project_id=PROJECT_ID,
        gate_id="promote_rollout:stale",
        actor_ref="operator:arda",
    )

    assert completed.status == "COMPLETE"
    assert replayed_approval.status == "COMPLETE"
    assert [item.release_candidate_id for item in deployment.deployments] == [candidate.id]
    assert [item.release_candidate_id for item in deployment.promotions] == [candidate.id]
    assert [item.exposure.value for item in deployment.deployments] == ["5%"]
    assert [item.exposure.value for item in deployment.promotions] == ["100%"]


@pytest.mark.parametrize(
    "delivery_status",
    (
        "RELEASING",
        "STAGING",
        "PRODUCTION_CANARY",
        "PRODUCTION_ROLLOUT",
        "SOAKING",
        "ROLLING_BACK",
    ),
)
@pytest.mark.asyncio
async def test_flow_resumes_each_active_delivery_phase(
    store: WorkStore,
    delivery_status: str,
) -> None:
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None
    await store.save_work(record.model_copy(update={"status": delivery_status}))
    candidate = _candidate("candidate-1", "a" * 40)
    flow, _deployment = _flow(
        store,
        candidate,
        observations=({"availability": True}, {"availability": True}),
    )

    completed = await flow.resume(WORK_ID, project_id=PROJECT_ID)

    assert completed.status == "COMPLETE"


@pytest.mark.asyncio
async def test_flow_completes_a_four_step_project_rollout(store: WorkStore) -> None:
    candidate = _candidate("candidate-1", "a" * 40)
    flow, deployment = _flow(
        store,
        candidate,
        observations=tuple({"availability": True} for _ in range(4)),
        policy=_policy(("5%", "20%", "50%", "100%")),
    )

    completed = await flow.resume(WORK_ID, project_id=PROJECT_ID)

    assert completed.status == "COMPLETE"
    assert [item.exposure.value for item in deployment.deployments] == ["5%"]
    assert [item.exposure.value for item in deployment.promotions] == [
        "20%",
        "50%",
        "100%",
    ]


@pytest.mark.asyncio
async def test_production_soak_regression_rolls_back_and_links_triage(
    store: WorkStore,
) -> None:
    candidate = _candidate("candidate-1", "a" * 40)
    flow, provider = _flow(
        store,
        candidate,
        observations=(
            {"availability": True},
            {"availability": False},
            {"availability": True},
        ),
    )

    triaged = await flow.resume(WORK_ID, project_id=PROJECT_ID)

    assert triaged.status == "TRIAGING"
    assert [item.exposure.value for item in provider.deployments] == ["5%"]
    assert [item.exposure.value for item in provider.promotions] == ["100%"]
    assert [item.id for item in provider.rollbacks] == [provider.promotions[0].id]

    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    candidate_event = next(
        event
        for event in events
        if event.event_type is WorkEventType.RELEASE_CREATED
        and event.payload_json.get("known_good_baseline") is not True
    )
    production_deployment_event = next(
        event
        for event in events
        if event.event_type is WorkEventType.DEPLOYMENT_RECORDED
        and event.payload_json["deployment"]["exposure"]["value"] == "100%"
    )
    canary_observation_event = next(
        event
        for event in events
        if event.event_type is WorkEventType.OBSERVATION_RECORDED
        and event.payload_json["observation"]["deployment_id"]
        == provider.deployments[0].id
    )
    failed_observation_event = next(
        event
        for event in events
        if event.event_type is WorkEventType.OBSERVATION_RECORDED
        and event.payload_json["observation"]["verdict"] == "fail"
    )
    rollback_event = next(
        event for event in events if event.event_type is WorkEventType.ROLLBACK_RECORDED
    )
    triage_event = next(
        event for event in events if event.event_type is WorkEventType.TRIAGE_CREATED
    )

    release = candidate_event.payload_json["release_candidate"]
    production_deployment = production_deployment_event.payload_json["deployment"]
    canary_observation = canary_observation_event.payload_json["observation"]
    failure = failed_observation_event.payload_json["observation"]
    assert release["id"] == candidate.id
    assert release["work_id"] == WORK_ID
    assert release["commit_sha"] == candidate.commit_sha
    assert production_deployment["release_candidate_id"] == release["id"]
    assert production_deployment["work_id"] == release["work_id"]
    assert canary_observation["verdict"] == "pass"
    assert canary_observation_event.sequence < production_deployment_event.sequence
    assert production_deployment_event.sequence < failed_observation_event.sequence
    assert failed_observation_event.sequence < rollback_event.sequence
    assert rollback_event.sequence < triage_event.sequence
    assert failure["deployment_id"] == production_deployment["id"]
    assert rollback_event.payload_json["source_deployment_id"] == production_deployment["id"]
    assert rollback_event.payload_json["source_release_candidate_id"] == release["id"]
    assert triage_event.payload_json["deployment_id"] == production_deployment["id"]
    assert triage_event.payload_json["observation"] == failure
    assert triage_event.payload_json["evidence_refs"] == [
        f"fake-observation://{production_deployment['id']}/availability",
        "fake-observation://rollback-1/availability",
    ]


@pytest.mark.asyncio
async def test_flow_persists_and_resumes_an_explicit_delivery_approval(
    store: WorkStore,
) -> None:
    candidate = _candidate("candidate-1", "a" * 40)
    flow, deployment, _ = _gated_flow(
        store,
        candidate,
        observations=({"availability": True}, {"availability": True}),
    )

    with pytest.raises(DeliveryApprovalRequiredError):
        await flow.resume(WORK_ID, project_id=PROJECT_ID)
    gated = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert gated is not None and gated.pending_gate is not None
    assert gated.status == "RELEASING"

    approved = await flow.approve(
        WORK_ID,
        project_id=PROJECT_ID,
        gate_id=gated.pending_gate,
        actor_ref="operator:arda",
    )
    assert approved.pending_gate is None
    assert approved.status == "RELEASING"

    with pytest.raises(DeliveryApprovalRequiredError):
        await flow.resume(WORK_ID, project_id=PROJECT_ID)
    assert len(deployment.deployments) == 1


@pytest.mark.asyncio
async def test_gated_failure_resumes_approved_rollback_and_reaches_triaging(
    store: WorkStore,
) -> None:
    candidate = _candidate("candidate-1", "a" * 40)
    flow, deployment, _ = _gated_flow(
        store,
        candidate,
        observations=({"availability": False}, {"availability": True}),
    )

    with pytest.raises(DeliveryApprovalRequiredError, match="deploy_production"):
        await flow.resume(WORK_ID, project_id=PROJECT_ID)
    deploy_gated = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert deploy_gated is not None and deploy_gated.pending_gate is not None
    await flow.approve(
        WORK_ID,
        project_id=PROJECT_ID,
        gate_id=deploy_gated.pending_gate,
        actor_ref="operator:arda",
    )

    with pytest.raises(DeliveryApprovalRequiredError, match="rollback"):
        await flow.resume(WORK_ID, project_id=PROJECT_ID)
    rollback_gated = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert rollback_gated is not None and rollback_gated.pending_gate is not None
    assert rollback_gated.status == "PRODUCTION_CANARY"
    rollback_approved = await flow.approve(
        WORK_ID,
        project_id=PROJECT_ID,
        gate_id=rollback_gated.pending_gate,
        actor_ref="operator:arda",
    )
    assert rollback_approved.status == "PRODUCTION_CANARY"

    triaged = await flow.resume(WORK_ID, project_id=PROJECT_ID)

    assert triaged.status == "TRIAGING"
    assert len(deployment.deployments) == 1
    assert len(deployment.rollbacks) == 1
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    requested_actions = [
        event.payload_json["action"]["action"]
        for event in events
        if event.event_type is WorkEventType.GATE_REQUESTED
    ]
    assert requested_actions == ["deploy_production", "rollback"]
    assert WorkEventType.ROLLBACK_RECORDED in [event.event_type for event in events]
    assert events[-1].event_type is WorkEventType.TRIAGE_CREATED
    with pytest.raises(DeliveryActionDeniedError, match="TRIAGING"):
        await flow.approve(
            WORK_ID,
            project_id=PROJECT_ID,
            gate_id=rollback_gated.pending_gate,
            actor_ref="operator:arda",
        )
    still_triaged = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert still_triaged is not None and still_triaged.status == "TRIAGING"


@pytest.mark.asyncio
async def test_resume_observes_deployment_persisted_before_process_death(
    store: WorkStore,
) -> None:
    candidate = _candidate("candidate-1", "a" * 40)
    flow, deployment, lifecycle = _gated_flow(
        store,
        candidate,
        observations=({"availability": True},),
    )
    with pytest.raises(DeliveryApprovalRequiredError, match="deploy_production"):
        await flow.resume(WORK_ID, project_id=PROJECT_ID)
    gated = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert gated is not None and gated.pending_gate is not None
    await flow.approve(
        WORK_ID,
        project_id=PROJECT_ID,
        gate_id=gated.pending_gate,
        actor_ref="operator:arda",
    )
    persisted_candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=candidate.commit_sha,
        evidence_refs=("merge://sha", "review://accepted"),
    )
    await lifecycle.deploy(
        persisted_candidate,
        environment="production",
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=("policy://docs-production",),
        expected_duration_seconds=30,
    )

    with pytest.raises(DeliveryApprovalRequiredError, match="promote_rollout"):
        await flow.resume(WORK_ID, project_id=PROJECT_ID)

    assert len(deployment.deployments) == 1
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    assert sum(event.event_type is WorkEventType.OBSERVATION_RECORDED for event in events) == 1


@pytest.mark.asyncio
async def test_resume_observes_rollback_persisted_before_process_death(
    store: WorkStore,
) -> None:
    candidate = _candidate("candidate-1", "a" * 40)
    flow, deployment, lifecycle = _gated_flow(
        store,
        candidate,
        observations=({"availability": False}, {"availability": True}),
    )
    with pytest.raises(DeliveryApprovalRequiredError, match="deploy_production"):
        await flow.resume(WORK_ID, project_id=PROJECT_ID)
    deploy_gated = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert deploy_gated is not None and deploy_gated.pending_gate is not None
    await flow.approve(
        WORK_ID,
        project_id=PROJECT_ID,
        gate_id=deploy_gated.pending_gate,
        actor_ref="operator:arda",
    )
    with pytest.raises(DeliveryApprovalRequiredError, match="rollback"):
        await flow.resume(WORK_ID, project_id=PROJECT_ID)
    rollback_gated = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert rollback_gated is not None and rollback_gated.pending_gate is not None
    await flow.approve(
        WORK_ID,
        project_id=PROJECT_ID,
        gate_id=rollback_gated.pending_gate,
        actor_ref="operator:arda",
    )
    await lifecycle.rollback(
        deployment.deployments[0],
        known_good_candidate=_known_good(),
        evidence_refs=("observation://failed",),
        expected_duration_seconds=30,
    )

    triaged = await flow.resume(WORK_ID, project_id=PROJECT_ID)

    assert triaged.status == "TRIAGING"
    assert len(deployment.rollbacks) == 1
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    rollback_index = next(
        index
        for index, event in enumerate(events)
        if event.event_type is WorkEventType.ROLLBACK_RECORDED
    )
    assert events[rollback_index + 1].event_type is WorkEventType.OBSERVATION_RECORDED
    assert events[-1].event_type is WorkEventType.TRIAGE_CREATED


@pytest.mark.asyncio
async def test_failed_rollback_verification_escalates_once_with_evidence(
    store: WorkStore,
) -> None:
    candidate = _candidate("candidate-1", "a" * 40)
    flow, provider = _flow(
        store,
        candidate,
        observations=({"availability": False}, {"availability": False}),
    )

    for _attempt in range(2):
        with pytest.raises(
            DeliveryActionDeniedError,
            match="rollback verification did not pass",
        ):
            await flow.resume(WORK_ID, project_id=PROJECT_ID)

    assert len(provider.deployments) == 1
    assert len(provider.rollbacks) == 1
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    critical = [
        event
        for event in events
        if event.event_type is WorkEventType.CONTROL_DEGRADED
        and event.payload_json.get("failed_preconditions")
        == ["rollback-verification"]
    ]
    assert len(critical) == 1
    assert critical[0].payload_json["severity"] == "critical"
    assert critical[0].payload_json["deployment_id"] == provider.deployments[0].id
    assert critical[0].payload_json["evidence_refs"] == [
        "fake-observation://rollback-1/availability"
    ]
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert len(pending) == 1
    assert pending[0].kind.value == "PRODUCTION_INCIDENT"
    assert pending[0].severity == "critical"
    assert pending[0].evidence_refs == (
        "fake-observation://deployment-1/availability",
        "fake-observation://rollback-1/availability",
    )


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

    assert triaged.status == "TRIAGING"
    assert first_provider.rollbacks
    triaged_pending = await store.pending_attention(project_id=PROJECT_ID)
    assert len(triaged_pending) == 1
    assert triaged_pending[0].kind.value == "PRODUCTION_INCIDENT"
    assert triaged_pending[0].severity == "high"
    await store.save_work(triaged.model_copy(update={"status": "READY_TO_DELIVER"}))

    repaired_candidate = _candidate("candidate-3", "c" * 40)
    repaired_flow, second_provider = _flow(
        store,
        repaired_candidate,
        observations=({"availability": True}, {"availability": True}),
    )
    completed = await repaired_flow.resume(WORK_ID, project_id=PROJECT_ID)

    assert completed.status == "COMPLETE"
    assert await store.pending_attention(project_id=PROJECT_ID) == ()
    assert [item.release_candidate_id for item in second_provider.deployments] == [
        repaired_candidate.id
    ]
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    assert WorkEventType.TRIAGE_CREATED in [event.event_type for event in events]
    assert events[-1].event_type is WorkEventType.WORK_COMPLETED


@pytest.mark.parametrize(
    ("first_status", "deployment_get_statuses", "expected_status"),
    (
        (200, (), "COMPLETE"),
        (503, (), "TRIAGING"),
        (200, (200, 403), "CONTROL_DEGRADED"),
    ),
)
@pytest.mark.asyncio
async def test_real_adapter_runs_through_lifecycle_and_flow(
    store: WorkStore,
    tmp_path: Path,
    first_status: int,
    deployment_get_statuses: tuple[int, ...],
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
    state.deployment_get_statuses = list(deployment_get_statuses)
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

        if expected_status == "CONTROL_DEGRADED":
            with pytest.raises(ControlDegradedError):
                await flow.resume(WORK_ID, project_id=PROJECT_ID)
            completed = await store.load_work(WORK_ID, project_id=PROJECT_ID)
        else:
            completed = await flow.resume(WORK_ID, project_id=PROJECT_ID)

    if expected_status == "CONTROL_DEGRADED":
        assert completed is not None and completed.status == "PRODUCTION_CANARY"
        events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
        assert events[-1].event_type is WorkEventType.CONTROL_DEGRADED
        assert WorkEventType.OBSERVATION_RECORDED not in [event.event_type for event in events]
        return
    assert completed is not None and completed.status == expected_status
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
    if expected_status == "TRIAGING":
        events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
        event_types = [event.event_type for event in events]
        assert WorkEventType.ROLLBACK_RECORDED in event_types
        assert event_types[-1] is WorkEventType.TRIAGE_CREATED
