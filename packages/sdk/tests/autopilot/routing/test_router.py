"""End-to-end tests for GoalRouter via pytest-httpx mocks."""

from __future__ import annotations

from typing import Any

import pytest

from sagewai.autopilot.routing.confidence import ConfidenceConfig
from sagewai.autopilot.routing.extractor import RuleBasedExtractor
from sagewai.autopilot.routing.router import GoalRouter
from sagewai.autopilot.routing.types import AutoRouted, PickerNeeded, SynthesisNeeded
from sagewai.autopilot.sagewai_llm import (
    BlueprintCache,
    FileIdentityStore,
    SagewaiLLMClient,
    ensure_identity,
)
from tests.autopilot.fixtures import (
    make_synthetic_batch_blueprint,
    make_synthetic_event_driven_blueprint,
    make_synthetic_scheduled_blueprint,
)

# ── Helpers ────────────────────────────────────────────────────────


def _bp_json(factory) -> str:
    return factory().model_dump_json()


def _retrieve_response(candidates: list[tuple[str, float]]) -> dict[str, Any]:
    """Build a JSON-serialisable retrieve response payload."""
    return {
        "candidates": [{"blueprint_json": bp_json, "score": score} for bp_json, score in candidates]
    }


@pytest.fixture()
def tmp_store(tmp_path):
    store = FileIdentityStore(tmp_path / "identity.json")
    ensure_identity(store)
    return store


@pytest.fixture()
def tmp_cache(tmp_path):
    return BlueprintCache(tmp_path / "cache.json", ttl_seconds=3600)


@pytest.fixture()
def client(tmp_store, tmp_cache) -> SagewaiLLMClient:
    return SagewaiLLMClient(
        base_url="https://api.sagewai.ai",
        identity=tmp_store.load(),
        cache=tmp_cache,
    )


@pytest.fixture()
def router(client) -> GoalRouter:
    return GoalRouter(
        client=client,
        config=ConfidenceConfig(auto_route_threshold=0.85, picker_threshold=0.65),
        extractor=RuleBasedExtractor(),
    )


# ── AUTO_ROUTE band ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_auto_routed_above_threshold(httpx_mock, router):
    scheduled_json = _bp_json(make_synthetic_scheduled_blueprint)
    payload = _retrieve_response([(scheduled_json, 0.92)])
    httpx_mock.add_response(
        method="POST",
        url="https://api.sagewai.ai/v1/blueprints/retrieve",
        json=payload,
        status_code=200,
    )

    result = await router.route("monitor vendors=openai,anthropic daily")
    assert isinstance(result, AutoRouted)
    assert result.kind == "auto_routed"
    assert result.ranked.score == pytest.approx(0.92)
    assert isinstance(result.preview, str)
    assert len(result.preview) > 0


@pytest.mark.asyncio
async def test_route_auto_routed_slots_extracted(httpx_mock, router):
    scheduled_json = _bp_json(make_synthetic_scheduled_blueprint)
    payload = _retrieve_response([(scheduled_json, 0.90)])
    httpx_mock.add_response(
        method="POST",
        url="https://api.sagewai.ai/v1/blueprints/retrieve",
        json=payload,
        status_code=200,
    )

    result = await router.route("vendors=https://openai.com schedule=0 8 * * 1-5")
    assert isinstance(result, AutoRouted)
    assert result.slots.get("vendors") == "https://openai.com"


@pytest.mark.asyncio
async def test_route_auto_routed_preview_contains_title(httpx_mock, router):
    bp = make_synthetic_scheduled_blueprint()
    payload = _retrieve_response([(bp.model_dump_json(), 0.91)])
    httpx_mock.add_response(
        method="POST",
        url="https://api.sagewai.ai/v1/blueprints/retrieve",
        json=payload,
        status_code=200,
    )

    result = await router.route("run research")
    assert isinstance(result, AutoRouted)
    assert bp.title in result.preview


# ── PICKER band ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_picker_needed_in_middle_band(httpx_mock, router):
    bp1 = make_synthetic_scheduled_blueprint().model_dump_json()
    bp2 = make_synthetic_event_driven_blueprint().model_dump_json()
    bp3 = make_synthetic_batch_blueprint().model_dump_json()
    payload = _retrieve_response([(bp1, 0.80), (bp2, 0.74), (bp3, 0.68)])
    httpx_mock.add_response(
        method="POST",
        url="https://api.sagewai.ai/v1/blueprints/retrieve",
        json=payload,
        status_code=200,
    )

    result = await router.route("process documents and route results")
    assert isinstance(result, PickerNeeded)
    assert result.kind == "picker_needed"
    assert len(result.candidates) <= 3


@pytest.mark.asyncio
async def test_route_picker_candidates_capped_at_top_k(httpx_mock, client):
    """GoalRouter with picker_top_k=2 should return at most 2 candidates."""
    router_k2 = GoalRouter(
        client=client,
        config=ConfidenceConfig(
            auto_route_threshold=0.85,
            picker_threshold=0.65,
            picker_top_k=2,
        ),
        extractor=RuleBasedExtractor(),
    )
    bp1 = make_synthetic_scheduled_blueprint().model_dump_json()
    bp2 = make_synthetic_event_driven_blueprint().model_dump_json()
    bp3 = make_synthetic_batch_blueprint().model_dump_json()
    payload = _retrieve_response([(bp1, 0.80), (bp2, 0.74), (bp3, 0.68)])
    httpx_mock.add_response(
        method="POST",
        url="https://api.sagewai.ai/v1/blueprints/retrieve",
        json=payload,
        status_code=200,
    )

    result = await router_k2.route("classify events")
    assert isinstance(result, PickerNeeded)
    assert len(result.candidates) == 2


# ── SYNTHESIZE band ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_synthesis_needed_below_threshold(httpx_mock, router):
    bp1 = make_synthetic_scheduled_blueprint().model_dump_json()
    payload = _retrieve_response([(bp1, 0.30)])
    httpx_mock.add_response(
        method="POST",
        url="https://api.sagewai.ai/v1/blueprints/retrieve",
        json=payload,
        status_code=200,
    )

    result = await router.route("invent a completely new kind of agent")
    assert isinstance(result, SynthesisNeeded)
    assert result.kind == "synthesis_needed"
    assert result.goal == "invent a completely new kind of agent"


@pytest.mark.asyncio
async def test_route_synthesis_needed_empty_candidates(httpx_mock, router):
    payload = _retrieve_response([])
    httpx_mock.add_response(
        method="POST",
        url="https://api.sagewai.ai/v1/blueprints/retrieve",
        json=payload,
        status_code=200,
    )

    result = await router.route("totally novel goal")
    assert isinstance(result, SynthesisNeeded)


# ── Error / degradation paths ─────────────────────────────────────


@pytest.mark.asyncio
async def test_route_synthesis_on_client_unreachable(httpx_mock, router):
    """When the service is unreachable, route falls back to SynthesisNeeded."""
    httpx_mock.add_exception(
        Exception("connection refused"),
        url="https://api.sagewai.ai/v1/blueprints/retrieve",
    )

    result = await router.route("do something when network is down")
    assert isinstance(result, SynthesisNeeded)
    assert result.goal == "do something when network is down"


@pytest.mark.asyncio
async def test_route_synthesis_on_service_error(httpx_mock, router):
    httpx_mock.add_response(
        method="POST",
        url="https://api.sagewai.ai/v1/blueprints/retrieve",
        status_code=503,
        text="Service Unavailable",
    )

    result = await router.route("route when service returns 503")
    assert isinstance(result, SynthesisNeeded)


# ── Goal string passed through ────────────────────────────────────


@pytest.mark.asyncio
async def test_route_goal_preserved_in_synthesis_needed(httpx_mock, router):
    payload = _retrieve_response([])
    httpx_mock.add_response(
        method="POST",
        url="https://api.sagewai.ai/v1/blueprints/retrieve",
        json=payload,
        status_code=200,
    )
    goal = "unique goal string xyz123"
    result = await router.route(goal)
    assert isinstance(result, SynthesisNeeded)
    assert result.goal == goal
