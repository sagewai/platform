# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cloudflare Workers delivery adapter for Sagewai's static docs site."""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from sagewai.fleet.execution import WorkerProcessResult, run_worker_subprocess
from sagewai.work.profiles.software.delivery import (
    BlastRadius,
    DeliveryControlLostError,
    Deployment,
    HealthGate,
    HealthGateResult,
    HealthVerdict,
    ObservationResult,
    ReleaseCandidate,
)

_VERSION_ID = re.compile(
    r"Worker Version ID:\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


ProcessRunner = Callable[..., Awaitable[WorkerProcessResult]]


class CloudflareDocsAdapterConfig(BaseModel):
    """Exact repository and Cloudflare coordinates for the docs delivery path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    work_id: str
    repository_root: Path
    docs_directory: Path
    artifact_root: Path
    account_id: str
    script_name: str
    target_url: str
    observation_sample_interval_seconds: float = Field(gt=0)
    command_timeout_seconds: float = Field(gt=0)
    api_base: str = "https://api.cloudflare.com/client/v4"


def cloudflare_static_asset_digest(directory: Path) -> str:
    """Return a deterministic digest for one static export directory."""

    if not directory.is_dir():
        raise ValueError(f"static asset directory is missing: {directory}")
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    if not files:
        raise ValueError("static asset directory is empty")
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(directory).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


class CloudflareDocsReleaseProvider:
    """Build and snapshot the docs artifact locally without configuring traffic."""

    def __init__(
        self,
        *,
        config: CloudflareDocsAdapterConfig,
        process_runner: ProcessRunner = run_worker_subprocess,
        verification_ref: str,
        review_ref: str,
    ) -> None:
        self._config = config
        self._run_process = process_runner
        self._verification_ref = verification_ref
        self._review_ref = review_ref

    async def build(self, commit_sha: str) -> ReleaseCandidate:
        head = await self._run(("git", "rev-parse", "HEAD"), self._config.repository_root)
        if head.stdout.strip() != commit_sha:
            raise ValueError("release commit does not match repository HEAD")
        status = await self._run(
            ("git", "status", "--porcelain", "--untracked-files=no"),
            self._config.repository_root,
        )
        if status.stdout.strip():
            raise ValueError("release build requires a clean tracked worktree")
        await self._run(
            ("pnpm", "--filter", "@sagewai/docs", "build"),
            self._config.repository_root,
        )

        output = self._config.docs_directory / "out"
        artifact_digest = cloudflare_static_asset_digest(output)
        snapshot = self._config.artifact_root / artifact_digest
        self._config.artifact_root.mkdir(parents=True, exist_ok=True)
        if snapshot.exists():
            if cloudflare_static_asset_digest(snapshot) != artifact_digest:
                raise ValueError("release snapshot conflicts with its digest")
        else:
            shutil.copytree(output, snapshot)
        config_digest = hashlib.sha256(
            (self._config.docs_directory / "wrangler.toml").read_bytes()
        ).hexdigest()
        tag = f"sagewai-{artifact_digest}"
        return ReleaseCandidate(
            id=f"docs-{commit_sha[:12]}-{artifact_digest[:12]}",
            project_id=self._config.project_id,
            work_id=self._config.work_id,
            commit_sha=commit_sha,
            artifact_ref=f"cloudflare-version-tag://{tag}",
            artifact_digest=artifact_digest,
            config_revision=config_digest,
            verification_ref=self._verification_ref,
            review_ref=self._review_ref,
        )

    async def _run(self, args: Sequence[str], cwd: Path) -> WorkerProcessResult:
        result = await self._run_process(
            argv=args,
            cwd=cwd,
            timeout=self._config.command_timeout_seconds,
            output_limit=100_000,
        )
        if result.returncode != 0:
            raise RuntimeError(f"command failed: {' '.join(args)}\n{result.stderr}")
        return result


class CloudflareDeploymentReference(BaseModel):
    """Persisted Cloudflare deployment and rollback coordinates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: str
    candidate_version_id: str
    rollback_version_id: str

    def to_uri(self) -> str:
        return (
            f"cloudflare-deployment://{self.deployment_id}/"
            f"{self.candidate_version_id}/{self.rollback_version_id}"
        )

    @classmethod
    def from_uri(cls, value: str) -> CloudflareDeploymentReference:
        parsed = urlparse(value)
        parts = parsed.path.strip("/").split("/")
        if parsed.scheme != "cloudflare-deployment" or len(parts) != 2:
            raise ValueError("deployment provider reference is not Cloudflare")
        return cls(
            deployment_id=parsed.netloc,
            candidate_version_id=parts[0],
            rollback_version_id=parts[1],
        )

    def to_deployment(
        self,
        *,
        project_id: str,
        work_id: str,
        release_candidate_id: str,
        environment: str,
        exposure: BlastRadius,
        status: Literal["active", "rolled_back"],
    ) -> Deployment:
        return Deployment(
            id=self.deployment_id,
            project_id=project_id,
            work_id=work_id,
            release_candidate_id=release_candidate_id,
            environment=environment,
            exposure=exposure,
            provider_ref=self.to_uri(),
            status=status,
        )


class CloudflareDocsDeploymentProvider:
    """Upload immutable docs versions and configure percentage deployments."""

    def __init__(
        self,
        *,
        config: CloudflareDocsAdapterConfig,
        api_token: str,
        client: httpx.AsyncClient,
        process_runner: ProcessRunner = run_worker_subprocess,
    ) -> None:
        self._config = config
        self._api_token = api_token
        self._client = client
        self._run_process = process_runner

    async def deploy(
        self,
        candidate: ReleaseCandidate,
        environment: str,
        exposure: BlastRadius,
        known_good_candidate: ReleaseCandidate,
    ) -> Deployment:
        self._validate_candidate(candidate, environment)
        candidate_version = await self._ensure_version(candidate)
        rollback_version = await self._version_for(known_good_candidate)
        traffic = _traffic_percentage(exposure)
        desired = _traffic_split(candidate_version, rollback_version, traffic)
        receipt = await self._ensure_deployment(
            desired,
            expected_current=({"version_id": rollback_version, "percentage": 100},),
            message=f"Sagewai Work {candidate.work_id}: deploy {candidate.id}",
        )
        return self._deployment(
            receipt,
            candidate=candidate,
            environment=environment,
            exposure=exposure,
            candidate_version=candidate_version,
            rollback_version=rollback_version,
            status="active",
        )

    async def promote(
        self,
        deployment: Deployment,
        exposure: BlastRadius,
    ) -> Deployment:
        if deployment.environment != "production" or deployment.status != "active":
            raise ValueError("Cloudflare promotion requires an active production receipt")
        reference = CloudflareDeploymentReference.from_uri(deployment.provider_ref)
        desired = _traffic_split(
            reference.candidate_version_id,
            reference.rollback_version_id,
            _traffic_percentage(exposure),
        )
        receipt = await self._ensure_deployment(
            desired,
            expected_current=_deployment_traffic(deployment, reference),
            message=f"Sagewai Work {deployment.work_id}: promote {deployment.id}",
        )
        updated = CloudflareDeploymentReference(
            deployment_id=str(receipt["id"]),
            candidate_version_id=reference.candidate_version_id,
            rollback_version_id=reference.rollback_version_id,
        )
        return updated.to_deployment(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            release_candidate_id=deployment.release_candidate_id,
            environment=deployment.environment,
            exposure=exposure,
            status="active",
        )

    async def rollback(
        self,
        deployment: Deployment,
        known_good_candidate: ReleaseCandidate,
    ) -> Deployment:
        reference = CloudflareDeploymentReference.from_uri(deployment.provider_ref)
        rollback_version = await self._version_for(known_good_candidate)
        receipt = await self._ensure_deployment(
            ({"version_id": rollback_version, "percentage": 100},),
            expected_current=_deployment_traffic(deployment, reference),
            message=f"Sagewai Work {deployment.work_id}: rollback {deployment.id}",
        )
        rolled_back = CloudflareDeploymentReference(
            deployment_id=str(receipt["id"]),
            candidate_version_id=rollback_version,
            rollback_version_id=reference.candidate_version_id,
        )
        return rolled_back.to_deployment(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            release_candidate_id=known_good_candidate.id,
            environment=deployment.environment,
            exposure=deployment.exposure,
            status="rolled_back",
        )

    def _validate_candidate(self, candidate: ReleaseCandidate, environment: str) -> None:
        if candidate.project_id != self._config.project_id:
            raise ValueError("release candidate belongs to a different project")
        if environment != "production":
            raise ValueError("Sagewai docs has no configured staging environment")
        tag = _candidate_tag(candidate)
        snapshot = self._config.artifact_root / candidate.artifact_digest
        if tag != f"sagewai-{candidate.artifact_digest}":
            raise ValueError("release tag does not bind the artifact digest")
        if cloudflare_static_asset_digest(snapshot) != candidate.artifact_digest:
            raise ValueError("release snapshot digest does not match")

    async def _ensure_version(self, candidate: ReleaseCandidate) -> str:
        tag = _candidate_tag(candidate)
        existing = await self._version_by_tag(tag)
        if existing is not None:
            return existing
        snapshot = self._config.artifact_root / candidate.artifact_digest
        result = await self._run_process(
            argv=(
                "pnpm",
                "exec",
                "wrangler",
                "versions",
                "upload",
                "--assets",
                str(snapshot),
                "--name",
                self._config.script_name,
                "--tag",
                tag,
                "--message",
                f"Sagewai Work {candidate.work_id} candidate {candidate.id}",
                "--strict",
            ),
            cwd=self._config.docs_directory,
            explicit_env={
                "CLOUDFLARE_API_TOKEN": self._api_token,
                "CLOUDFLARE_ACCOUNT_ID": self._config.account_id,
            },
            timeout=self._config.command_timeout_seconds,
            output_limit=100_000,
        )
        if result.returncode != 0:
            raise DeliveryControlLostError(
                "cloudflare-workspace",
                f"Wrangler version upload result is not usable: {result.stderr}",
                evidence_refs=(f"{self._worker_url}/versions",),
            )
        match = _VERSION_ID.search(result.stdout)
        if match is None:
            raise DeliveryControlLostError(
                "cloudflare-workspace",
                "Wrangler did not report the uploaded Worker Version ID",
                evidence_refs=(f"{self._worker_url}/versions",),
            )
        uploaded = str(uuid.UUID(match.group(1)))
        resolved = await self._version_by_tag(tag)
        if resolved != uploaded:
            raise DeliveryControlLostError(
                "cloudflare-workspace",
                "uploaded Worker version does not resolve from its release tag",
                evidence_refs=(f"{self._worker_url}/versions",),
            )
        return uploaded

    async def _version_for(self, candidate: ReleaseCandidate) -> str:
        if candidate.artifact_ref.startswith("cloudflare-version://"):
            version_id = candidate.artifact_ref.removeprefix("cloudflare-version://")
            return str(uuid.UUID(version_id))
        tag = _candidate_tag(candidate)
        resolved = await self._version_by_tag(tag)
        if resolved is None:
            raise ValueError("known-good Cloudflare version tag is missing")
        return resolved

    async def _version_by_tag(self, tag: str) -> str | None:
        try:
            response = await self._client.get(
                f"{self._worker_url}/versions",
                headers=self._headers,
                params={"deployable": "true"},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("success") is not True:
                raise ValueError("Cloudflare deployable version lookup failed")
            versions = payload["result"]["items"]
            matches = [
                str(item["id"])
                for item in versions
                if item.get("annotations", {}).get("workers/tag") == tag
            ]
            if len(matches) > 1:
                raise ValueError("Cloudflare release tag resolves to multiple versions")
            return matches[0] if matches else None
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise DeliveryControlLostError(
                "cloudflare-workspace",
                f"Cloudflare Worker version cannot be resolved: {exc}",
                evidence_refs=(f"{self._worker_url}/versions",),
            ) from exc

    async def _ensure_deployment(
        self,
        desired: tuple[dict[str, int | float | str], ...],
        *,
        expected_current: tuple[dict[str, int | float | str], ...],
        message: str,
    ) -> dict:
        current = await self._current_deployment()
        if current is not None and _same_traffic(current["versions"], desired):
            return current
        if current is None or not _same_traffic(current["versions"], expected_current):
            raise DeliveryControlLostError(
                "cloudflare-workspace",
                "current Cloudflare deployment moved outside the approved transition",
                evidence_refs=(f"{self._worker_url}/deployments",),
            )
        try:
            response = await self._client.post(
                f"{self._worker_url}/deployments",
                headers={**self._headers, "Content-Type": "application/json"},
                json={
                    "strategy": "percentage",
                    "versions": list(desired),
                    "annotations": {
                        "workers/message": message,
                        "workers/triggered_by": "sagewai",
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
            receipt = payload["result"]
            if (
                payload.get("success") is not True
                or receipt.get("strategy") != "percentage"
                or not _same_traffic(receipt["versions"], desired)
                or not isinstance(receipt.get("id"), str)
            ):
                raise ValueError("Cloudflare deployment receipt does not match the request")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise DeliveryControlLostError(
                "cloudflare-workspace",
                f"Cloudflare deployment result is unavailable: {exc}",
                evidence_refs=(f"{self._worker_url}/deployments",),
            ) from exc
        return receipt

    async def _current_deployment(self) -> dict | None:
        return await _read_current_deployment(
            client=self._client,
            worker_url=self._worker_url,
            headers=self._headers,
        )

    def _deployment(
        self,
        receipt: dict,
        *,
        candidate: ReleaseCandidate,
        environment: str,
        exposure: BlastRadius,
        candidate_version: str,
        rollback_version: str,
        status: Literal["active", "rolled_back"],
    ) -> Deployment:
        reference = CloudflareDeploymentReference(
            deployment_id=str(receipt["id"]),
            candidate_version_id=candidate_version,
            rollback_version_id=rollback_version,
        )
        return reference.to_deployment(
            project_id=candidate.project_id,
            work_id=candidate.work_id,
            release_candidate_id=candidate.id,
            environment=environment,
            exposure=exposure,
            status=status,
        )

    @property
    def _worker_url(self) -> str:
        return (
            f"{self._config.api_base}/accounts/{self._config.account_id}"
            f"/workers/scripts/{self._config.script_name}"
        )

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_token}"}


class CloudflareDocsObservationProvider:
    """Observe the exact Worker version and configured traffic for a full window."""

    def __init__(
        self,
        *,
        config: CloudflareDocsAdapterConfig,
        api_token: str,
        client: httpx.AsyncClient,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._api_token = api_token
        self._client = client
        self._monotonic = monotonic
        self._sleep = sleep

    async def observe(
        self,
        deployment: Deployment,
        gates: tuple[HealthGate, ...],
        window_seconds: int,
    ) -> ObservationResult:
        if deployment.project_id != self._config.project_id:
            raise ValueError("deployment belongs to a different project")
        if not gates:
            raise ValueError("at least one health gate is required")
        if any(
            gate.project_id != deployment.project_id or gate.check_ref != self._config.target_url
            for gate in gates
        ):
            raise ValueError("health gate does not belong to the docs delivery path")
        reference = CloudflareDeploymentReference.from_uri(deployment.provider_ref)
        passed = {gate.id: True for gate in gates}
        evidence = {
            f"{self._worker_url}/deployments/{reference.deployment_id}",
            self._config.target_url,
        }
        deadline = self._monotonic() + window_seconds
        while True:
            await self._configured(deployment, reference)
            endpoint_passed = await self._endpoint_passes(reference)
            if not endpoint_passed:
                passed = dict.fromkeys(passed, False)
            if not all(passed.values()) or self._monotonic() >= deadline:
                break
            await self._sleep(
                min(
                    self._config.observation_sample_interval_seconds,
                    deadline - self._monotonic(),
                )
            )
        gate_results = tuple(
            HealthGateResult(
                project_id=deployment.project_id,
                gate_id=gate.id,
                passed=passed[gate.id],
                evidence_refs=tuple(sorted(evidence)),
            )
            for gate in gates
        )
        failed = [result for result in gate_results if not result.passed]
        verdict = HealthVerdict.PASS
        if failed:
            gate_by_id = {gate.id: gate for gate in gates}
            verdict = (
                HealthVerdict.FAIL
                if any(
                    gate_by_id[result.gate_id].failure_verdict is HealthVerdict.FAIL
                    for result in failed
                )
                else HealthVerdict.HOLD
            )
        return ObservationResult(
            project_id=deployment.project_id,
            work_id=deployment.work_id,
            deployment_id=deployment.id,
            verdict=verdict,
            gate_results=gate_results,
            evidence_refs=tuple(sorted(evidence)),
        )

    async def _configured(
        self,
        deployment: Deployment,
        reference: CloudflareDeploymentReference,
    ) -> None:
        try:
            receipt = await _read_current_deployment(
                client=self._client,
                worker_url=self._worker_url,
                headers=self._headers,
            )
            if (
                receipt is None
                or str(receipt["id"]) != deployment.id
                or not _same_traffic(
                    receipt["versions"],
                    _deployment_traffic(deployment, reference),
                )
            ):
                raise DeliveryControlLostError(
                    "cloudflare-workspace",
                    "current Cloudflare deployment no longer matches the Work receipt",
                    evidence_refs=(f"{self._worker_url}/deployments",),
                )
        except DeliveryControlLostError:
            raise
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise DeliveryControlLostError(
                "cloudflare-workspace",
                f"current Cloudflare deployment cannot be verified: {exc}",
                evidence_refs=(f"{self._worker_url}/deployments",),
            ) from exc

    async def _endpoint_passes(
        self,
        reference: CloudflareDeploymentReference,
    ) -> bool:
        try:
            response = await self._client.get(
                self._config.target_url,
                headers={
                    "Cloudflare-Workers-Version-Overrides": (
                        f'{self._config.script_name}="{reference.candidate_version_id}"'
                    )
                },
            )
        except httpx.HTTPError as exc:
            raise DeliveryControlLostError(
                "cloudflare-observability",
                f"docs endpoint reachability is unavailable: {exc}",
                evidence_refs=(self._config.target_url,),
            ) from exc
        return response.is_success

    @property
    def _worker_url(self) -> str:
        return (
            f"{self._config.api_base}/accounts/{self._config.account_id}"
            f"/workers/scripts/{self._config.script_name}"
        )

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_token}"}


async def _read_current_deployment(
    *,
    client: httpx.AsyncClient,
    worker_url: str,
    headers: Mapping[str, str],
) -> dict | None:
    try:
        response = await client.get(
            f"{worker_url}/deployments",
            headers=headers,
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
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise DeliveryControlLostError(
            "cloudflare-workspace",
            f"current Cloudflare deployment is unavailable: {exc}",
            evidence_refs=(f"{worker_url}/deployments",),
        ) from exc


def _deployment_traffic(
    deployment: Deployment,
    reference: CloudflareDeploymentReference,
) -> tuple[dict[str, int | float | str], ...]:
    if deployment.status == "rolled_back":
        return ({"version_id": reference.candidate_version_id, "percentage": 100},)
    return _traffic_split(
        reference.candidate_version_id,
        reference.rollback_version_id,
        _traffic_percentage(deployment.exposure),
    )


def _candidate_tag(candidate: ReleaseCandidate) -> str:
    prefix = "cloudflare-version-tag://"
    if not candidate.artifact_ref.startswith(prefix):
        raise ValueError("release candidate is not a Cloudflare version tag")
    tag = candidate.artifact_ref.removeprefix(prefix)
    if not tag:
        raise ValueError("Cloudflare version tag is missing")
    return tag


def _traffic_percentage(exposure: BlastRadius) -> int | float:
    if exposure.dimension != "traffic" or not exposure.value.endswith("%"):
        raise ValueError("Cloudflare docs exposure must be a traffic percentage")
    try:
        value = Decimal(exposure.value.removesuffix("%"))
    except InvalidOperation as exc:
        raise ValueError("Cloudflare traffic percentage is invalid") from exc
    if value < Decimal("0.01") or value > Decimal(100):
        raise ValueError("Cloudflare traffic percentage is outside 0.01-100")
    return int(value) if value == value.to_integral() else float(value)


def _traffic_split(
    candidate_version: str,
    rollback_version: str,
    candidate_percentage: int | float,
) -> tuple[dict[str, int | float | str], ...]:
    if candidate_percentage == 100:
        return ({"version_id": candidate_version, "percentage": 100},)
    return (
        {
            "version_id": rollback_version,
            "percentage": float(Decimal(100) - Decimal(str(candidate_percentage)))
            if not float(candidate_percentage).is_integer()
            else int(Decimal(100) - Decimal(str(candidate_percentage))),
        },
        {"version_id": candidate_version, "percentage": candidate_percentage},
    )


def _same_traffic(
    actual: Sequence[Mapping[str, object]],
    expected: Sequence[Mapping[str, object]],
) -> bool:
    def normalize(items: Sequence[Mapping[str, object]]) -> dict[str, Decimal]:
        return {str(item["version_id"]): Decimal(str(item["percentage"])) for item in items}

    return normalize(actual) == normalize(expected)


__all__ = [
    "CloudflareDeploymentReference",
    "CloudflareDocsAdapterConfig",
    "CloudflareDocsDeploymentProvider",
    "CloudflareDocsObservationProvider",
    "CloudflareDocsReleaseProvider",
    "ProcessRunner",
    "cloudflare_static_asset_digest",
]
