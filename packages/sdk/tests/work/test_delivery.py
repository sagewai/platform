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

import asyncio
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sagewai.work import (
    ActionRequest,
    ControlCheckResult,
    ControlDegradedError,
    ControlPrecondition,
    ControlPreconditionKind,
    GateDecision,
    Reversibility,
    WorkEvent,
    WorkEventType,
    WorkRecord,
    WorkStore,
)
from sagewai.work.profiles.software.delivery import (
    BlastRadius,
    DeliveryActionDeniedError,
    DeliveryApprovalRequiredError,
    DeliveryControlLostError,
    DeliveryControlRequest,
    DeliveryLifecycle,
    Deployment,
    HealthGate,
    HealthVerdict,
    ReleaseCandidate,
    default_delivery_action_policy,
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
    commit_sha: str = COMMIT_SHA,
) -> ReleaseCandidate:
    return ReleaseCandidate(
        id=candidate_id,
        project_id=PROJECT_ID,
        work_id=work_id,
        commit_sha=commit_sha,
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


@pytest.mark.parametrize(
    ("risk", "reversibility", "expected"),
    (
        ("low", Reversibility.PURE, GateDecision.ALLOW),
        ("critical", Reversibility.PURE, GateDecision.DENY),
        ("low", Reversibility.SNAPSHOT_REVERSIBLE, GateDecision.REQUIRE_APPROVAL),
        ("high", Reversibility.COMPENSATABLE, GateDecision.REQUIRE_APPROVAL),
        ("low", Reversibility.IRREVERSIBLE, GateDecision.DENY),
        ("critical", Reversibility.SNAPSHOT_REVERSIBLE, GateDecision.DENY),
    ),
)
def test_default_delivery_action_policy(
    risk: str,
    reversibility: Reversibility,
    expected: GateDecision,
) -> None:
    request = ActionRequest(
        project_id=PROJECT_ID,
        action="execute_migration",
        work_id=WORK_ID,
        risk=risk,
        reversibility=reversibility,
        scope="database://project-a/schema",
        evidence_refs=("policy://request-claims-approval",),
    )

    assert default_delivery_action_policy(request) is expected


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


async def _record_degradation(store: WorkStore, precondition_id: str) -> None:
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    await store.append_event(
        WorkEvent(
            id=f"degraded-{precondition_id}",
            project_id=PROJECT_ID,
            work_id=WORK_ID,
            sequence=events[-1].sequence + 1,
            event_type=WorkEventType.CONTROL_DEGRADED,
            actor_type="test",
            actor_ref=None,
            payload_json={
                "failed_preconditions": [precondition_id],
                "evidence_refs": [f"check://{precondition_id}"],
                "frozen_action_ids": ["state-changing"],
            },
            created_at=NOW,
        )
    )


def _lifecycle(
    store: WorkStore,
    *,
    control_probe=None,
    deployment_provider=None,
    observation_provider=None,
    policy=None,
    observations=({"availability": True},),
    heartbeat_interval: float = 30,
):
    candidate = _candidate()
    release = DeterministicFakeReleaseProvider(candidate)
    deployment = deployment_provider or DeterministicFakeDeploymentProvider()
    observation = observation_provider or DeterministicFakeObservationProvider(observations)
    action_policy = policy or RecordingPolicy()
    lifecycle = DeliveryLifecycle(
        work_store=store,
        release_provider=release,
        deployment_provider=deployment,
        observation_provider=observation,
        control_probe=control_probe or _passing_probe(),
        control_preconditions=_preconditions(),
        action_policy=action_policy,
        heartbeat_interval=heartbeat_interval,
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
async def test_delivery_events_project_canonical_work_status(store: WorkStore) -> None:
    gate = HealthGate(
        id="availability",
        project_id=PROJECT_ID,
        description="availability",
        check_ref="http://availability",
        failure_verdict=HealthVerdict.FAIL,
    )
    lifecycle, _, _, _, _ = _lifecycle(
        store,
        observations=(
            {"availability": True},
            {"availability": True},
            {"availability": True},
            {"availability": True},
            {"availability": False},
            {"availability": True},
        ),
    )

    baseline = _candidate(
        candidate_id="baseline-current-work",
        artifact_ref="artifact://baseline-current-work",
        digest="d" * 64,
        commit_sha="e" * 40,
    )
    await lifecycle.register_known_good(baseline, evidence_refs=("config://baseline",))
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None and record.status == "READY_TO_DELIVER"

    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=("merge://sha",),
    )
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None and record.status == "RELEASING"

    staging = await lifecycle.deploy(
        candidate,
        environment="staging",
        risk="medium",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="instances", value="1"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None and record.status == "STAGING"
    await lifecycle.observe(staging, gates=(gate,), window_seconds=30)
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None and record.status == "STAGING"

    deployment = await lifecycle.deploy(
        candidate,
        environment="production",
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None and record.status == "PRODUCTION_CANARY"
    await lifecycle.observe(deployment, gates=(gate,), window_seconds=30)

    for exposure in ("20%", "50%"):
        deployment = await lifecycle.promote(
            deployment,
            exposure=BlastRadius(dimension="traffic", value=exposure),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )
        record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
        assert record is not None and record.status == "PRODUCTION_CANARY"
        await lifecycle.observe(deployment, gates=(gate,), window_seconds=30)

    rollout = await lifecycle.promote(
        deployment,
        exposure=BlastRadius(dimension="traffic", value="100%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None and record.status == "PRODUCTION_ROLLOUT"
    failed = await lifecycle.observe(rollout, gates=(gate,), window_seconds=30)
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None and record.status == "PRODUCTION_ROLLOUT"

    rolled_back = await lifecycle.rollback(
        rollout,
        known_good_candidate=_known_good(),
        evidence_refs=("observation://failed",),
        expected_duration_seconds=60,
    )
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None and record.status == "ROLLING_BACK"
    await lifecycle.observe(rolled_back, gates=(gate,), window_seconds=30)
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None and record.status == "ROLLING_BACK"

    triaged = await lifecycle.triage(
        rollout,
        observation=failed,
        summary="Full production rollout failed.",
        evidence_refs=("observation://failed",),
    )
    assert triaged.status == "TRIAGING"


@pytest.mark.parametrize("failure_verdict", (HealthVerdict.HOLD, HealthVerdict.FAIL))
@pytest.mark.asyncio
async def test_nonpassing_full_rollout_does_not_project_soaking(
    store: WorkStore,
    failure_verdict: HealthVerdict,
) -> None:
    gate = HealthGate(
        id="availability",
        project_id=PROJECT_ID,
        description="availability",
        check_ref="http://availability",
        failure_verdict=failure_verdict,
    )
    lifecycle, _, _, _, _ = _lifecycle(
        store,
        observations=({"availability": False},),
    )
    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )
    rollout = await lifecycle.deploy(
        candidate,
        environment="production",
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="100%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )

    observation = await lifecycle.observe(rollout, gates=(gate,), window_seconds=30)

    assert observation.verdict is failure_verdict
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None and record.status == "PRODUCTION_ROLLOUT"


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
        risk="medium",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
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
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
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
    assert policy.requests[0].reversibility.value == "pure"
    assert policy.requests[-1].reversibility.value == "snapshot_reversible"

    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    assert [event.event_type for event in events] == [
        WorkEventType.GATE_DECIDED,
        WorkEventType.RELEASE_CREATED,
        WorkEventType.GATE_DECIDED,
        WorkEventType.DEPLOYMENT_RECORDED,
        WorkEventType.OBSERVATION_RECORDED,
        WorkEventType.GATE_DECIDED,
        WorkEventType.DEPLOYMENT_RECORDED,
        WorkEventType.OBSERVATION_RECORDED,
        WorkEventType.GATE_DECIDED,
        WorkEventType.DEPLOYMENT_RECORDED,
        WorkEventType.OBSERVATION_RECORDED,
        WorkEventType.EXTERNAL_OUTCOME_RECORDED,
        WorkEventType.GATE_DECIDED,
        WorkEventType.ROLLBACK_RECORDED,
        WorkEventType.EXTERNAL_OUTCOME_RECORDED,
        WorkEventType.OBSERVATION_RECORDED,
    ]
    rollback_event = next(
        event for event in events if event.event_type is WorkEventType.ROLLBACK_RECORDED
    )
    assert rollback_event.payload_json["evidence_refs"] == ["observation://failed"]


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
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
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


@pytest.mark.parametrize("verdict", (HealthVerdict.HOLD, HealthVerdict.FAIL))
@pytest.mark.asyncio
async def test_later_nonpassing_receipt_blocks_promotion_from_stale_pass(
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
    lifecycle, _, provider, _, _ = _lifecycle(
        store,
        observations=(
            {"availability": True},
            {"availability": False},
        ),
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
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    assert (
        await lifecycle.observe(canary, gates=(gate,), window_seconds=30)
    ).verdict is HealthVerdict.PASS
    later_receipt = await lifecycle.promote(
        canary,
        exposure=BlastRadius(dimension="traffic", value="20%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    assert (
        await lifecycle.observe(later_receipt, gates=(gate,), window_seconds=30)
    ).verdict is verdict

    with pytest.raises(DeliveryActionDeniedError, match="non-passing observation"):
        await lifecycle.promote(
            canary,
            exposure=BlastRadius(dimension="traffic", value="100%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )

    assert [receipt.exposure.value for receipt in provider.promotions] == ["20%"]


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
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
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
            risk="high",
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
            exposure=BlastRadius(dimension="traffic", value="100%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )

    assert provider.deployments == [canary]


@pytest.mark.asyncio
async def test_failed_candidate_cannot_be_deployed_to_another_environment(
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
    staging = await lifecycle.deploy(
        candidate,
        environment="staging",
        risk="medium",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="instances", value="1"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    assert (
        await lifecycle.observe(staging, gates=(gate,), window_seconds=30)
    ).verdict is HealthVerdict.FAIL

    with pytest.raises(DeliveryActionDeniedError, match="non-passing observation"):
        await lifecycle.deploy(
            candidate,
            environment="production",
            risk="high",
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
            exposure=BlastRadius(dimension="traffic", value="5%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )

    assert provider.deployments == [staging]


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
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
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
    events_before_duplicate = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    with pytest.raises(DeliveryActionDeniedError, match="already rolled back"):
        await lifecycle.rollback(
            rolled_back,
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )
    assert await store.read_events(WORK_ID, project_id=PROJECT_ID) == events_before_duplicate

    with pytest.raises(DeliveryActionDeniedError, match="rolled-back candidate"):
        await lifecycle.deploy(
            candidate,
            environment="staging",
            risk="medium",
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
            exposure=BlastRadius(dimension="instances", value="1"),
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
    assert provider.deployments == [deployment]


@pytest.mark.asyncio
async def test_rollback_of_promoted_receipt_freezes_sibling_candidate_receipts(
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
    canary = await lifecycle.deploy(
        candidate,
        environment="production",
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    await lifecycle.observe(canary, gates=(gate,), window_seconds=30)
    promoted = await lifecycle.promote(
        canary,
        exposure=BlastRadius(dimension="traffic", value="50%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    await lifecycle.rollback(
        promoted,
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )

    with pytest.raises(DeliveryActionDeniedError, match="cannot be promoted"):
        await lifecycle.promote(
            canary,
            exposure=BlastRadius(dimension="traffic", value="100%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )

    assert len(provider.promotions) == 1


@pytest.mark.asyncio
async def test_provider_reusing_deployment_id_requires_new_observation(
    store: WorkStore,
) -> None:
    class StableIdentityProvider(DeterministicFakeDeploymentProvider):
        async def promote(self, deployment, exposure):
            self.promotions.append(deployment)
            return deployment.model_copy(update={"exposure": exposure})

    gate = HealthGate(
        id="availability",
        project_id=PROJECT_ID,
        description="availability",
        check_ref="http://availability",
        failure_verdict=HealthVerdict.FAIL,
    )
    provider = StableIdentityProvider()
    lifecycle, _, _, _, _ = _lifecycle(
        store,
        deployment_provider=provider,
        observations=({"availability": True},),
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
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    await lifecycle.observe(canary, gates=(gate,), window_seconds=30)
    promoted = await lifecycle.promote(
        canary,
        exposure=BlastRadius(dimension="traffic", value="20%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    assert promoted.id == canary.id

    with pytest.raises(DeliveryActionDeniedError, match="passing observation"):
        await lifecycle.promote(
            promoted,
            exposure=BlastRadius(dimension="traffic", value="100%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )

    assert len(provider.promotions) == 1


@pytest.mark.parametrize(
    ("failed_id", "detail"),
    (
        ("rollback-artifact", "known-good artifact is missing"),
        ("rollback-authority", "rollback credential is expired"),
        pytest.param("rollback-authority", "x" * 2000, id="overlong-detail"),
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
                _result(
                    "rollback-authority",
                    passed=failed_id != "rollback-authority",
                    detail=detail if failed_id == "rollback-authority" else None,
                ),
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
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    degraded = next(
        event for event in events if event.event_type is WorkEventType.CONTROL_DEGRADED
    )
    incident_event = next(
        event
        for event in events
        if event.event_type is WorkEventType.EXTERNAL_OUTCOME_RECORDED
    )
    assert len(incident_event.payload_json["incident"]["summary"]) <= 500
    assert incident_event.payload_json["incident"]["active_control_event_ids"] == [
        degraded.id
    ]
    assert degraded.payload_json["severity"] == "critical"
    assert degraded.payload_json["action"] == "rollback"
    assert degraded.payload_json["deployment_id"] == deployment.id
    assert "environment" not in degraded.payload_json
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert len(pending) == 1
    assert pending[0].attention_id == "software-delivery:deployment-1"
    assert pending[0].kind.value == "EXTERNAL_OUTCOME_INCIDENT"
    assert pending[0].evidence_refs == (f"check://{failed_id}",)


@pytest.mark.asyncio
async def test_rollback_provider_failure_escalates_once_and_is_not_retried(
    store: WorkStore,
) -> None:
    class FailingRollbackProvider(DeterministicFakeDeploymentProvider):
        def __init__(self) -> None:
            super().__init__()
            self.rollback_attempts = 0

        async def rollback(self, deployment, known_good_candidate):
            self.rollback_attempts += 1
            raise RuntimeError("provider rejected rollback")

    provider = FailingRollbackProvider()
    lifecycle, _, _, _, _ = _lifecycle(store, deployment_provider=provider)
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

    for _attempt in range(2):
        with pytest.raises(DeliveryActionDeniedError, match="rollback provider failed"):
            await lifecycle.rollback(
                deployment,
                known_good_candidate=_known_good(),
                evidence_refs=("observation://failed",),
                expected_duration_seconds=60,
            )

    assert provider.rollback_attempts == 1
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    critical = [
        event
        for event in events
        if event.event_type is WorkEventType.CONTROL_DEGRADED
        and event.payload_json.get("failed_preconditions") == ["rollback-provider"]
    ]
    assert len(critical) == 1
    assert critical[0].payload_json["severity"] == "critical"
    assert critical[0].payload_json["deployment_id"] == deployment.id
    assert critical[0].payload_json["evidence_refs"] == ["observation://failed"]
    assert "RuntimeError: provider rejected rollback" in critical[0].payload_json["details"]
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert len(pending) == 1
    assert pending[0].kind.value == "EXTERNAL_OUTCOME_INCIDENT"
    assert pending[0].severity == "critical"


@pytest.mark.asyncio
async def test_rollback_heartbeat_error_is_not_recorded_as_provider_failure(
    store: WorkStore,
) -> None:
    class BrokenHeartbeatProbe:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate(self, request, preconditions):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("control probe crashed")
            return tuple(_result(precondition.id) for precondition in preconditions)

    class BlockingRollbackProvider(DeterministicFakeDeploymentProvider):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled = False

        async def rollback(self, deployment, known_good_candidate):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    provider = BlockingRollbackProvider()
    lifecycle, _, _, _, _ = _lifecycle(
        store,
        control_probe=BrokenHeartbeatProbe(),
        deployment_provider=provider,
        heartbeat_interval=0.01,
    )
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

    with pytest.raises(RuntimeError, match="control probe crashed"):
        await lifecycle.rollback(
            deployment,
            known_good_candidate=_known_good(),
            evidence_refs=("observation://failed",),
            expected_duration_seconds=60,
        )

    assert provider.cancelled is True
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    assert not any(
        event.event_type is WorkEventType.CONTROL_DEGRADED
        and "rollback-provider"
        in event.payload_json.get("failed_preconditions", ())
        for event in events
    )


@pytest.mark.asyncio
async def test_rollback_refusal_records_one_critical_receipt_for_active_precondition(
    store: WorkStore,
) -> None:
    probe = DeterministicFakeControlProbe(
        {
            "rollback": (
                _result("rollback-authority", passed=False),
                _result("delivery-observability"),
                _result("rollback-artifact"),
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
    await _record_degradation(store, "rollback-authority")

    for _attempt in range(2):
        with pytest.raises(ControlDegradedError, match="rollback-authority"):
            await lifecycle.rollback(
                deployment,
                known_good_candidate=_known_good(),
                evidence_refs=(),
                expected_duration_seconds=60,
            )

    assert provider.rollbacks == []
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    critical = [
        event
        for event in events
        if event.event_type is WorkEventType.CONTROL_DEGRADED
        and event.payload_json.get("severity") == "critical"
    ]
    assert len(critical) == 1
    assert critical[0].payload_json["action"] == "rollback"
    assert critical[0].payload_json["deployment_id"] == deployment.id
    assert critical[0].payload_json["failed_preconditions"] == ["rollback-authority"]

    expanded_probe = DeterministicFakeControlProbe(
        {
            "rollback": (
                _result("rollback-authority", passed=False),
                _result("delivery-observability"),
                _result("rollback-artifact", passed=False),
            )
        }
    )
    expanded, _, expanded_provider, _, _ = _lifecycle(
        store,
        control_probe=expanded_probe,
    )
    for _attempt in range(2):
        with pytest.raises(ControlDegradedError):
            await expanded.rollback(
                deployment,
                known_good_candidate=_known_good(),
                evidence_refs=(),
                expected_duration_seconds=60,
            )

    assert expanded_provider.rollbacks == []
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    critical = [
        event
        for event in events
        if event.event_type is WorkEventType.CONTROL_DEGRADED
        and event.payload_json.get("severity") == "critical"
    ]
    assert len(critical) == 2
    assert critical[1].payload_json["failed_preconditions"] == ["rollback-artifact"]
    assert critical[1].payload_json["evidence_refs"] == ["check://rollback-artifact"]

    await store.append_event(
        WorkEvent(
            id="restore-rollback-controls",
            project_id=PROJECT_ID,
            work_id=WORK_ID,
            sequence=events[-1].sequence + 1,
            event_type=WorkEventType.CONTROL_RESTORED,
            actor_type="test",
            actor_ref=None,
            payload_json={
                "precondition_ids": ["rollback-authority", "rollback-artifact"],
                "evidence_refs": ["check://restored"],
            },
            created_at=NOW,
        )
    )
    restored_probe = DeterministicFakeControlProbe(
        {
            "rollback": (
                _result("rollback-authority", passed=False),
                _result("delivery-observability"),
                _result("rollback-artifact"),
            )
        }
    )
    restored, _, _, _, _ = _lifecycle(store, control_probe=restored_probe)
    with pytest.raises(ControlDegradedError, match="rollback-authority"):
        await restored.rollback(
            deployment,
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    critical = [
        event
        for event in events
        if event.event_type is WorkEventType.CONTROL_DEGRADED
        and event.payload_json.get("severity") == "critical"
    ]
    assert len(critical) == 3
    assert critical[2].payload_json["failed_preconditions"] == ["rollback-authority"]


@pytest.mark.asyncio
async def test_staging_rollback_refusal_records_one_high_degradation(
    store: WorkStore,
) -> None:
    probe = DeterministicFakeControlProbe(
        {
            "rollback": (
                _result("rollback-authority", passed=False),
                _result("delivery-observability"),
                _result("rollback-artifact"),
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
        id="staging-1",
        project_id=PROJECT_ID,
        work_id=WORK_ID,
        release_candidate_id="candidate-1",
        environment="staging",
        exposure=BlastRadius(dimension="instances", value="1"),
        provider_ref="fake://deployment/staging-1",
        status="active",
    )
    await _record_deployment(store, deployment)

    for _attempt in range(2):
        with pytest.raises(ControlDegradedError, match="rollback-authority"):
            await lifecycle.rollback(
                deployment,
                known_good_candidate=_known_good(),
                evidence_refs=(),
                expected_duration_seconds=60,
            )

    assert provider.rollbacks == []
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    degraded = [
        event for event in events if event.event_type is WorkEventType.CONTROL_DEGRADED
    ]
    assert len(degraded) == 1
    assert degraded[0].payload_json["severity"] == "high"
    assert degraded[0].payload_json["failed_preconditions"] == ["rollback-authority"]


@pytest.mark.asyncio
async def test_unrecorded_known_good_candidate_degrades_reversibility(
    store: WorkStore,
) -> None:
    lifecycle, _, provider, _, _ = _lifecycle(store)
    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )
    deployment = await lifecycle.deploy(
        candidate,
        environment="production",
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    unrecorded = _known_good().model_copy(update={"id": "unrecorded"})

    with pytest.raises(ControlDegradedError, match="rollback-artifact"):
        await lifecycle.rollback(
            deployment,
            known_good_candidate=unrecorded,
            evidence_refs=(),
            expected_duration_seconds=60,
        )

    assert provider.rollbacks == []
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert len(pending) == 1
    assert pending[0].kind.value == "EXTERNAL_OUTCOME_INCIDENT"
    assert pending[0].evidence_refs == ("check://rollback-artifact",)
    assert pending[0].summary.startswith("CRITICAL:")


@pytest.mark.asyncio
async def test_unrelated_degradation_freezes_promotion_but_not_observe_or_rollback(
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
        observations=({"availability": True}, {"availability": True}),
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
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    await lifecycle.observe(deployment, gates=(gate,), window_seconds=30)
    await _record_degradation(store, "unrelated-authority")

    assert (
        await lifecycle.observe(deployment, gates=(gate,), window_seconds=30)
    ).verdict is HealthVerdict.PASS
    with pytest.raises(ControlDegradedError, match="unrelated-authority"):
        await lifecycle.promote(
            deployment,
            exposure=BlastRadius(dimension="traffic", value="20%"),
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

    assert rolled_back.status == "rolled_back"
    assert provider.promotions == []
    assert provider.rollbacks == [deployment]


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
            risk="high",
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
            exposure=BlastRadius(dimension="traffic", value="5%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )
    assert provider.deployments == []
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    assert events[-1].event_type is WorkEventType.CONTROL_DEGRADED
    assert events[-1].payload_json["severity"] == "high"
    assert events[-1].payload_json["action"] == "deploy"

    approval_policy = RecordingPolicy(GateDecision.REQUIRE_APPROVAL)
    lifecycle, _, provider, _, _ = _lifecycle(store, policy=approval_policy)
    with pytest.raises(DeliveryApprovalRequiredError, match="deploy_production"):
        await lifecycle.deploy(
            _candidate(),
            environment="production",
            risk="high",
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
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

    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None
    assert record.pending_gate is not None
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    await store.append_event(
        WorkEvent(
            id="operator-approved-delivery",
            project_id=PROJECT_ID,
            work_id=WORK_ID,
            sequence=events[-1].sequence + 1,
            event_type=WorkEventType.GATE_DECIDED,
            actor_type="human",
            actor_ref="operator",
            payload_json={
                "gate_id": record.pending_gate,
                "decision": GateDecision.ALLOW.value,
                "action": events[-1].payload_json["action"],
            },
            created_at=NOW,
        )
    )

    deployment = await lifecycle.deploy(
        _candidate(),
        environment="production",
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )

    assert provider.deployments == [deployment]
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    assert sum(event.event_type is WorkEventType.GATE_REQUESTED for event in events) == 1
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None
    assert record.pending_gate is None
    assert await store.pending_attention(project_id=PROJECT_ID) == ()


@pytest.mark.asyncio
async def test_delivery_approval_is_bound_to_release_candidate(
    store: WorkStore,
) -> None:
    lifecycle, _, provider, _, _ = _lifecycle(store)
    candidate_one = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )
    approval_policy = RecordingPolicy(GateDecision.REQUIRE_APPROVAL)
    gated_lifecycle, _, _, _, _ = _lifecycle(
        store,
        deployment_provider=provider,
        policy=approval_policy,
    )
    exposure = BlastRadius(dimension="traffic", value="5%")
    with pytest.raises(DeliveryApprovalRequiredError):
        await gated_lifecycle.deploy(
            candidate_one,
            environment="production",
            risk="high",
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
            exposure=exposure,
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )

    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None
    assert record.pending_gate is not None
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    requested = events[-1]
    await store.append_event(
        WorkEvent(
            id="approve-candidate-one",
            project_id=PROJECT_ID,
            work_id=WORK_ID,
            sequence=requested.sequence + 1,
            event_type=WorkEventType.GATE_DECIDED,
            actor_type="human",
            actor_ref="operator",
            payload_json={
                "gate_id": record.pending_gate,
                "decision": GateDecision.ALLOW.value,
                "action": requested.payload_json["action"],
            },
            created_at=NOW,
        )
    )
    first_deployment = await gated_lifecycle.deploy(
        candidate_one,
        environment="production",
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=exposure,
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )

    candidate_two = _candidate(
        candidate_id="candidate-2",
        artifact_ref="artifact://candidate-2",
        digest="d" * 64,
        commit_sha="e" * 40,
    )
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    await store.append_event(
        WorkEvent(
            id="release-candidate-two",
            project_id=PROJECT_ID,
            work_id=WORK_ID,
            sequence=events[-1].sequence + 1,
            event_type=WorkEventType.RELEASE_CREATED,
            actor_type="test",
            actor_ref="release-provider",
            payload_json={"release_candidate": candidate_two.model_dump(mode="json")},
            created_at=NOW,
        )
    )

    with pytest.raises(DeliveryApprovalRequiredError):
        await gated_lifecycle.deploy(
            candidate_two,
            environment="production",
            risk="high",
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
            exposure=exposure,
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )

    assert provider.deployments == [first_deployment]
    assert [request.action for request in approval_policy.requests] == [
        "deploy_production",
        "deploy_production",
    ]
    assert candidate_one.id in approval_policy.requests[0].scope
    assert candidate_two.id in approval_policy.requests[1].scope


@pytest.mark.asyncio
async def test_completed_release_and_exact_delivery_actions_resume_without_rerun(
    store: WorkStore,
) -> None:
    lifecycle, release, provider, _, _ = _lifecycle(
        store,
        observations=({"availability": True},),
    )
    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )
    assert (
        await lifecycle.build(
            work_id=WORK_ID,
            project_id=PROJECT_ID,
            commit_sha=COMMIT_SHA,
            evidence_refs=(),
        )
        == candidate
    )
    exposure = BlastRadius(dimension="traffic", value="5%")
    deployment = await lifecycle.deploy(
        candidate,
        environment="production",
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=exposure,
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    assert (
        await lifecycle.deploy(
            candidate,
            environment="production",
            risk="high",
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
            exposure=exposure,
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )
        == deployment
    )

    assert release.builds == [COMMIT_SHA]
    assert provider.deployments == [deployment]


@pytest.mark.asyncio
async def test_failure_triage_and_verified_rollout_completion_are_persisted(
    store: WorkStore,
) -> None:
    gate = HealthGate(
        id="availability",
        project_id=PROJECT_ID,
        description="availability",
        check_ref="http://availability",
        failure_verdict=HealthVerdict.FAIL,
    )
    lifecycle, _, _, _, _ = _lifecycle(
        store,
        observations=(
            {"availability": False},
            {"availability": True},
            {"availability": True},
            {"availability": True},
        ),
    )
    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )
    failed_deployment = await lifecycle.deploy(
        candidate,
        environment="production",
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    failed = await lifecycle.observe(
        failed_deployment,
        gates=(gate,),
        window_seconds=30,
    )
    with pytest.raises(DeliveryActionDeniedError, match="recorded rollback"):
        await lifecycle.triage(
            failed_deployment,
            observation=failed,
            summary="Canary availability failed.",
            evidence_refs=("observation://failed",),
        )
    rolled_back = await lifecycle.rollback(
        failed_deployment,
        known_good_candidate=_known_good(),
        evidence_refs=("observation://failed",),
        expected_duration_seconds=60,
    )
    await lifecycle.observe(rolled_back, gates=(gate,), window_seconds=30)
    assert (
        await lifecycle.build(
            work_id=WORK_ID,
            project_id=PROJECT_ID,
            commit_sha=COMMIT_SHA,
            evidence_refs=(),
        )
        == candidate
    )
    with pytest.raises(DeliveryActionDeniedError, match="rolled-back candidate"):
        await lifecycle.deploy(
            candidate,
            environment="production",
            risk="high",
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
            exposure=BlastRadius(dimension="traffic", value="5%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )
    with pytest.raises(DeliveryActionDeniedError, match="rolled-back deployment"):
        await lifecycle.promote(
            failed_deployment,
            exposure=BlastRadius(dimension="traffic", value="20%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )
    triaged = await lifecycle.triage(
        failed_deployment,
        observation=failed,
        summary="Canary availability failed.",
        evidence_refs=("observation://failed",),
    )
    assert triaged.status == "TRIAGING"
    resumed_triage = await lifecycle.triage(
        failed_deployment,
        observation=failed,
        summary="Canary availability failed.",
        evidence_refs=("observation://failed",),
    )
    assert resumed_triage.status == "TRIAGING"

    repaired = _candidate(
        candidate_id="candidate-2",
        artifact_ref="artifact://candidate-2",
        digest="d" * 64,
        commit_sha="e" * 40,
    )
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    await store.append_event(
        WorkEvent(
            id="release-candidate-two-for-completion",
            project_id=PROJECT_ID,
            work_id=WORK_ID,
            sequence=events[-1].sequence + 1,
            event_type=WorkEventType.RELEASE_CREATED,
            actor_type="test",
            actor_ref="release-provider",
            payload_json={"release_candidate": repaired.model_dump(mode="json")},
            created_at=NOW,
        )
    )
    rollout = await lifecycle.deploy(
        repaired,
        environment="production",
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="100%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    passed = await lifecycle.observe(rollout, gates=(gate,), window_seconds=30)
    completed = await lifecycle.complete(
        rollout,
        required_exposure=BlastRadius(dimension="traffic", value="100%"),
        observation=passed,
        evidence_refs=("configured://docs",),
    )

    assert completed.status == "COMPLETE"
    resumed_completion = await lifecycle.complete(
        rollout,
        required_exposure=BlastRadius(dimension="traffic", value="100%"),
        observation=passed,
        evidence_refs=("configured://docs",),
    )
    assert resumed_completion.status == "COMPLETE"
    await lifecycle.observe(rollout, gates=(gate,), window_seconds=30)
    still_complete = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert still_complete is not None and still_complete.status == "COMPLETE"
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    assert sum(event.event_type is WorkEventType.TRIAGE_CREATED for event in events) == 1
    assert sum(event.event_type is WorkEventType.WORK_COMPLETED for event in events) == 1
    assert events[-2].event_type is WorkEventType.WORK_COMPLETED
    assert events[-1].event_type is WorkEventType.OBSERVATION_RECORDED


@pytest.mark.asyncio
async def test_in_flight_control_loss_cancels_provider_and_freezes_work(
    store: WorkStore,
) -> None:
    class LosingProbe:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate(self, request, preconditions):
            self.calls += 1
            return tuple(
                _result(
                    precondition.id,
                    passed=self.calls == 1 or precondition.id != "delivery-observability",
                    detail="monitoring dark" if self.calls > 1 else None,
                )
                for precondition in preconditions
            )

    class BlockingProvider(DeterministicFakeDeploymentProvider):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled = False

        async def deploy(self, candidate, environment, exposure, known_good_candidate):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    provider = BlockingProvider()
    lifecycle, _, _, _, _ = _lifecycle(
        store,
        control_probe=LosingProbe(),
        deployment_provider=provider,
        heartbeat_interval=0.01,
    )
    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )

    with pytest.raises(ControlDegradedError, match="delivery-observability"):
        await lifecycle.deploy(
            candidate,
            environment="production",
            risk="high",
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
            exposure=BlastRadius(dimension="traffic", value="5%"),
            known_good_candidate=_known_good(),
            evidence_refs=(),
            expected_duration_seconds=60,
        )

    assert provider.cancelled is True
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert [item.attention_id for item in pending] == ["delivery-observability"]


@pytest.mark.asyncio
async def test_credential_expiry_mid_deploy_restores_and_resumes_same_candidate(
    store: WorkStore,
) -> None:
    class ExpiringAuthorityProbe:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate(self, request, preconditions):
            self.calls += 1
            return tuple(
                _result(
                    precondition.id,
                    passed=self.calls == 1 or precondition.id != "delivery-authority",
                    detail="credential expired" if self.calls > 1 else None,
                )
                for precondition in preconditions
            )

    class ResumableProvider(DeterministicFakeDeploymentProvider):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0
            self.cancelled = False

        async def deploy(self, candidate, environment, exposure, known_good_candidate):
            self.attempts += 1
            if self.attempts == 1:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            return await super().deploy(
                candidate,
                environment,
                exposure,
                known_good_candidate,
            )

    provider = ResumableProvider()
    lifecycle, release, _, _, _ = _lifecycle(
        store,
        control_probe=ExpiringAuthorityProbe(),
        deployment_provider=provider,
        heartbeat_interval=0.01,
    )
    candidate = await lifecycle.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )

    request = dict(
        environment="production",
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=("policy://docs",),
        expected_duration_seconds=60,
    )
    with pytest.raises(ControlDegradedError, match="delivery-authority"):
        await lifecycle.deploy(candidate, **request)

    assert provider.cancelled is True
    assert provider.deployments == []
    restored, resumed_release, _, _, _ = _lifecycle(
        store,
        control_probe=_passing_probe(),
        deployment_provider=provider,
        heartbeat_interval=0.01,
    )
    resumed_candidate = await restored.build(
        work_id=WORK_ID,
        project_id=PROJECT_ID,
        commit_sha=COMMIT_SHA,
        evidence_refs=(),
    )
    deployment = await restored.deploy(resumed_candidate, **request)

    assert resumed_candidate == candidate
    assert release.builds == [COMMIT_SHA]
    assert resumed_release.builds == []
    assert provider.attempts == 2
    assert provider.deployments == [deployment]
    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    assert [
        event.event_type
        for event in events
        if event.event_type
        in {WorkEventType.CONTROL_DEGRADED, WorkEventType.CONTROL_RESTORED}
    ] == [WorkEventType.CONTROL_DEGRADED, WorkEventType.CONTROL_RESTORED]


@pytest.mark.asyncio
async def test_monitoring_darkness_during_observation_cancels_window_and_freezes_work(
    store: WorkStore,
) -> None:
    class LosingProbe:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate(self, request, preconditions):
            self.calls += 1
            return tuple(
                _result(
                    precondition.id,
                    passed=self.calls <= 2,
                    detail="monitoring dark" if self.calls > 2 else None,
                )
                for precondition in preconditions
            )

    class BlockingObservationProvider(DeterministicFakeObservationProvider):
        def __init__(self) -> None:
            super().__init__(({"availability": True},))
            self.cancelled = False

        async def observe(self, deployment, gates, window_seconds):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    observation = BlockingObservationProvider()
    lifecycle, _, _, _, _ = _lifecycle(
        store,
        control_probe=LosingProbe(),
        observation_provider=observation,
        heartbeat_interval=0.01,
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
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    gate = HealthGate(
        id="availability",
        project_id=PROJECT_ID,
        description="availability",
        check_ref="http://availability",
        failure_verdict=HealthVerdict.FAIL,
    )

    with pytest.raises(ControlDegradedError, match="delivery-observability"):
        await lifecycle.observe(deployment, gates=(gate,), window_seconds=60)

    assert observation.cancelled is True
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert [item.attention_id for item in pending] == ["delivery-observability"]


@pytest.mark.asyncio
async def test_observation_provider_control_loss_freezes_without_health_or_rollback(
    store: WorkStore,
) -> None:
    class UnreachableObservationProvider(DeterministicFakeObservationProvider):
        async def observe(self, deployment, gates, window_seconds):
            raise DeliveryControlLostError(
                "delivery-observability",
                "monitoring transport unavailable",
                evidence_refs=("monitoring://unreachable",),
            )

    lifecycle, _, provider, _, _ = _lifecycle(
        store,
        observation_provider=UnreachableObservationProvider(({"availability": True},)),
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
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        exposure=BlastRadius(dimension="traffic", value="5%"),
        known_good_candidate=_known_good(),
        evidence_refs=(),
        expected_duration_seconds=60,
    )
    gate = HealthGate(
        id="availability",
        project_id=PROJECT_ID,
        description="availability",
        check_ref="http://availability",
        failure_verdict=HealthVerdict.FAIL,
    )

    with pytest.raises(ControlDegradedError, match="delivery-observability"):
        await lifecycle.observe(deployment, gates=(gate,), window_seconds=30)

    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    assert WorkEventType.CONTROL_DEGRADED in [event.event_type for event in events]
    assert WorkEventType.OBSERVATION_RECORDED not in [event.event_type for event in events]
    assert provider.rollbacks == []


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
        risk="high",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
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
            risk="high",
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
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


@pytest.mark.asyncio
async def test_critical_irreversible_action_is_denied_without_side_effect(
    store: WorkStore,
) -> None:
    lifecycle, release, provider, observation, _ = _lifecycle(
        store,
        policy=default_delivery_action_policy,
    )
    candidate = _candidate()
    await store.append_event(
        WorkEvent(
            id="release-candidate",
            project_id=PROJECT_ID,
            work_id=WORK_ID,
            sequence=1,
            event_type=WorkEventType.RELEASE_CREATED,
            actor_type="test",
            actor_ref=None,
            payload_json={"release_candidate": candidate.model_dump(mode="json")},
            created_at=NOW,
        )
    )
    exposure = BlastRadius(dimension="traffic", value="5%")
    with pytest.raises(DeliveryApprovalRequiredError, match="deploy_production"):
        await lifecycle.deploy(
            candidate,
            environment="production",
            exposure=exposure,
            known_good_candidate=_known_good(),
            evidence_refs=("policy://reversible",),
            expected_duration_seconds=60,
            risk="high",
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        )
    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None and record.pending_gate is not None
    await lifecycle.approve(
        WORK_ID,
        project_id=PROJECT_ID,
        gate_id=record.pending_gate,
        actor_ref="operator:arda",
    )
    assert provider.deployments == []

    request = ActionRequest(
        project_id=PROJECT_ID,
        action="deploy_production",
        work_id=WORK_ID,
        risk="critical",
        reversibility=Reversibility.IRREVERSIBLE,
        scope=f"{candidate.id}:production:traffic:5%",
        evidence_refs=("policy://request-claims-approval",),
    )

    with pytest.raises(DeliveryActionDeniedError, match="deploy_production"):
        await lifecycle.deploy(
            candidate,
            environment="production",
            exposure=BlastRadius(dimension="traffic", value="5%"),
            known_good_candidate=_known_good(),
            evidence_refs=request.evidence_refs,
            expected_duration_seconds=60,
            risk=request.risk,
            reversibility=request.reversibility,
        )

    events = await store.read_events(WORK_ID, project_id=PROJECT_ID)
    control_events = [
        event.event_type
        for event in events
        if event.event_type
        in {
            WorkEventType.GATE_REQUESTED,
            WorkEventType.GATE_DECIDED,
            WorkEventType.WORK_BLOCKED,
        }
    ]
    assert control_events[-2:] == [
        WorkEventType.GATE_DECIDED,
        WorkEventType.WORK_BLOCKED,
    ]
    assert control_events.count(WorkEventType.GATE_REQUESTED) == 1
    denied = next(
        event
        for event in reversed(events)
        if event.event_type is WorkEventType.GATE_DECIDED
    )
    assert denied.payload_json["decision"] == GateDecision.DENY.value
    assert denied.payload_json["action"] == request.model_dump(mode="json")

    record = await store.load_work(WORK_ID, project_id=PROJECT_ID)
    assert record is not None
    assert record.status == "WORK_BLOCKED"
    assert record.pending_gate is None
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert [item.kind.value for item in pending] == ["WORK_BLOCKED"]

    gate_id = f"{request.action}:{request.work_id}:{request.scope}"
    with pytest.raises(DeliveryActionDeniedError, match="cannot resume"):
        await lifecycle.approve(
            WORK_ID,
            project_id=PROJECT_ID,
            gate_id=gate_id,
            actor_ref="operator:arda",
        )

    assert release.builds == []
    assert provider.deployments == []
    assert provider.promotions == []
    assert provider.rollbacks == []
    assert observation.calls == []


def test_control_request_is_immutable() -> None:
    request = DeliveryControlRequest(
        project_id=PROJECT_ID,
        work_id=WORK_ID,
        action="observe",
        candidate=_candidate(),
        known_good_candidate=None,
        expected_duration_seconds=60,
    )

    with pytest.raises(ValidationError):
        request.action = "deploy"  # type: ignore[misc]
