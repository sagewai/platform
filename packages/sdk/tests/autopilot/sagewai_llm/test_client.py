# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for SagewaiLLMClient — happy path and graceful degradation."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from sagewai.autopilot.sagewai_llm.cache import BlueprintCache
from sagewai.autopilot.sagewai_llm.client import SagewaiLLMClient
from sagewai.autopilot.sagewai_llm.errors import ClientUnreachable, QuotaExceeded
from sagewai.autopilot.sagewai_llm.identity import FileIdentityStore, ensure_identity

BASE_URL = "https://api.sagewai.ai"


@pytest.fixture()
def client(tmp_path: Path) -> SagewaiLLMClient:
    ident_store = FileIdentityStore(tmp_path / "identity.json")
    ident = ensure_identity(ident_store)
    cache = BlueprintCache(tmp_path / "cache", ttl_seconds=3600)
    return SagewaiLLMClient(
        base_url=BASE_URL,
        identity=ident,
        cache=cache,
    )


def _quota_header(tier: str = "anonymous", endpoint: str = "generate") -> str:
    return f"tier={tier};endpoint={endpoint};used=1;limit=50;reset=2026-05-01T00:00:00Z"


@pytest.mark.asyncio
async def test_generate_blueprint_happy_path(client: SagewaiLLMClient, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/blueprints/generate",
        method="POST",
        json={"blueprint_json": '{"id":"x"}', "confidence": 0.82},
        headers={"X-Sagewai-Quota": _quota_header()},
    )
    resp = await client.generate_blueprint(goal="run daily research")
    assert resp.blueprint_json == '{"id":"x"}'
    assert client.last_quota is not None
    assert client.last_quota.tier == "anonymous"


@pytest.mark.asyncio
async def test_retrieve_blueprints_happy_path(client: SagewaiLLMClient, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/blueprints/retrieve",
        method="POST",
        json={
            "candidates": [
                {"blueprint_json": '{"id":"a"}', "score": 0.9},
                {"blueprint_json": '{"id":"b"}', "score": 0.7},
            ]
        },
        headers={"X-Sagewai-Quota": _quota_header(endpoint="retrieve")},
    )
    resp = await client.retrieve_blueprints(goal="x", k=2)
    assert len(resp.candidates) == 2
    assert resp.candidates[0].score > resp.candidates[1].score


@pytest.mark.asyncio
async def test_publish_blueprint_happy_path(client: SagewaiLLMClient, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/blueprints/publish",
        method="POST",
        json={"submission_id": "sub-abc", "status": "queued"},
        headers={"X-Sagewai-Quota": _quota_header(endpoint="publish")},
    )
    resp = await client.publish_blueprint(blueprint_json='{"id":"x"}', notes="initial submission")
    assert resp.submission_id == "sub-abc"


@pytest.mark.asyncio
async def test_get_feed_happy_path(client: SagewaiLLMClient, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/feed?since=2026-04-01T00:00:00Z",
        method="GET",
        json={
            "since": "2026-04-01T00:00:00Z",
            "blueprints": ['{"id":"a"}', '{"id":"b"}'],
        },
        headers={"X-Sagewai-Quota": _quota_header(endpoint="retrieve")},
    )
    resp = await client.get_feed(since="2026-04-01T00:00:00Z")
    assert len(resp.blueprints) == 2


@pytest.mark.asyncio
async def test_submit_telemetry_happy_path(client: SagewaiLLMClient, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/telemetry",
        method="POST",
        json={"ok": True},
        headers={"X-Sagewai-Quota": _quota_header(endpoint="telemetry")},
    )
    await client.submit_telemetry(type_="retrieval.miss", payload={"goal": "x"})


@pytest.mark.asyncio
async def test_run_eval_happy_path(client: SagewaiLLMClient, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/eval/run",
        method="POST",
        json={
            "eval_id": "ev-1",
            "metrics": {"accuracy": 0.91},
            "passed": True,
        },
        headers={"X-Sagewai-Quota": _quota_header(endpoint="eval")},
    )
    resp = await client.run_eval(blueprint_json='{"id":"x"}', dataset_id="ds-1")
    assert resp.passed is True


@pytest.mark.asyncio
async def test_get_quota_happy_path(client: SagewaiLLMClient, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/quota",
        method="GET",
        json={
            "tier": "anonymous",
            "endpoint": "generate",
            "used": 12,
            "limit": 50,
            "reset_at": "2026-05-01T00:00:00Z",
        },
    )
    resp = await client.get_quota()
    assert resp.used == 12


@pytest.mark.asyncio
async def test_client_sends_signed_headers(client: SagewaiLLMClient, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/blueprints/generate",
        method="POST",
        json={"blueprint_json": '{"id":"x"}', "confidence": 0.5},
        headers={"X-Sagewai-Quota": _quota_header()},
    )
    await client.generate_blueprint(goal="hello")
    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers.get("X-Sagewai-Instance") == client.identity.instance_id
    assert "X-Sagewai-Signature" in request.headers
    assert "X-Sagewai-Timestamp" in request.headers


# ── Graceful degradation ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_retrieve_returns_cached_on_quota_exceeded(
    client: SagewaiLLMClient, httpx_mock: HTTPXMock
):
    # Prime the cache with a retrieval result keyed by goal.
    cached_json = json.dumps(
        {
            "candidates": [
                {"blueprint_json": '{"id":"cached"}', "score": 0.85},
            ]
        }
    )
    client.cache.put("retrieve_run_daily_research", cached_json)

    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/blueprints/retrieve",
        method="POST",
        status_code=429,
        json={"error": "quota exceeded"},
        headers={
            "X-Sagewai-Quota": "tier=anonymous;endpoint=retrieve;used=500;limit=500;reset=2026-05-01T00:00:00Z"
        },
    )

    resp = await client.retrieve_blueprints(goal="run daily research", k=5)
    assert len(resp.candidates) == 1
    assert resp.candidates[0].blueprint_json == '{"id":"cached"}'
    assert client.last_degraded is True


@pytest.mark.asyncio
async def test_retrieve_returns_cached_on_unreachable(
    client: SagewaiLLMClient, httpx_mock: HTTPXMock
):
    cached_json = json.dumps(
        {
            "candidates": [
                {"blueprint_json": '{"id":"cached"}', "score": 0.85},
            ]
        }
    )
    client.cache.put("retrieve_offline_goal", cached_json)

    httpx_mock.add_exception(
        httpx.ConnectError("no route to host"),
        url=f"{BASE_URL}/v1/blueprints/retrieve",
    )

    resp = await client.retrieve_blueprints(goal="offline goal", k=5)
    assert resp.candidates[0].blueprint_json == '{"id":"cached"}'
    assert client.last_degraded is True


@pytest.mark.asyncio
async def test_retrieve_raises_when_cache_empty_and_quota_exceeded(
    client: SagewaiLLMClient, httpx_mock: HTTPXMock
):
    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/blueprints/retrieve",
        method="POST",
        status_code=429,
        json={"error": "quota exceeded"},
        headers={
            "X-Sagewai-Quota": "tier=anonymous;endpoint=retrieve;used=500;limit=500;reset=2026-05-01T00:00:00Z"
        },
    )
    with pytest.raises(QuotaExceeded):
        await client.retrieve_blueprints(goal="nothing cached", k=5)


@pytest.mark.asyncio
async def test_retrieve_raises_when_cache_empty_and_unreachable(
    client: SagewaiLLMClient, httpx_mock: HTTPXMock
):
    httpx_mock.add_exception(
        httpx.ConnectError("no route"),
        url=f"{BASE_URL}/v1/blueprints/retrieve",
    )
    with pytest.raises(ClientUnreachable):
        await client.retrieve_blueprints(goal="nothing cached", k=5)


@pytest.mark.asyncio
async def test_generate_does_not_fall_back_to_cache(
    client: SagewaiLLMClient, httpx_mock: HTTPXMock
):
    # Generate is a write path — no cache fallback.
    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/blueprints/generate",
        method="POST",
        status_code=429,
        json={"error": "quota exceeded"},
        headers={
            "X-Sagewai-Quota": "tier=anonymous;endpoint=generate;used=50;limit=50;reset=2026-05-01T00:00:00Z"
        },
    )
    with pytest.raises(QuotaExceeded):
        await client.generate_blueprint(goal="x")
