# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Read-only control probes for Sagewai's Cloudflare Workers docs path."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field

from sagewai.work.profiles.software.delivery import (
    DeliveryControlRequest,
    DeliveryPreconditionResult,
    ReleaseCandidate,
)


class CloudflareDeliveryConfig(BaseModel):
    """Project-scoped read-only probe configuration for the docs Worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    account_id: str
    script_name: str
    target_url: str
    minimum_credential_ttl_seconds: int = Field(gt=0)
    maximum_monitoring_staleness_seconds: int = Field(gt=0)
    api_base: str = "https://api.cloudflare.com/client/v4"


class CloudflareDeliveryControlProbe:
    """Verify credentials, fresh read-back, and known-good Worker version."""

    def __init__(
        self,
        *,
        config: CloudflareDeliveryConfig,
        deployment_token: str,
        rollback_token: str,
        client: httpx.AsyncClient,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._deployment_token = deployment_token
        self._rollback_token = rollback_token
        self._client = client
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def evaluate(
        self,
        request: DeliveryControlRequest,
    ) -> tuple[DeliveryPreconditionResult, ...]:
        if request.project_id != self._config.project_id:
            raise ValueError("Cloudflare probe belongs to a different project")
        if request.action == "observe":
            return (await self._observability(request),)
        if request.action == "rollback":
            return (
                await self._authority(
                    request,
                    token=self._rollback_token,
                    precondition_id="cloudflare-rollback-authority",
                ),
                await self._observability(request),
                await self._rollback_artifact(request),
            )
        return (
            await self._authority(
                request,
                token=self._deployment_token,
                precondition_id="cloudflare-deploy-authority",
            ),
            await self._observability(request),
            await self._authority(
                request,
                token=self._rollback_token,
                precondition_id="cloudflare-rollback-authority",
            ),
            await self._rollback_artifact(request),
        )

    async def _authority(
        self,
        request: DeliveryControlRequest,
        *,
        token: str,
        precondition_id: str,
    ) -> DeliveryPreconditionResult:
        checked_at = self._now()
        required_ttl = max(
            request.expected_duration_seconds,
            self._config.minimum_credential_ttl_seconds,
        )
        try:
            response = await self._client.get(
                f"{self._config.api_base}/user/tokens/verify",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            payload = response.json()
            result = payload["result"]
            status = str(result["status"])
            expires_on = _parse_datetime(result.get("expires_on"))
            not_before = _parse_datetime(result.get("not_before"))
            if payload.get("success") is not True:
                detail = "Cloudflare credential verification failed"
            elif status != "active":
                detail = f"Cloudflare credential is {status}"
            elif expires_on is None:
                detail = "Cloudflare credential expiry is unavailable"
            elif expires_on < checked_at:
                detail = "Cloudflare credential is expired"
            elif (expires_on - checked_at).total_seconds() < required_ttl:
                detail = "Cloudflare credential TTL is insufficient"
            elif not_before is not None and not_before > checked_at:
                detail = "Cloudflare credential is not active yet"
            else:
                detail = None
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            detail = f"Cloudflare credential verification unavailable: {exc}"
        return DeliveryPreconditionResult(
            project_id=request.project_id,
            precondition_id=precondition_id,
            passed=detail is None,
            evidence_refs=(f"cloudflare://credential/{precondition_id}",),
            detail=detail,
            checked_at=checked_at,
        )

    async def _observability(
        self,
        request: DeliveryControlRequest,
    ) -> DeliveryPreconditionResult:
        checked_at = self._now()
        try:
            response = await self._client.get(self._config.target_url)
            response.raise_for_status()
            observed_at = parsedate_to_datetime(response.headers["date"])
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=timezone.utc)
            age = (checked_at - observed_at).total_seconds()
            if age < 0 or age > self._config.maximum_monitoring_staleness_seconds:
                detail = "Cloudflare monitoring timestamp is stale"
            else:
                detail = None
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            detail = f"Cloudflare observability unavailable: {exc}"
        return DeliveryPreconditionResult(
            project_id=request.project_id,
            precondition_id="cloudflare-observability",
            passed=detail is None,
            evidence_refs=(self._config.target_url,),
            detail=detail,
            checked_at=checked_at,
        )

    async def _rollback_artifact(
        self,
        request: DeliveryControlRequest,
    ) -> DeliveryPreconditionResult:
        checked_at = self._now()
        candidate = request.known_good_candidate
        detail: str | None
        if candidate is None:
            detail = "known-good rollback artifact is missing"
            evidence_refs: tuple[str, ...] = ()
        elif not candidate.artifact_ref.startswith("cloudflare-version://"):
            detail = "known-good rollback artifact is not a Cloudflare version"
            evidence_refs = (candidate.artifact_ref,)
        else:
            evidence_refs = (candidate.artifact_ref,)
            detail = await self._verify_version(candidate)
        return DeliveryPreconditionResult(
            project_id=request.project_id,
            precondition_id="cloudflare-rollback-artifact",
            passed=detail is None,
            evidence_refs=evidence_refs,
            detail=detail,
            checked_at=checked_at,
        )

    async def _verify_version(self, candidate: ReleaseCandidate) -> str | None:
        version_id = candidate.artifact_ref.removeprefix("cloudflare-version://")
        if not version_id:
            return "known-good rollback artifact is missing"
        try:
            response = await self._client.get(
                (
                    f"{self._config.api_base}/accounts/{self._config.account_id}"
                    f"/workers/scripts/{self._config.script_name}/versions/{version_id}"
                ),
                headers={"Authorization": f"Bearer {self._rollback_token}"},
            )
            response.raise_for_status()
            payload = response.json()
            result = payload["result"]
            if payload.get("success") is not True or str(result["id"]) != version_id:
                return "known-good rollback artifact is missing"
            remote_digest = str(result["resources"]["script"]["etag"]).strip('"')
            if remote_digest != candidate.artifact_digest:
                return "known-good rollback artifact digest does not match"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return "known-good rollback artifact is missing"
            return f"Cloudflare rollback provider unavailable: {exc}"
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            return f"Cloudflare rollback provider unavailable: {exc}"
        return None


def _parse_datetime(value) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


__all__ = [
    "CloudflareDeliveryConfig",
    "CloudflareDeliveryControlProbe",
]
