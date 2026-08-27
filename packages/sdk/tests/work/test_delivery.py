# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Release and delivery lifecycle contracts driven entirely by deterministic fakes."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sagewai.work import (
    ControlCheckResult,
    ControlDegradedError,
    ControlPrecondition,
    ControlPreconditionKind,
    GateDecision,
    WorkEvent,
    WorkEventType,
    WorkRecord,
    WorkStore,
)
from sagewai.work.profiles.software.delivery import (
    BlastRadius,
    DeliveryActionDeniedError,
    DeliveryApprovalRequiredError,
    DeliveryControlRequest,
    DeliveryLifecycle,
    Deployment,
    HealthGate,
    HealthVerdict,
    ReleaseCandidate,
)
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.fakes_delivery import (
    DeterministicFakeControlProbe,
    DeterministicFakeDeploymentProvider,
    DeterministicFakeObservationProvider,
    DeterministicFakeReleaseProvider,
)

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
PROJECT_ID = "project-a"
WORK_ID = "work-1"
COMMIT_SHA = "a" * 40


def _candidate(
    *,
    candidate_id: str = "candidate-1",
    artifact_ref: str = "artifact://candidate-1",
    digest: str = "b" * 64,
    work_id: str = WORK_ID,
) -> ReleaseCandidate:
    return ReleaseCandidate(
        id=candidate_id,
        project_id=PROJECT_ID,
        work_id=work_id,
        commit_sha=COMMIT_SHA,
        artifact_ref=artifact_ref,
        artifact_digest=digest,
        config_revision="config-1",
        verification_ref="verification://1",
        review_ref="review://1",
    )


def _known_good() -> ReleaseCandidate:
    return _candidate(
        candidate_id="known-good",
        artifact_ref="artifact://known-good",
        digest="c" * 64,
        work_id="work-previous",
    )


def _record() -> WorkRecord:
    return WorkRecord(
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


def _result(
    precondition_id: str,
    *,
    passed: bool = True,
    detail: str | None = None,
) -> ControlCheckResult:
    return ControlCheckResult(
        project_id=PROJECT_ID,
        precondition_id=precondition_id,
        passed=passed,
        evidence_refs=(f"check://{precondition_id}",),
        detail=detail,
        checked_at=NOW,
    )


def _preconditions() -> tuple[ControlPrecondition, ...]:
    all_actions = ("deploy", "promote", "observe", "rollback")
    state_changes = ("deploy", "promote")
    return (
        ControlPrecondition(
            id="delivery-authority",
            project_id=PROJECT_ID,
            kind=ControlPreconditionKind.AUTHORITY,
            description="delivery authority",
            check_ref="fake.authority",
            required_for=state_changes,
        ),
        ControlPrecondition(
            id="delivery-observability",
            project_id=PROJECT_ID,
            kind=ControlPreconditionKind.OBSERVABILITY,
            description="delivery observability",
            check_ref="fake.observability",
            required_for=all_actions,
        ),
        ControlPrecondition(
            id="rollback-artifact",
            project_id=PROJECT_ID,
            kind=ControlPreconditionKind.REVERSIBILITY,
            description="delivery reversibility",
            check_ref="fake.reversibility",
            required_for=("deploy", "promote", "rollback"),
        ),
        ControlPrecondition(
            id="rollback-authority",
            project_id=PROJECT_ID,
            kind=ControlPreconditionKind.AUTHORITY,
            description="rollback authority",
            check_ref="fake.rollback_authority",
            required_for=("deploy", "promote", "rollback"),
        ),
    )


def _passing_probe() -> DeterministicFakeControlProbe:
    authority = _result("delivery-authority")
    observability = _result("delivery-observability")
    reversibility = _result("rollback-artifact")
    rollback_authority = _result("rollback-authority")
    return DeterministicFakeControlProbe(
        {
            "deploy": (authority, observability, reversibility, rollback_authority),
            "promote": (authority, observability, reversibility, rollback_authority),
            "observe": (observability,),
            "rollback": (rollback_authority, observability, reversibility),
        }
    )


class RecordingPolicy:
    def __init__(self, decision: GateDecision = GateDecision.ALLOW) -> None:
        self.decision = decision
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.decision


@pytest.fixture
async def store(dialect_engine) -> WorkStore:  # noqa: F811
    result = WorkStore(engine=dialect_engine)
    await result.init()
    await result.save_work(_record())
    known_good = _known_good()
    await result.append_event(
        WorkEvent(
            id="release-known-good",
            project_id=PROJECT_ID,
            work_id=known_good.work_id,
            sequence=1,
            event_type=WorkEventType.RELEASE_CREATED,
            actor_type="test",
            actor_ref=None,
            payload_json={"release_candidate": known_good.model_dump(mode="json")},
            created_at=NOW,
        )
    )
    return result


async def _record_deployment(store: WorkStore, deployment: Deployment) -> None:
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    await store.append_event(
        WorkEvent(
            id=f"record-{deployment.id}",
            project_id=PROJECT_ID,
            work_id=WORK_ID,
            sequence=events[-1].sequence + 1,
            event_type=WorkEventType.DEPLOYMENT_RECORDED,
            actor_type="test",
            actor_ref=None,
            payload_json={
                "action": "deploy_production",
                "deployment": deployment.model_dump(mode="json"),
            },
            created_at=NOW,
        )
    )


def _lifecycle(
    store: WorkStore,
    *,
    control_probe=None,
    policy=None,
    observations=({"availability": True},),
):
    candidate = _candidate()
    release = DeterministicFakeReleaseProvider(candidate)
    deployment = DeterministicFakeDeploymentProvider()
    observation = DeterministicFakeObservationProvider(observations)
    action_policy = policy or RecordingPolicy()
    lifecycle = DeliveryLifecycle(
        work_store=store,
        release_provider=release,
        deployment_provider=deployment,
        observation_provider=observation,
        control_probe=control_probe or _passing_probe(),
        control_preconditions=_preconditions(),
        action_policy=action_policy,
    )
    return lifecycle, release, deployment, observation, action_policy


def test_delivery_models_are_immutable_and_project_scoped() -> None:
    candidate = _candidate()
    exposure = BlastRadius(dimension="traffic", value="5%")

    assert candidate.project_id == PROJECT_ID
    assert exposure.value == "5%"
    with pytest.raises(ValidationError):
        candidate.commit_sha = "c" * 40  # type: ignore[misc]
    with pytest.raises(ValidationError):
        BlastRadius(dimension="cluster", value="one")


@pytest.mark.asyncio
async def test_fake_lifecycle_drives_staging_canary_rollout_observation_and_rollback(
    store: WorkStore,
) -> None:
    lifecycle, release, deployment, observation, policy = _lifecycle(
        store,
        observations=(
            {"availability": True},
            {"availability": True},
            {"availability": False},
            {"availability": True},
        ),
    )
    known_good = _known_good()
    gate = HealthGate(
        id="availability",
        project_id=PROJECT_ID,
        description="docs endpoint remains available",
        check_ref="http://docs-availability",
        failure_verdict=HealthVerdict.FAIL,
    )

    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=("merge://sha",),
    )
    staging = await lifecycle.deploy(
        candidate,
        environment="staging",
        exposure=BlastRadius(dimension="instances", value="1"),
        known_good_candidate=known_good,
        evidence_refs=("policy://staging",),
        expected_duration_seconds=60,
    )
    assert (
        await lifecycle.observe(staging, gates=(gate,), window_seconds=60)
    ).verdict is HealthVerdict.PASS

    canary = await lifecycle.deploy(
        candidate,
        environment="production",
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=known_good,
        evidence_refs=("policy://canary",),
        expected_duration_seconds=300,
    )
    assert canary.release_candidate_id == staging.release_candidate_id == candidate.id
    assert (
        await lifecycle.observe(canary, gates=(gate,), window_seconds=300)
    ).verdict is HealthVerdict.PASS

    rollout = await lifecycle.promote(
        canary,
        exposure=BlastRadius(dimension="traffic", value="100%"),
        known_good_candidate=known_good,
        evidence_refs=("policy://rollout",),
        expected_duration_seconds=600,
    )
    failed = await lifecycle.observe(rollout, gates=(gate,), window_seconds=600)
    assert failed.verdict is HealthVerdict.FAIL

    rolled_back = await lifecycle.rollback(
        rollout,
        known_good_candidate=known_good,
        evidence_refs=("observation://failed",),
        expected_duration_seconds=600,
    )

    rollback_observation = await lifecycle.observe(
        rolled_back,
        gates=(gate,),
        window_seconds=60,
    )

    assert rolled_back.status == "rolled_back"
    assert rollback_observation.verdict is HealthVerdict.PASS
    assert release.builds == [COMMIT_SHA]
    assert [item.environment for item in deployment.deployments] == [
        "staging",
        "production",
    ]
    assert [item.exposure.value for item in deployment.promotions] == ["100%"]
    assert deployment.rollbacks == [rollout]
    assert observation.calls == [
        (staging.id, 60),
        (canary.id, 300),
        (rollout.id, 600),
        (rolled_back.id, 60),
    ]
    assert [request.action for request in policy.requests] == [
        "build_release",
        "deploy_staging",
        "deploy_production",
        "promote_rollout",
        "rollback",
    ]
    assert policy.requests[-1].reversibility.value == "snapshot_reversible"

    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    assert [event.event_type for event in events] == [
        WorkEventType.RELEASE_CREATED,
        WorkEventType.DEPLOYMENT_RECORDED,
        WorkEventType.OBSERVATION_RECORDED,
        WorkEventType.DEPLOYMENT_RECORDED,
        WorkEventType.OBSERVATION_RECORDED,
        WorkEventType.DEPLOYMENT_RECORDED,
        WorkEventType.OBSERVATION_RECORDED,
        WorkEventType.ROLLBACK_RECORDED,
        WorkEventType.OBSERVATION_RECORDED,
    ]


@pytest.mark.parametrize("verdict", (HealthVerdict.HOLD, HealthVerdict.FAIL))
@pytest.mark.asyncio
async def test_nonpassing_health_gate_never_increases_exposure(
    store: WorkStore,
    verdict: HealthVerdict,
) -> None:
    gate = HealthGate(
        id="availability",
        project_id=PROJECT_ID,
        description="availability",
        check_ref="http://availability",
        failure_verdict=verdict,
    )
    lifecycle, _, deployment, _, _ = _lifecycle(
        store,
        observations=({"availability": False},),
    )
    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )
    canary = await lifecycle.deploy(
        candidate,
        environment="production",
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    result = await lifecycle.observe(canary, gates=(gate,), window_seconds=30)

    assert result.verdict is verdict
    with pytest.raises(DeliveryActionDeniedError, match="passing observation"):
        await lifecycle.promote(
            canary,
            exposure=BlastRadius(dimension="traffic", value="20%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )
    assert deployment.promotions == []


@pytest.mark.asyncio
async def test_nonpassing_observation_cannot_be_bypassed_by_redeploy(
    store: WorkStore,
) -> None:
    gate = HealthGate(
        id="availability",
        project_id=PROJECT_ID,
        description="availability",
        check_ref="http://availability",
        failure_verdict=HealthVerdict.FAIL,
    )
    lifecycle, _, provider, _, _ = _lifecycle(
        store,
        observations=({"availability": False},),
    )
    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )
    canary = await lifecycle.deploy(
        candidate,
        environment="production",
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    assert (
        await lifecycle.observe(canary, gates=(gate,), window_seconds=30)
    ).verdict is HealthVerdict.FAIL

    with pytest.raises(DeliveryActionDeniedError, match="use promote"):
        await lifecycle.deploy(
            candidate,
            environment="production",
            exposure=BlastRadius(dimension="traffic", value="100%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )

    assert provider.deployments == [canary]


@pytest.mark.asyncio
async def test_rolled_back_deployment_cannot_be_promoted(
    store: WorkStore,
) -> None:
    gate = HealthGate(
        id="availability",
        project_id=PROJECT_ID,
        description="availability",
        check_ref="http://availability",
        failure_verdict=HealthVerdict.FAIL,
    )
    lifecycle, _, provider, _, _ = _lifecycle(
        store,
        observations=({"availability": True},),
    )
    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )
    deployment = await lifecycle.deploy(
        candidate,
        environment="production",
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    rolled_back = await lifecycle.rollback(
        deployment,
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    assert (
        await lifecycle.observe(rolled_back, gates=(gate,), window_seconds=30)
    ).verdict is HealthVerdict.PASS

    with pytest.raises(DeliveryActionDeniedError, match="cannot be promoted"):
        await lifecycle.promote(
            rolled_back,
            exposure=BlastRadius(dimension="traffic", value="100%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )

    assert provider.promotions == []


@pytest.mark.parametrize(
    ("failed_id", "detail"),
    (
        ("rollback-artifact", "known-good artifact is missing"),
        ("rollback-authority", "rollback credential is expired"),
    ),
)
@pytest.mark.asyncio
async def test_failed_rollback_precondition_refuses_action_and_freezes_work(
    store: WorkStore,
    failed_id: str,
    detail: str,
) -> None:
    probe = DeterministicFakeControlProbe(
        {
            "rollback": (
                _result("rollback-authority", passed=failed_id != "rollback-authority"),
                _result("delivery-observability"),
                _result(
                    "rollback-artifact",
                    passed=failed_id != "rollback-artifact",
                    detail=detail if failed_id == "rollback-artifact" else None,
                ),
            )
        }
    )
    lifecycle, _, provider, _, _ = _lifecycle(store, control_probe=probe)
    await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )
    deployment = Deployment(
        id="deployment-1",
        project_id=PROJECT_ID,
        work_id=WORK_ID,
        release_candidate_id="candidate-1",
        environment="production",
        exposure=BlastRadius(dimension="traffic", value="5%"),
        provider_ref="fake://deployment/1",
        status="active",
    )
    await _record_deployment(store, deployment)

    with pytest.raises(ControlDegradedError, match=failed_id):
        await lifecycle.rollback(
            deployment,
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )

    assert provider.rollbacks == []
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert [item.attention_id for item in pending] == [failed_id]
    assert pending[0].kind.value == "CONTROL_DEGRADED"


@pytest.mark.asyncio
async def test_blind_deploy_and_unapproved_deploy_are_impossible(store: WorkStore) -> None:
    failing_probe = DeterministicFakeControlProbe(
        {
            "deploy": (
                _result("delivery-authority", passed=False),
                _result("delivery-observability"),
                _result("rollback-artifact"),
                _result("rollback-authority"),
            )
        }
    )
    lifecycle, _, provider, _, _ = _lifecycle(store, control_probe=failing_probe)
    await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )

    with pytest.raises(ControlDegradedError, match="delivery-authority"):
        await lifecycle.deploy(
            _candidate(),
            environment="production",
            exposure=BlastRadius(dimension="traffic", value="5%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )
    assert provider.deployments == []

    approval_policy = RecordingPolicy(GateDecision.REQUIRE_APPROVAL)
    lifecycle, _, provider, _, _ = _lifecycle(store, policy=approval_policy)
    with pytest.raises(DeliveryApprovalRequiredError, match="deploy_production"):
        await lifecycle.deploy(
            _candidate(),
            environment="production",
            exposure=BlastRadius(dimension="traffic", value="5%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )
    assert provider.deployments == []
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    assert [event.event_type for event in events[-3:]] == [
        WorkEventType.CONTROL_DEGRADED,
        WorkEventType.CONTROL_RESTORED,
        WorkEventType.GATE_REQUESTED,
    ]
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert [item.kind.value for item in pending] == ["GATE_REQUESTED"]


@pytest.mark.parametrize("action", ("promote", "rollback"))
@pytest.mark.asyncio
async def test_policy_denies_promotion_and_rollback_before_provider_side_effect(
    store: WorkStore,
    action: str,
) -> None:
    policy = RecordingPolicy()
    lifecycle, _, provider, _, _ = _lifecycle(
        store,
        policy=policy,
        observations=({"availability": True},),
    )
    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )
    deployment = await lifecycle.deploy(
        candidate,
        environment="production",
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    if action == "promote":
        gate = HealthGate(
            id="availability",
            project_id=PROJECT_ID,
            description="availability",
            check_ref="http://availability",
            failure_verdict=HealthVerdict.FAIL,
        )
        await lifecycle.observe(deployment, gates=(gate,), window_seconds=30)
    policy.decision = GateDecision.DENY

    with pytest.raises(DeliveryActionDeniedError):
        if action == "promote":
            await lifecycle.promote(
                deployment,
                exposure=BlastRadius(dimension="traffic", value="100%"),
                known_good_candidate=_known_good(),
                evidence_refs=(),
                expected_duration_seconds=60,
            )
        else:
            await lifecycle.rollback(
                deployment,
                known_good_candidate=_known_good(),
                evidence_refs=(),
                expected_duration_seconds=60,
            )

    assert provider.promotions == []
    assert provider.rollbacks == []


@pytest.mark.asyncio
async def test_delivery_policy_denial_blocks_work_without_side_effect(
    store: WorkStore,
) -> None:
    lifecycle, _, _, _, _ = _lifecycle(store)
    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )
    denied = RecordingPolicy(GateDecision.DENY)
    lifecycle, _, provider, _, _ = _lifecycle(store, policy=denied)

    with pytest.raises(DeliveryActionDeniedError, match="deploy_production"):
        await lifecycle.deploy(
            candidate,
            environment="production",
            exposure=BlastRadius(dimension="traffic", value="5%"),
            known_good_candidate=_known_good(),
            evidence_refs=("policy://deny",),
            expected_duration_seconds=60,
        )

    assert provider.deployments == []
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None
    assert record.status == "WORK_BLOCKED"
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert [item.kind.value for item in pending] == ["WORK_BLOCKED"]


def test_control_request_is_immutable() -> None:
    request = DeliveryControlRequest(
        project_id=PROJECT_ID,
        work_id=WORK_ID,
        action="observe",
        candidate=_candidate(),
        deployment=None,
        known_good_candidate=None,
        expected_duration_seconds=60,
    )

    with pytest.raises(ValidationError):
        request.action = "deploy"  # type: ignore[misc]
