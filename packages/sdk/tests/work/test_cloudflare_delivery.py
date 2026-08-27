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

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from sagewai.work.profiles.software.cloudflare import (
    CloudflareDeliveryConfig,
    CloudflareDeliveryControlProbe,
)
from sagewai.work.profiles.software.delivery import (
    DeliveryControlRequest,
    ReleaseCandidate,
)

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
PROJECT_ID = "project-a"
WORK_ID = "work-1"
DIGEST = "b" * 64
VERSION_ID = "11111111-1111-1111-1111-111111111111"


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


def _config(*, max_staleness: int = 120) -> CloudflareDeliveryConfig:
    return CloudflareDeliveryConfig(
        project_id=PROJECT_ID,
        account_id="account-1",
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
        deployment=None,
        known_good_candidate=_candidate() if known_good else None,
        expected_duration_seconds=600,
    )


def _response(request: httpx.Request, *, observed_at: datetime = NOW) -> httpx.Response:
    assert request.method == "GET"
    if request.url.path == "/client/v4/user/tokens/verify":
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
        return httpx.Response(
            200,
            request=request,
            headers={"date": format_datetime(observed_at, usegmt=True)},
        )
    if request.url.path.endswith(f"/versions/{VERSION_ID}"):
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


@pytest.mark.asyncio
async def test_real_path_preflight_checks_authority_observability_and_rollback() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_response)) as client:
        probe = CloudflareDeliveryControlProbe(
            config=_config(),
            deployment_token="deploy-token",
            rollback_token="rollback-token",
            client=client,
            now=lambda: NOW,
        )

        results = await probe.evaluate(_request("deploy"))

    assert [result.precondition_id for result in results] == [
        "cloudflare-deploy-authority",
        "cloudflare-observability",
        "cloudflare-rollback-authority",
        "cloudflare-rollback-artifact",
    ]
    assert all(result.passed for result in results)


@pytest.mark.asyncio
async def test_stale_monitoring_fails_even_when_endpoint_returns_200() -> None:
    def stale_response(request: httpx.Request) -> httpx.Response:
        return _response(request, observed_at=NOW - timedelta(minutes=10))

    async with httpx.AsyncClient(transport=httpx.MockTransport(stale_response)) as client:
        probe = CloudflareDeliveryControlProbe(
            config=_config(max_staleness=60),
            deployment_token="deploy-token",
            rollback_token="rollback-token",
            client=client,
            now=lambda: NOW,
        )

        results = await probe.evaluate(_request("observe"))

    assert len(results) == 1
    assert results[0].precondition_id == "cloudflare-observability"
    assert results[0].passed is False
    assert "stale" in (results[0].detail or "")


@pytest.mark.asyncio
async def test_missing_known_good_artifact_ref_refuses_reversibility() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_response)) as client:
        probe = CloudflareDeliveryControlProbe(
            config=_config(),
            deployment_token="deploy-token",
            rollback_token="rollback-token",
            client=client,
            now=lambda: NOW,
        )

        results = await probe.evaluate(_request("rollback", known_good=False))

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
            and request.headers["authorization"] == "Bearer rollback-token"
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
            deployment_token="deploy-token",
            rollback_token="rollback-token",
            client=client,
            now=lambda: NOW,
        )

        results = await probe.evaluate(_request("rollback"))

    authority = next(
        result for result in results if result.precondition_id == "cloudflare-rollback-authority"
    )
    assert authority.passed is False
    assert "expired" in (authority.detail or "")


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
                        "resources": {"script": {"etag": "c" * 64}},
                    },
                },
            )
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(mismatched_digest)) as client:
        probe = CloudflareDeliveryControlProbe(
            config=_config(),
            deployment_token="deploy-token",
            rollback_token="rollback-token",
            client=client,
            now=lambda: NOW,
        )

        results = await probe.evaluate(_request("rollback"))

    artifact = next(
        result for result in results if result.precondition_id == "cloudflare-rollback-artifact"
    )
    assert artifact.passed is False
    assert artifact.detail == "known-good rollback artifact digest does not match"
