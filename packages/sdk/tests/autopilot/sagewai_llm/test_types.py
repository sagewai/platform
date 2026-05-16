# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for Sagewai LLM client request/response models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.autopilot.sagewai_llm.types import (
    FeedResponse,
    GenerateBlueprintRequest,
    GenerateBlueprintResponse,
    PublishBlueprintRequest,
    PublishBlueprintResponse,
    QuotaResponse,
    RetrieveBlueprintsRequest,
    RetrieveBlueprintsResponse,
    RunEvalRequest,
    RunEvalResponse,
    TelemetryEvent,
)


def test_generate_request_requires_goal():
    with pytest.raises(ValidationError):
        GenerateBlueprintRequest()  # type: ignore[call-arg]


def test_generate_request_rejects_blank_goal():
    with pytest.raises(ValidationError):
        GenerateBlueprintRequest(goal="")


def test_generate_response_carries_blueprint_json_string():
    resp = GenerateBlueprintResponse(
        blueprint_json='{"id":"x"}',
        confidence=0.8,
    )
    assert "id" in resp.blueprint_json
    assert 0.0 <= resp.confidence <= 1.0


def test_retrieve_request_defaults_k_to_5():
    req = RetrieveBlueprintsRequest(goal="do a thing")
    assert req.k == 5


def test_retrieve_request_k_must_be_positive():
    with pytest.raises(ValidationError):
        RetrieveBlueprintsRequest(goal="x", k=0)


def test_retrieve_response_carries_scored_candidates():
    resp = RetrieveBlueprintsResponse(
        candidates=(
            {"blueprint_json": '{"id":"a"}', "score": 0.9},
            {"blueprint_json": '{"id":"b"}', "score": 0.7},
        ),
    )
    assert len(resp.candidates) == 2
    assert resp.candidates[0].score > resp.candidates[1].score


def test_publish_round_trip():
    req = PublishBlueprintRequest(blueprint_json='{"id":"x"}', notes="hi")
    dumped = req.model_dump_json()
    restored = PublishBlueprintRequest.model_validate_json(dumped)
    assert restored == req


def test_publish_response_carries_status():
    resp = PublishBlueprintResponse(submission_id="sub-abc", status="queued")
    assert resp.status == "queued"


def test_feed_response_with_timestamps():
    resp = FeedResponse(
        since="2026-04-16T00:00:00Z",
        blueprints=(),
    )
    assert resp.since == "2026-04-16T00:00:00Z"


def test_telemetry_event_requires_type():
    with pytest.raises(ValidationError):
        TelemetryEvent()  # type: ignore[call-arg]


def test_telemetry_event_keeps_opaque_payload():
    ev = TelemetryEvent(type="retrieval.miss", payload={"goal": "x", "k": 5})
    assert ev.payload["goal"] == "x"


def test_run_eval_request_and_response():
    req = RunEvalRequest(blueprint_json='{"id":"x"}', dataset_id="ds-1")
    resp = RunEvalResponse(
        eval_id="ev-1",
        metrics={"accuracy": 0.91},
        passed=True,
    )
    assert req.dataset_id == "ds-1"
    assert resp.passed is True


def test_quota_response_carries_usage_and_limit():
    q = QuotaResponse(
        tier="anonymous",
        endpoint="generate",
        used=12,
        limit=50,
        reset_at="2026-05-01T00:00:00Z",
    )
    assert q.used < q.limit
