# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cloudflare Workers adapter tests with no external side effects."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from sagewai.work.profiles.software.cloudflare_adapter import (
    CloudflareDeploymentReference,
    CloudflareDocsAdapterConfig,
    CloudflareDocsDeploymentProvider,
    CloudflareDocsObservationProvider,
    CloudflareDocsReleaseProvider,
    CommandResult,
    cloudflare_static_asset_digest,
)
from sagewai.work.profiles.software.delivery import (
    BlastRadius,
    HealthGate,
    HealthVerdict,
    ReleaseCandidate,
)

PROJECT_ID = "project-a"
WORK_ID = "work-1"
COMMIT_SHA = "a" * 40
NEW_VERSION_ID = "11111111-1111-1111-1111-111111111111"
OLD_VERSION_ID = "22222222-2222-2222-2222-222222222222"


class FakeCommandRunner:
    def __init__(self, results, *, on_run=None) -> None:
        self.results = list(results)
        self.on_run = on_run
        self.calls = []

    async def run(self, args, *, cwd: Path, env=None) -> CommandResult:
        self.calls.append((tuple(args), cwd, dict(env or {})))
        if self.on_run is not None:
            self.on_run(tuple(args))
        return self.results.pop(0)


def _config(tmp_path: Path) -> CloudflareDocsAdapterConfig:
    repository = tmp_path / "repo"
    docs = repository / "apps" / "docs"
    docs.mkdir(parents=True)
    (docs / "wrangler.toml").write_text(
        'name = "docs"\ncompatibility_date = "2026-04-04"\n',
        encoding="utf-8",
    )
    return CloudflareDocsAdapterConfig(
        project_id=PROJECT_ID,
        work_id=WORK_ID,
        repository_root=repository,
        docs_directory=docs,
        artifact_root=tmp_path / "artifacts",
        account_id="account-1",
        script_name="docs",
        target_url="https://docs.sagewai.ai",
        observation_sample_interval_seconds=30,
    )


def _local_candidate(config: CloudflareDocsAdapterConfig) -> ReleaseCandidate:
    snapshot = config.artifact_root / "snapshot-source"
    snapshot.mkdir(parents=True)
    (snapshot / "index.html").write_text("<h1>Sagewai docs</h1>", encoding="utf-8")
    digest = cloudflare_static_asset_digest(snapshot)
    final_snapshot = config.artifact_root / digest
    snapshot.rename(final_snapshot)
    return ReleaseCandidate(
        id=f"docs-{COMMIT_SHA[:12]}-{digest[:12]}",
        project_id=PROJECT_ID,
        work_id=WORK_ID,
        commit_sha=COMMIT_SHA,
        artifact_ref=f"cloudflare-version-tag://sagewai-{digest}",
        artifact_digest=digest,
        config_revision="config-digest",
        verification_ref="verification://1",
        review_ref="review://1",
    )


def _known_good() -> ReleaseCandidate:
    from sagewai.work.profiles.software.cloudflare import cloudflare_version_digest

    return ReleaseCandidate(
        id="known-good",
        project_id=PROJECT_ID,
        work_id="previous-work",
        commit_sha="b" * 40,
        artifact_ref=f"cloudflare-version://{OLD_VERSION_ID}",
        artifact_digest=cloudflare_version_digest("account-1", "docs", OLD_VERSION_ID),
        config_revision="old-config",
        verification_ref="verification://old",
        review_ref="review://old",
    )


class CloudflareState:
    def __init__(self) -> None:
        self.versions = []
        self.deployments = []
        self.posts = []
        self.target_status = 200
        self.target_requests = []

    def add_candidate_version(self, tag: str) -> None:
        self.versions.append(
            {
                "id": NEW_VERSION_ID,
                "annotations": {"workers/tag": tag},
                "metadata": {"created_on": "2026-08-27T10:00:00Z"},
            }
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "docs.sagewai.ai":
            self.target_requests.append(request)
            return httpx.Response(
                self.target_status,
                request=request,
                text="<h1>Sagewai docs</h1>",
            )
        path = request.url.path
        if path.endswith("/versions") and request.method == "GET":
            assert request.url.params["deployable"] == "true"
            return httpx.Response(
                200,
                request=request,
                json={"success": True, "result": {"items": self.versions}},
            )
        if path.endswith("/deployments") and request.method == "GET":
            return httpx.Response(
                200,
                request=request,
                json={
                    "success": True,
                    "result": {"deployments": self.deployments},
                },
            )
        if path.endswith("/deployments") and request.method == "POST":
            body = json.loads(request.content)
            self.posts.append(body)
            deployment = {
                "id": f"33333333-3333-3333-3333-{len(self.posts):012d}",
                "strategy": "percentage",
                "versions": body["versions"],
                "annotations": body["annotations"],
            }
            self.deployments.insert(0, deployment)
            return httpx.Response(
                200,
                request=request,
                json={"success": True, "result": deployment},
            )
        if "/deployments/" in path and request.method == "GET":
            deployment_id = path.rsplit("/", 1)[-1]
            deployment = next(item for item in self.deployments if item["id"] == deployment_id)
            return httpx.Response(
                200,
                request=request,
                json={"success": True, "result": deployment},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


@pytest.mark.asyncio
async def test_release_provider_builds_one_immutable_local_snapshot(tmp_path: Path) -> None:
    config = _config(tmp_path)
    output = config.docs_directory / "out"
    output.mkdir()
    (output / "index.html").write_text("<h1>Sagewai docs</h1>", encoding="utf-8")
    runner = FakeCommandRunner(
        (
            CommandResult(returncode=0, stdout=f"{COMMIT_SHA}\n", stderr=""),
            CommandResult(returncode=0, stdout="", stderr=""),
            CommandResult(returncode=0, stdout="build passed", stderr=""),
        )
    )
    provider = CloudflareDocsReleaseProvider(
        config=config,
        command_runner=runner,
        verification_ref="verification://1",
        review_ref="review://1",
    )

    candidate = await provider.build(COMMIT_SHA)

    assert candidate.artifact_ref == (
        f"cloudflare-version-tag://sagewai-{candidate.artifact_digest}"
    )
    assert (config.artifact_root / candidate.artifact_digest / "index.html").read_text(
        encoding="utf-8"
    ) == "<h1>Sagewai docs</h1>"
    assert [call[0] for call in runner.calls] == [
        ("git", "rev-parse", "HEAD"),
        ("git", "status", "--porcelain", "--untracked-files=no"),
        ("pnpm", "--filter", "@sagewai/docs", "build"),
    ]


@pytest.mark.asyncio
async def test_deployment_provider_uploads_once_promotes_same_version_and_rolls_back(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    candidate = _local_candidate(config)
    state = CloudflareState()
    tag = candidate.artifact_ref.removeprefix("cloudflare-version-tag://")
    runner = FakeCommandRunner(
        (
            CommandResult(
                returncode=0,
                stdout=f"Uploaded docs\nWorker Version ID: {NEW_VERSION_ID}\n",
                stderr="",
            ),
        ),
        on_run=lambda args: state.add_candidate_version(tag),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(state)) as client:
        provider = CloudflareDocsDeploymentProvider(
            config=config,
            api_token="token",
            client=client,
            command_runner=runner,
        )

        canary = await provider.deploy(
            candidate,
            "production",
            BlastRadius(dimension="traffic", value="5%"),
            _known_good(),
        )
        rollout = await provider.promote(
            canary,
            BlastRadius(dimension="traffic", value="20%"),
        )
        rolled_back = await provider.rollback(rollout, _known_good())

    assert len(runner.calls) == 1
    assert "versions" in runner.calls[0][0]
    assert "upload" in runner.calls[0][0]
    assert runner.calls[0][0][runner.calls[0][0].index("--assets") + 1] == str(
        config.artifact_root / candidate.artifact_digest
    )
    assert state.posts[0]["versions"] == [
        {"version_id": OLD_VERSION_ID, "percentage": 95},
        {"version_id": NEW_VERSION_ID, "percentage": 5},
    ]
    assert state.posts[1]["versions"] == [
        {"version_id": OLD_VERSION_ID, "percentage": 80},
        {"version_id": NEW_VERSION_ID, "percentage": 20},
    ]
    assert state.posts[2]["versions"] == [{"version_id": OLD_VERSION_ID, "percentage": 100}]
    assert canary.release_candidate_id == rollout.release_candidate_id == candidate.id
    assert rolled_back.release_candidate_id == _known_good().id
    reference = CloudflareDeploymentReference.from_uri(rollout.provider_ref)
    assert reference.candidate_version_id == NEW_VERSION_ID
    assert reference.rollback_version_id == OLD_VERSION_ID


@pytest.mark.asyncio
async def test_deployment_provider_recovers_uploaded_and_configured_receipts(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    candidate = _local_candidate(config)
    tag = candidate.artifact_ref.removeprefix("cloudflare-version-tag://")
    state = CloudflareState()
    state.add_candidate_version(tag)
    state.deployments.append(
        {
            "id": "44444444-4444-4444-4444-444444444444",
            "strategy": "percentage",
            "versions": [
                {"version_id": OLD_VERSION_ID, "percentage": 95},
                {"version_id": NEW_VERSION_ID, "percentage": 5},
            ],
            "annotations": {},
        }
    )
    runner = FakeCommandRunner(())
    async with httpx.AsyncClient(transport=httpx.MockTransport(state)) as client:
        provider = CloudflareDocsDeploymentProvider(
            config=config,
            api_token="token",
            client=client,
            command_runner=runner,
        )

        recovered = await provider.deploy(
            candidate,
            "production",
            BlastRadius(dimension="traffic", value="5%"),
            _known_good(),
        )

    assert recovered.id == "44444444-4444-4444-4444-444444444444"
    assert runner.calls == []
    assert state.posts == []


@pytest.mark.asyncio
async def test_observation_targets_exact_version_and_samples_full_window(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    state = CloudflareState()
    deployment_id = "55555555-5555-5555-5555-555555555555"
    state.deployments.append(
        {
            "id": deployment_id,
            "strategy": "percentage",
            "versions": [
                {"version_id": OLD_VERSION_ID, "percentage": 95},
                {"version_id": NEW_VERSION_ID, "percentage": 5},
            ],
            "annotations": {},
        }
    )
    deployment = CloudflareDeploymentReference(
        deployment_id=deployment_id,
        candidate_version_id=NEW_VERSION_ID,
        rollback_version_id=OLD_VERSION_ID,
    ).to_deployment(
        project_id=PROJECT_ID,
        work_id=WORK_ID,
        release_candidate_id="candidate-1",
        environment="production",
        exposure=BlastRadius(dimension="traffic", value="5%"),
        status="active",
    )
    current = 0.0

    async def advance(seconds: float) -> None:
        nonlocal current
        current += seconds

    async with httpx.AsyncClient(transport=httpx.MockTransport(state)) as client:
        provider = CloudflareDocsObservationProvider(
            config=config,
            api_token="token",
            client=client,
            monotonic=lambda: current,
            sleep=advance,
        )
        result = await provider.observe(
            deployment,
            gates=(
                HealthGate(
                    id="docs-available",
                    project_id=PROJECT_ID,
                    description="docs available",
                    check_ref="https://docs.sagewai.ai",
                    failure_verdict=HealthVerdict.FAIL,
                ),
            ),
            window_seconds=60,
        )

    assert result.verdict is HealthVerdict.PASS
    assert len(state.target_requests) == 3
    assert all(
        request.headers["cloudflare-workers-version-overrides"] == f'docs="{NEW_VERSION_ID}"'
        for request in state.target_requests
    )


@pytest.mark.asyncio
async def test_observation_failure_returns_health_fail(tmp_path: Path) -> None:
    config = _config(tmp_path).model_copy(update={"observation_sample_interval_seconds": 1})
    state = CloudflareState()
    state.target_status = 503
    deployment_id = "66666666-6666-6666-6666-666666666666"
    state.deployments.append(
        {
            "id": deployment_id,
            "strategy": "percentage",
            "versions": [{"version_id": NEW_VERSION_ID, "percentage": 100}],
            "annotations": {},
        }
    )
    deployment = CloudflareDeploymentReference(
        deployment_id=deployment_id,
        candidate_version_id=NEW_VERSION_ID,
        rollback_version_id=OLD_VERSION_ID,
    ).to_deployment(
        project_id=PROJECT_ID,
        work_id=WORK_ID,
        release_candidate_id="candidate-1",
        environment="production",
        exposure=BlastRadius(dimension="traffic", value="100%"),
        status="active",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(state)) as client:
        provider = CloudflareDocsObservationProvider(
            config=config,
            api_token="token",
            client=client,
        )
        result = await provider.observe(
            deployment,
            gates=(
                HealthGate(
                    id="docs-available",
                    project_id=PROJECT_ID,
                    description="docs available",
                    check_ref="https://docs.sagewai.ai",
                    failure_verdict=HealthVerdict.FAIL,
                ),
            ),
            window_seconds=1,
        )

    assert result.verdict is HealthVerdict.FAIL
    assert result.gate_results[0].passed is False
