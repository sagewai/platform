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

import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from sagewai.work.control import ControlCheckResult
from sagewai.work.models import ControlPrecondition, ControlPreconditionKind
from sagewai.work.profiles.software.delivery import (
    BlastRadius,
    DeliveryControlRequest,
    Deployment,
    ReleaseCandidate,
)


def cloudflare_version_digest(
    account_id: str,
    script_name: str,
    version_id: str,
) -> str:
    """Digest the identity of one immutable, complete Worker version."""

    identity = f"cloudflare-worker-version:{account_id}:{script_name}:{version_id}"
    return hashlib.sha256(identity.encode()).hexdigest()


class CloudflareDeliveryConfig(BaseModel):
    """Project-scoped read-only probe configuration for the docs Worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    account_id: str
    zone_name: str
    script_name: str
    target_url: str
    minimum_credential_ttl_seconds: int = Field(gt=0)
    credential_ttl_safety_factor: float = Field(default=2.0, ge=1)
    maximum_monitoring_staleness_seconds: int = Field(gt=0)
    api_base: str = "https://api.cloudflare.com/client/v4"


def cloudflare_delivery_preconditions(
    project_id: str,
) -> tuple[ControlPrecondition, ...]:
    """Declare the controls required by each docs delivery action."""

    controlled_actions = ("deploy", "promote", "rollback")
    observed_actions = (*controlled_actions, "observe")
    return (
        ControlPrecondition(
            id="cloudflare-authority",
            project_id=project_id,
            kind=ControlPreconditionKind.AUTHORITY,
            description="Cloudflare credential remains valid for delivery and rollback.",
            check_ref="cloudflare.authority",
            required_for=controlled_actions,
        ),
        ControlPrecondition(
            id="cloudflare-observability",
            project_id=project_id,
            kind=ControlPreconditionKind.OBSERVABILITY,
            description="Docs is reachable and zone HTTP analytics remain fresh.",
            check_ref="cloudflare.observability",
            required_for=(*controlled_actions, "observe"),
        ),
        ControlPrecondition(
            id="cloudflare-workspace",
            project_id=project_id,
            kind=ControlPreconditionKind.WORKSPACE,
            description="The live docs deployment still matches the Work receipt.",
            check_ref="cloudflare.workspace",
            required_for=observed_actions,
        ),
        ControlPrecondition(
            id="cloudflare-rollback-artifact",
            project_id=project_id,
            kind=ControlPreconditionKind.REVERSIBILITY,
            description="The known-good Worker version exists with the expected digest.",
            check_ref="cloudflare.rollback_artifact",
            required_for=controlled_actions,
        ),
    )


class CloudflareDeliveryControlProbe:
    """Verify credentials, fresh telemetry, and a known-good Worker version."""

    def __init__(
        self,
        *,
        config: CloudflareDeliveryConfig,
        api_token: str,
        client: httpx.AsyncClient,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._api_token = api_token
        self._client = client
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def evaluate(
        self,
        request: DeliveryControlRequest,
        preconditions: tuple[ControlPrecondition, ...],
    ) -> tuple[ControlCheckResult, ...]:
        if request.project_id != self._config.project_id:
            raise ValueError("Cloudflare probe belongs to a different project")
        results: list[ControlCheckResult] = []
        for precondition in preconditions:
            if precondition.project_id != request.project_id:
                raise ValueError("Cloudflare precondition belongs to a different project")
            if precondition.check_ref == "cloudflare.authority":
                result = await self._authority(request, precondition.id)
            elif precondition.check_ref == "cloudflare.observability":
                result = await self._observability(request, precondition.id)
            elif precondition.check_ref == "cloudflare.rollback_artifact":
                result = await self._rollback_artifact(request, precondition.id)
            elif precondition.check_ref == "cloudflare.workspace":
                result = await self._workspace(request, precondition.id)
            else:
                raise ValueError(f"unsupported Cloudflare precondition: {precondition.check_ref}")
            results.append(result)
        return tuple(results)

    async def _authority(
        self,
        request: DeliveryControlRequest,
        precondition_id: str,
    ) -> ControlCheckResult:
        checked_at = self._now()
        required_ttl = max(
            self._config.minimum_credential_ttl_seconds,
            int(request.expected_duration_seconds * self._config.credential_ttl_safety_factor),
        )
        try:
            response = await self._client.get(
                f"{self._config.api_base}/user/tokens/verify",
                headers={"Authorization": f"Bearer {self._api_token}"},
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
            elif expires_on is not None and expires_on < checked_at:
                detail = "Cloudflare credential is expired"
            elif (
                expires_on is not None and (expires_on - checked_at).total_seconds() < required_ttl
            ):
                detail = "Cloudflare credential TTL is insufficient"
            elif not_before is not None and not_before > checked_at:
                detail = "Cloudflare credential is not active yet"
            else:
                detail = None
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            detail = f"Cloudflare credential verification unavailable: {exc}"
        return ControlCheckResult(
            project_id=request.project_id,
            precondition_id=precondition_id,
            passed=detail is None,
            evidence_refs=(f"cloudflare://credential/{precondition_id}",),
            detail=detail,
            checked_at=checked_at,
        )

    async def _workspace(
        self,
        request: DeliveryControlRequest,
        precondition_id: str,
    ) -> ControlCheckResult:
        checked_at = self._now()
        evidence_ref = (
            f"{self._config.api_base}/accounts/{self._config.account_id}"
            f"/workers/scripts/{self._config.script_name}/deployments"
        )
        detail: str | None
        try:
            current = await self._current_deployment()
            if current is None:
                detail = "current Cloudflare deployment is missing"
            else:
                accepted = await self._accepted_workspace_traffic(request)
                current_traffic = _traffic_map(current["versions"])
                receipt_matches = (
                    request.action != "observe"
                    or request.deployment is not None
                    and str(current["id"]) == request.deployment.id
                )
                detail = (
                    None
                    if receipt_matches and current_traffic in accepted
                    else "current Cloudflare deployment moved outside the Work receipt"
                )
        except (httpx.HTTPError, KeyError, TypeError, ValueError, InvalidOperation) as exc:
            detail = f"Cloudflare workspace verification unavailable: {exc}"
        return ControlCheckResult(
            project_id=request.project_id,
            precondition_id=precondition_id,
            passed=detail is None,
            evidence_refs=(evidence_ref,),
            detail=detail,
            checked_at=checked_at,
        )

    async def _accepted_workspace_traffic(
        self,
        request: DeliveryControlRequest,
    ) -> tuple[dict[str, Decimal], ...]:
        accepted: list[dict[str, Decimal]] = []
        if request.action == "deploy":
            if request.known_good_candidate is None:
                raise ValueError("known-good rollback artifact is missing")
            known_good = await self._required_candidate_version(request.known_good_candidate)
            accepted.append({known_good: Decimal(100)})
        else:
            if request.deployment is None:
                raise ValueError("delivery receipt is missing")
            accepted.append(_receipt_traffic(request.deployment))

        if request.action in {"deploy", "promote"}:
            if request.target_exposure is None or request.known_good_candidate is None:
                raise ValueError("target delivery exposure is missing")
            candidate_version = await self._candidate_version(
                request.candidate,
                missing_ok=request.action == "deploy",
            )
            if candidate_version is not None:
                known_good = await self._required_candidate_version(request.known_good_candidate)
                accepted.append(
                    _split_traffic(
                        candidate_version,
                        known_good,
                        request.target_exposure,
                    )
                )
        elif request.action == "rollback":
            if request.known_good_candidate is None:
                raise ValueError("known-good rollback artifact is missing")
            known_good = await self._required_candidate_version(request.known_good_candidate)
            accepted.append({known_good: Decimal(100)})
        return tuple(accepted)

    async def _required_candidate_version(self, candidate: ReleaseCandidate) -> str:
        version_id = await self._candidate_version(candidate)
        if version_id is None:
            raise ValueError("Cloudflare version is missing")
        return version_id

    async def _candidate_version(
        self,
        candidate: ReleaseCandidate,
        *,
        missing_ok: bool = False,
    ) -> str | None:
        if candidate.artifact_ref.startswith("cloudflare-version://"):
            version_id = candidate.artifact_ref.removeprefix("cloudflare-version://")
            if not version_id:
                raise ValueError("Cloudflare version id is missing")
            return version_id
        if not candidate.artifact_ref.startswith("cloudflare-version-tag://"):
            if missing_ok:
                return None
            raise ValueError("candidate is not a Cloudflare version")
        tag = candidate.artifact_ref.removeprefix("cloudflare-version-tag://")
        response = await self._client.get(
            (
                f"{self._config.api_base}/accounts/{self._config.account_id}"
                f"/workers/scripts/{self._config.script_name}/versions"
            ),
            headers={"Authorization": f"Bearer {self._api_token}"},
            params={"deployable": "true"},
        )
        response.raise_for_status()
        payload = response.json()
        matches = [
            str(item["id"])
            for item in payload["result"]["items"]
            if item.get("annotations", {}).get("workers/tag") == tag
        ]
        if not matches and missing_ok:
            return None
        if len(matches) != 1:
            raise ValueError("Cloudflare release tag does not resolve exactly once")
        return matches[0]

    async def _current_deployment(self) -> dict | None:
        response = await self._client.get(
            (
                f"{self._config.api_base}/accounts/{self._config.account_id}"
                f"/workers/scripts/{self._config.script_name}/deployments"
            ),
            headers={"Authorization": f"Bearer {self._api_token}"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("success") is not True:
            raise ValueError("Cloudflare deployment lookup failed")
        deployments = payload["result"]["deployments"]
        if not deployments:
            return None
        if any(not isinstance(item.get("created_on"), str) for item in deployments):
            raise ValueError("Cloudflare deployment timestamp is missing")
        return max(deployments, key=lambda item: item["created_on"])

    async def _observability(
        self,
        request: DeliveryControlRequest,
        precondition_id: str,
    ) -> ControlCheckResult:
        checked_at = self._now()
        try:
            endpoint = await self._client.get(self._config.target_url)
            endpoint.raise_for_status()
            zone_response = await self._client.get(
                f"{self._config.api_base}/zones",
                headers={"Authorization": f"Bearer {self._api_token}"},
                params={
                    "name": self._config.zone_name,
                    "account.id": self._config.account_id,
                    "status": "active",
                    "per_page": 1,
                },
            )
            zone_response.raise_for_status()
            zone_payload = zone_response.json()
            zones = zone_payload["result"]
            if zone_payload.get("success") is not True or len(zones) != 1:
                raise ValueError("Cloudflare zone is unavailable")
            zone_id = str(zones[0]["id"])
            telemetry = await self._client.post(
                f"{self._config.api_base}/graphql",
                headers={"Authorization": f"Bearer {self._api_token}"},
                json={
                    "query": (
                        "query SagewaiDeliveryMetrics($zoneTag: string, $start: "
                        "Time, $end: Time, $host: string) { viewer { "
                        "zones(filter: {zoneTag: $zoneTag}) { metrics: "
                        "httpRequestsAdaptiveGroups(filter: {datetime_geq: $start, "
                        "datetime_leq: $end, clientRequestHTTPHost: $host, "
                        'requestSource: "eyeball"}, limit: 1, orderBy: '
                        "[datetimeMinute_DESC]) { count dimensions { "
                        "datetimeMinute } } } } }"
                    ),
                    "variables": {
                        "zoneTag": zone_id,
                        "start": datetime.fromtimestamp(
                            checked_at.timestamp()
                            - self._config.maximum_monitoring_staleness_seconds,
                            tz=timezone.utc,
                        ).isoformat(),
                        "end": checked_at.isoformat(),
                        "host": urlparse(self._config.target_url).hostname,
                    },
                },
            )
            telemetry.raise_for_status()
            payload = telemetry.json()
            if payload.get("errors"):
                detail = "Cloudflare monitoring query failed"
            else:
                telemetry_zones = payload["data"]["viewer"]["zones"]
                if len(telemetry_zones) != 1:
                    raise ValueError("Cloudflare monitoring zone is unavailable")
                metrics = telemetry_zones[0]["metrics"]
                if not metrics:
                    detail = None
                else:
                    latest = _parse_datetime(metrics[0]["dimensions"]["datetimeMinute"])
                    if latest is None:
                        raise ValueError("Cloudflare monitoring timestamp is missing")
                    age = (checked_at - latest).total_seconds()
                    if age < 0 or age > self._config.maximum_monitoring_staleness_seconds:
                        detail = "Cloudflare monitoring timestamp is stale"
                    else:
                        detail = None
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            detail = f"Cloudflare observability unavailable: {exc}"
        return ControlCheckResult(
            project_id=request.project_id,
            precondition_id=precondition_id,
            passed=detail is None,
            evidence_refs=(
                self._config.target_url,
                f"cloudflare://analytics/{self._config.zone_name}",
            ),
            detail=detail,
            checked_at=checked_at,
        )

    async def _rollback_artifact(
        self,
        request: DeliveryControlRequest,
        precondition_id: str,
    ) -> ControlCheckResult:
        checked_at = self._now()
        candidate = request.known_good_candidate
        detail: str | None
        if candidate is None:
            detail = "known-good rollback artifact is missing"
            evidence_refs: tuple[str, ...] = ()
        elif candidate.artifact_ref.startswith("cloudflare-version://"):
            evidence_refs = (candidate.artifact_ref,)
            detail = await self._verify_version(candidate)
        elif candidate.artifact_ref.startswith("cloudflare-version-tag://"):
            evidence_refs = (candidate.artifact_ref,)
            detail = await self._verify_version_tag(candidate)
        else:
            detail = "known-good rollback artifact is not a Cloudflare version"
            evidence_refs = (candidate.artifact_ref,)
        return ControlCheckResult(
            project_id=request.project_id,
            precondition_id=precondition_id,
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
                headers={"Authorization": f"Bearer {self._api_token}"},
            )
            response.raise_for_status()
            payload = response.json()
            result = payload["result"]
            if payload.get("success") is not True or str(result["id"]) != version_id:
                return "known-good rollback artifact is missing"
            remote_digest = cloudflare_version_digest(
                self._config.account_id,
                self._config.script_name,
                version_id,
            )
            if remote_digest != candidate.artifact_digest:
                return "known-good rollback artifact digest does not match"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return "known-good rollback artifact is missing"
            return f"Cloudflare rollback provider unavailable: {exc}"
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            return f"Cloudflare rollback provider unavailable: {exc}"
        return None

    async def _verify_version_tag(self, candidate: ReleaseCandidate) -> str | None:
        tag = candidate.artifact_ref.removeprefix("cloudflare-version-tag://")
        if tag != f"sagewai-{candidate.artifact_digest}":
            return "known-good rollback artifact digest does not match"
        try:
            response = await self._client.get(
                (
                    f"{self._config.api_base}/accounts/{self._config.account_id}"
                    f"/workers/scripts/{self._config.script_name}/versions"
                ),
                headers={"Authorization": f"Bearer {self._api_token}"},
                params={"deployable": "true"},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("success") is not True:
                return "known-good rollback artifact is missing"
            matches = [
                item
                for item in payload["result"]["items"]
                if item.get("annotations", {}).get("workers/tag") == tag
            ]
            if not matches:
                return "known-good rollback artifact is missing"
            if len(matches) > 1:
                return "Cloudflare rollback provider unavailable: release tag is ambiguous"
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


def _traffic_map(items) -> dict[str, Decimal]:
    result = {str(item["version_id"]): Decimal(str(item["percentage"])) for item in items}
    if len(result) != len(items) or sum(result.values()) != Decimal(100):
        raise ValueError("Cloudflare deployment traffic is invalid")
    return result


def _receipt_traffic(deployment: Deployment) -> dict[str, Decimal]:
    parsed = urlparse(deployment.provider_ref)
    parts = parsed.path.strip("/").split("/")
    if parsed.scheme != "cloudflare-deployment" or len(parts) != 2:
        raise ValueError("delivery receipt is not a Cloudflare deployment")
    candidate_version, rollback_version = parts
    if deployment.status == "rolled_back":
        return {candidate_version: Decimal(100)}
    return _split_traffic(candidate_version, rollback_version, deployment.exposure)


def _split_traffic(
    candidate_version: str,
    rollback_version: str,
    exposure: BlastRadius,
) -> dict[str, Decimal]:
    if exposure.dimension != "traffic" or not exposure.value.endswith("%"):
        raise ValueError("Cloudflare docs exposure must be a traffic percentage")
    percentage = Decimal(exposure.value.removesuffix("%"))
    if percentage <= 0 or percentage > 100:
        raise ValueError("Cloudflare traffic percentage is invalid")
    if percentage == 100:
        return {candidate_version: Decimal(100)}
    if candidate_version == rollback_version:
        raise ValueError("candidate and rollback versions must differ")
    return {
        rollback_version: Decimal(100) - percentage,
        candidate_version: percentage,
    }


__all__ = [
    "CloudflareDeliveryConfig",
    "CloudflareDeliveryControlProbe",
    "cloudflare_delivery_preconditions",
    "cloudflare_version_digest",
]
