# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Request and response pydantic v2 models for every Sagewai LLM endpoint.

These are intentionally flat: the server returns blueprints as
serialized JSON strings rather than nested pydantic models, so the
client can hand them directly to
``Blueprint.model_validate_json(...)`` when the caller wants the real
object. That keeps the transport layer decoupled from the Plan 1
framework types.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# ── Generate ──────────────────────────────────────────────────────


class GenerateBlueprintRequest(BaseModel):
    """Request body for ``POST /v1/blueprints/generate``."""

    model_config = ConfigDict(frozen=True)

    goal: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class GenerateBlueprintResponse(BaseModel):
    """Response body for ``POST /v1/blueprints/generate``."""

    model_config = ConfigDict(frozen=True)

    blueprint_json: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    quality_tier: Optional[str] = None
    latency_ms: Optional[float] = None


# ── Retrieve ──────────────────────────────────────────────────────


class RetrieveCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    blueprint_json: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    quality_tier: Optional[str] = None


class RetrieveBlueprintsRequest(BaseModel):
    """Request body for ``POST /v1/blueprints/retrieve``."""

    model_config = ConfigDict(frozen=True)

    goal: str = Field(min_length=1)
    k: int = Field(default=5, gt=0, le=25)


class RetrieveBlueprintsResponse(BaseModel):
    """Response body for ``POST /v1/blueprints/retrieve``."""

    model_config = ConfigDict(frozen=True)

    candidates: tuple[RetrieveCandidate, ...]
    routing_decision: Optional[dict[str, Any]] = None


# ── Publish ───────────────────────────────────────────────────────


class PublishBlueprintRequest(BaseModel):
    """Request body for ``POST /v1/blueprints/publish``."""

    model_config = ConfigDict(frozen=True)

    blueprint_json: str = Field(min_length=1)
    notes: str | None = None


class PublishBlueprintResponse(BaseModel):
    """Response body for ``POST /v1/blueprints/publish``."""

    model_config = ConfigDict(frozen=True)

    submission_id: str
    status: str  # "queued" | "accepted" | "rejected"


# ── Feed ──────────────────────────────────────────────────────────


class FeedResponse(BaseModel):
    """Response body for ``GET /v1/feed?since=<ts>``."""

    model_config = ConfigDict(frozen=True)

    since: str
    blueprints: tuple[str, ...]  # each element is a Blueprint JSON string


# ── Telemetry ─────────────────────────────────────────────────────


class TelemetryEvent(BaseModel):
    """Request body for ``POST /v1/telemetry``.

    Deliberately opaque — the schema of ``payload`` is owned by the
    server, not the client.
    """

    model_config = ConfigDict(frozen=True)

    type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


# ── Eval ──────────────────────────────────────────────────────────


class RunEvalRequest(BaseModel):
    """Request body for ``POST /v1/eval/run``."""

    model_config = ConfigDict(frozen=True)

    blueprint_json: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)


class RunEvalResponse(BaseModel):
    """Response body for ``POST /v1/eval/run``."""

    model_config = ConfigDict(frozen=True)

    eval_id: str
    metrics: dict[str, float]
    passed: bool
    tier_metrics: Optional[dict[str, Any]] = None


# ── Quota ─────────────────────────────────────────────────────────


class QuotaResponse(BaseModel):
    """Response body for ``GET /v1/quota``."""

    model_config = ConfigDict(frozen=True)

    tier: str  # "anonymous" | "free" | "starter" | ...
    endpoint: str  # "generate" | "retrieve" | "eval" | ...
    used: int = Field(ge=0)
    limit: int = Field(ge=0)
    reset_at: str  # ISO 8601
