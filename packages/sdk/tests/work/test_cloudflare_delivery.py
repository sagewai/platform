# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Read-only Cloudflare Workers control probes for Sagewai's docs path."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sagewai.work import (
    ControlDegradedError,
    GateDecision,
    WorkEvent,
    WorkEventType,
    WorkRecord,
    WorkStore,
)
from sagewai.work.profiles.software.cloudflare import (
    CloudflareDeliveryConfig,
    CloudflareDeliveryControlProbe,
    cloudflare_delivery_preconditions,
    cloudflare_version_digest,
)
from sagewai.work.profiles.software.delivery import (
    BlastRadius,
    DeliveryControlRequest,
    DeliveryLifecycle,
    ReleaseCandidate,
)
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.fakes_delivery import (
    DeterministicFakeDeploymentProvider,
    DeterministicFakeObservationProvider,
    DeterministicFakeReleaseProvider,
)

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
PROJECT_ID = "project-a"
WORK_ID = "work-1"
VERSION_ID = "11111111-1111-1111-1111-111111111111"
DIGEST = cloudflare_version_digest("account-1", "docs", VERSION_ID)


def _candidate() -> ReleaseCandidate:
    return ReleaseCandidate(
        id="known-good",
        project_id=PROJECT_ID,
        work_id=WORK_ID,
        commit_sha="a" * 40,
        artifact_ref=f"cloudflare-version://{VERSION_ID}",
        artifact_digest=DIGEST,
        config_revision="apps/docs/wrangler.toml@abc",
        verification_ref="verification://1",
        review_ref="review://1",
    )


def _current_candidate() -> ReleaseCandidate:
    return _candidate().model_copy(
        update={
            "id": "candidate-current",
            "work_id": WORK_ID,
            "artifact_ref": "artifact://candidate-current",
        }
    )


def _known_good() -> ReleaseCandidate:
    return _candidate().model_copy(update={"work_id": "work-previous"})


def _config(*, max_staleness: int = 120) -> CloudflareDeliveryConfig:
    return CloudflareDeliveryConfig(
        project_id=PROJECT_ID,
        account_id="account-1",
        zone_name="sagewai.ai",
        script_name="docs",
        target_url="https://docs.sagewai.ai",
        minimum_credential_ttl_seconds=1800,
        maximum_monitoring_staleness_seconds=max_staleness,
    )


def _request(action: str, *, known_good=True) -> DeliveryControlRequest:
    return DeliveryControlRequest(
        project_id=PROJECT_ID,
        work_id=WORK_ID,
        action=action,
        candidate=_candidate(),
        known_good_candidate=_candidate() if known_good else None,
        expected_duration_seconds=600,
    )


def _response(request: httpx.Request, *, observed_at: datetime = NOW) -> httpx.Response:
    if request.url.path == "/client/v4/user/tokens/verify":
        assert request.method == "GET"
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "result": {
                    "status": "active",
                    "expires_on": (NOW + timedelta(hours=2)).isoformat(),
                    "not_before": (NOW - timedelta(hours=1)).isoformat(),
                },
            },
        )
    if request.url.host == "docs.sagewai.ai":
        assert request.method == "GET"
        return httpx.Response(200, request=request)
    if request.url.path == "/client/v4/zones":
        assert request.method == "GET"
        assert request.url.params["name"] == "sagewai.ai"
        assert request.url.params["account.id"] == "account-1"
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "result": [{"id": "zone-1", "name": "sagewai.ai", "status": "active"}],
            },
        )
    if request.url.path == "/client/v4/graphql":
        assert request.method == "POST"
        body = json.loads(request.content)
        assert "httpRequestsAdaptiveGroups" in body["query"]
        assert "clientRequestHTTPHost" in body["query"]
        assert "datetimeMinute" in body["query"]
        assert " count " in body["query"]
        assert " requests " not in body["query"]
        assert body["variables"]["zoneTag"] == "zone-1"
        assert body["variables"]["host"] == "docs.sagewai.ai"
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "viewer": {
                        "zones": [
                            {
                                "metrics": [
                                    {
                                        "count": 1,
                                        "dimensions": {"datetimeMinute": observed_at.isoformat()},
                                    }
                                ]
                            }
                        ]
                    }
                },
                "errors": None,
            },
        )
    if request.url.path.endswith(f"/versions/{VERSION_ID}"):
        assert request.method == "GET"
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "result": {
                    "id": VERSION_ID,
                    "resources": {"script": {"etag": DIGEST}},
                },
            },
        )
    raise AssertionError(f"unexpected request: {request.url}")


async def _evaluate(
    probe: CloudflareDeliveryControlProbe,
    request: DeliveryControlRequest,
):
    preconditions = tuple(
        precondition
        for precondition in cloudflare_delivery_preconditions(PROJECT_ID)
        if request.action in precondition.required_for
    )
    return await probe.evaluate(request, preconditions)


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


@pytest.mark.asyncio
async def test_real_path_preflight_checks_authority_observability_and_rollback() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_response)) as client:
        probe = CloudflareDeliveryControlProbe(
            config=_config(),
            api_token="api-token",
            client=client,
            now=lambda: NOW,
        )

        results = await _evaluate(probe, _request("deploy"))

    assert [result.precondition_id for result in results] == [
        "cloudflare-authority",
        "cloudflare-observability",
        "cloudflare-rollback-artifact",
    ]
    assert all(result.passed for result in results)


@pytest.mark.asyncio
async def test_active_unbounded_token_has_sufficient_ttl() -> None:
    def unbounded_token(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/client/v4/user/tokens/verify":
            return httpx.Response(
                200,
                request=request,
                json={"success": True, "result": {"status": "active"}},
            )
        return _response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unbounded_token)) as client:
        probe = CloudflareDeliveryControlProbe(
            config=_config(),
            api_token="api-token",
            client=client,
            now=lambda: NOW,
        )

        results = await _evaluate(probe, _request("deploy"))

    authority = [result for result in results if result.precondition_id.endswith("authority")]
    assert authority
    assert all(result.passed for result in authority)


@pytest.mark.asyncio
async def test_stale_monitoring_fails_even_when_endpoint_returns_200() -> None:
    def stale_response(request: httpx.Request) -> httpx.Response:
        return _response(request, observed_at=NOW - timedelta(minutes=10))

    async with httpx.AsyncClient(transport=httpx.MockTransport(stale_response)) as client:
        probe = CloudflareDeliveryControlProbe(
            config=_config(max_staleness=60),
            api_token="api-token",
            client=client,
            now=lambda: NOW,
        )

        results = await _evaluate(probe, _request("observe"))

    assert len(results) == 1
    assert results[0].precondition_id == "cloudflare-observability"
    assert results[0].passed is False
    assert "stale" in (results[0].detail or "")


@pytest.mark.asyncio
async def test_empty_analytics_window_does_not_mean_control_was_lost() -> None:
    def empty_window(request: httpx.Request) -> httpx.Response:
        response = _response(request)
        if request.url.path == "/client/v4/graphql":
            return httpx.Response(
                200,
                request=request,
                json={
                    "data": {"viewer": {"zones": [{"metrics": []}]}},
                    "errors": None,
                },
            )
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(empty_window)) as client:
        probe = CloudflareDeliveryControlProbe(
            config=_config(),
            api_token="api-token",
            client=client,
            now=lambda: NOW,
        )

        results = await _evaluate(probe, _request("observe"))

    assert len(results) == 1
    assert results[0].passed is True
    assert results[0].detail is None


@pytest.mark.asyncio
async def test_missing_known_good_artifact_ref_refuses_reversibility() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_response)) as client:
        probe = CloudflareDeliveryControlProbe(
            config=_config(),
            api_token="api-token",
            client=client,
            now=lambda: NOW,
        )

        results = await _evaluate(probe, _request("rollback", known_good=False))

    artifact = next(
        result for result in results if result.precondition_id == "cloudflare-rollback-artifact"
    )
    assert artifact.passed is False
    assert artifact.detail == "known-good rollback artifact is missing"


@pytest.mark.asyncio
async def test_invalid_rollback_credential_fails_its_own_precondition() -> None:
    def expired_rollback(request: httpx.Request) -> httpx.Response:
        if (
            request.url.path == "/client/v4/user/tokens/verify"
            and request.headers["authorization"] == "Bearer api-token"
        ):
            return httpx.Response(
                200,
                request=request,
                json={
                    "success": True,
                    "result": {
                        "status": "expired",
                        "expires_on": (NOW - timedelta(minutes=1)).isoformat(),
                    },
                },
            )
        return _response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(expired_rollback)) as client:
        probe = CloudflareDeliveryControlProbe(
            config=_config(),
            api_token="api-token",
            client=client,
            now=lambda: NOW,
        )

        results = await _evaluate(probe, _request("rollback"))

    authority = next(
        result for result in results if result.precondition_id == "cloudflare-authority"
    )
    assert authority.passed is False
    assert "expired" in (authority.detail or "")


@pytest.mark.asyncio
async def test_lifecycle_refuses_rollback_when_cloudflare_credential_expires(
    store: WorkStore,
) -> None:
    verification_calls = 0

    def expires_before_rollback(request: httpx.Request) -> httpx.Response:
        nonlocal verification_calls
        if request.url.path == "/client/v4/user/tokens/verify":
            verification_calls += 1
            if verification_calls > 1:
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "success": True,
                        "result": {
                            "status": "expired",
                            "expires_on": (NOW - timedelta(minutes=1)).isoformat(),
                        },
                    },
                )
        return _response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(expires_before_rollback)) as client:
        provider = DeterministicFakeDeploymentProvider()
        lifecycle = DeliveryLifecycle(
            work_store=store,
            release_provider=DeterministicFakeReleaseProvider(_current_candidate()),
            deployment_provider=provider,
            observation_provider=DeterministicFakeObservationProvider(()),
            control_probe=CloudflareDeliveryControlProbe(
                config=_config(),
                api_token="api-token",
                client=client,
                now=lambda: NOW,
            ),
            control_preconditions=cloudflare_delivery_preconditions(PROJECT_ID),
            action_policy=lambda request: GateDecision.ALLOW,
        )
        candidate = await lifecycle.build(
            work_id=WORK_ID,
            project_id=PROJECT_ID,
            commit_sha=_current_candidate().commit_sha,
            evidence_refs=("merge://sha",),
        )
        deployment = await lifecycle.deploy(
            candidate,
            environment="production",
            exposure=BlastRadius(dimension="traffic", value="5%"),
            known_good_candidate=_known_good(),
            evidence_refs=("policy://production",),
            expected_duration_seconds=600,
        )

        with pytest.raises(ControlDegradedError, match="cloudflare-authority"):
            await lifecycle.rollback(
                deployment,
                known_good_candidate=_known_good(),
                evidence_refs=("observation://failed",),
                expected_duration_seconds=600,
            )

    assert provider.rollbacks == []
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert [item.attention_id for item in pending] == ["cloudflare-authority"]


@pytest.mark.asyncio
async def test_rollback_artifact_digest_must_match_remote_version() -> None:
    def mismatched_digest(request: httpx.Request) -> httpx.Response:
        response = _response(request)
        if request.url.path.endswith(f"/versions/{VERSION_ID}"):
            return httpx.Response(
                200,
                request=request,
                json={
                    "success": True,
                    "result": {
                        "id": VERSION_ID,
                        "resources": {},
                    },
                },
            )
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(mismatched_digest)) as client:
        probe = CloudflareDeliveryControlProbe(
            config=_config(),
            api_token="api-token",
            client=client,
            now=lambda: NOW,
        )

        request = _request("rollback").model_copy(
            update={
                "known_good_candidate": _candidate().model_copy(
                    update={"artifact_digest": "c" * 64}
                )
            }
        )
        results = await _evaluate(probe, request)

    artifact = next(
        result for result in results if result.precondition_id == "cloudflare-rollback-artifact"
    )
    assert artifact.passed is False
    assert artifact.detail == "known-good rollback artifact digest does not match"
