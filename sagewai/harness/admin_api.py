# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Admin CRUD endpoints for the LLM Harness.

Provides REST endpoints for managing harness configuration, policies,
keys, spend analytics, and audit events. Intended to be mounted in
the admin backend under ``/api/v1/harness``.

Usage::

    from sagewai.harness.admin_api import create_harness_admin_router

    router = create_harness_admin_router(
        store=store, classifier=classifier, config=config,
    )
    app.include_router(router, prefix="/api/v1/harness")
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from sagewai.harness.classifier import RequestClassifier
from sagewai.harness.models import (
    HarnessConfig,
    HarnessKey,
    PolicyRule,
)
from sagewai.harness.store import InMemoryHarnessStore

logger = logging.getLogger(__name__)


# ── Request / response models ────────────────────────────────────────


class CreateKeyRequest(BaseModel):
    """Request body for creating a harness key."""

    name: str = ""
    user_id: str = ""
    org_id: str = "default"
    team_id: str | None = None
    project_id: str | None = None
    allowed_models: list[str] = []
    max_budget_daily_usd: float | None = None
    max_budget_monthly_usd: float | None = None
    expires_at: float | None = None


class CreateKeyResponse(BaseModel):
    """Response body for key creation — includes the plaintext key once."""

    key_id: str
    plaintext: str
    name: str
    key_suffix: str


class TestClassifyRequest(BaseModel):
    """Request body for dry-run classification."""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    model: str = ""


# ── Router factory ───────────────────────────────────────────────────


def create_harness_admin_router(
    store: InMemoryHarnessStore,
    classifier: RequestClassifier,
    config: HarnessConfig,
) -> APIRouter:
    """Create a FastAPI router with admin CRUD endpoints for the harness.

    Endpoints (all under the mounted prefix):

    **Policies**
    - ``GET  /policies``          — list policies
    - ``POST /policies``          — create policy
    - ``GET  /policies/{id}``     — get policy
    - ``PUT  /policies/{id}``     — update policy
    - ``DELETE /policies/{id}``   — delete policy

    **Keys**
    - ``GET  /keys``              — list keys
    - ``POST /keys``              — create key (returns plaintext once)
    - ``DELETE /keys/{id}``       — revoke key

    **Spend**
    - ``GET /spend``              — spend summary
    - ``GET /spend/breakdown``    — spend by model

    **Audit**
    - ``GET /audit``              — audit events

    **Config**
    - ``GET  /config``            — get global config
    - ``PUT  /config``            — update global config

    **Classification**
    - ``POST /test-classify``     — dry-run classification

    Args:
        store: Harness store for policies, keys, spend, and audit.
        classifier: Request complexity classifier.
        config: Global harness configuration (mutable reference).

    Returns:
        A FastAPI :class:`APIRouter` ready to mount.
    """
    router = APIRouter(tags=["harness-admin"])

    # TODO: Add rate limiting middleware

    # ── Policies ─────────────────────────────────────────────────

    @router.get("/policies")
    async def list_policies(
        org_id: str | None = Query(None),
    ) -> list[PolicyRule]:
        """List all harness policies."""
        return await store.list_policies(org_id=org_id)

    @router.post("/policies", status_code=201)
    async def create_policy(body: PolicyRule) -> PolicyRule:
        """Create a new harness policy."""
        return await store.create_policy(body)

    @router.get("/policies/{policy_id}")
    async def get_policy(policy_id: str) -> PolicyRule:
        """Get a harness policy by ID."""
        policy = await store.get_policy(policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="Policy not found")
        return policy

    @router.put("/policies/{policy_id}")
    async def update_policy(policy_id: str, body: PolicyRule) -> PolicyRule:
        """Update an existing harness policy."""
        updated = await store.update_policy(policy_id, body)
        if updated is None:
            raise HTTPException(status_code=404, detail="Policy not found")
        return updated

    @router.delete("/policies/{policy_id}", status_code=204)
    async def delete_policy(policy_id: str) -> None:
        """Delete a harness policy."""
        deleted = await store.delete_policy(policy_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Policy not found")

    # ── Keys ─────────────────────────────────────────────────────

    @router.get("/keys")
    async def list_keys(
        org_id: str | None = Query(None),
    ) -> list[HarnessKey]:
        """List all harness keys (hashes only, no plaintext)."""
        return await store.list_keys(org_id=org_id)

    @router.post("/keys", status_code=201)
    async def create_key(body: CreateKeyRequest) -> CreateKeyResponse:
        """Create a new harness key.

        The plaintext key is returned **once** in the response. It
        cannot be retrieved again after creation.
        """
        key = HarnessKey(
            name=body.name,
            user_id=body.user_id,
            org_id=body.org_id,
            team_id=body.team_id,
            project_id=body.project_id,
            allowed_models=body.allowed_models,
            max_budget_daily_usd=body.max_budget_daily_usd,
            max_budget_monthly_usd=body.max_budget_monthly_usd,
            expires_at=body.expires_at,
        )
        plaintext = await store.create_key(key)
        return CreateKeyResponse(
            key_id=key.id,
            plaintext=plaintext,
            name=key.name,
            key_suffix=key.key_suffix,
        )

    @router.delete("/keys/{key_id}", status_code=204)
    async def revoke_key(key_id: str) -> None:
        """Revoke a harness key (disables it, does not delete)."""
        revoked = await store.revoke_key(key_id)
        if not revoked:
            raise HTTPException(status_code=404, detail="Key not found")

    # ── Spend ────────────────────────────────────────────────────

    @router.get("/spend")
    async def get_spend_summary(
        org_id: str | None = Query(None),
        user_id: str | None = Query(None),
    ) -> dict[str, Any]:
        """Get aggregated spend summary.

        Returns daily, monthly, and total cost/request counts.
        """
        return await store.get_spend_summary(
            org_id=org_id, user_id=user_id,
        )

    @router.get("/spend/breakdown")
    async def get_spend_breakdown(
        org_id: str | None = Query(None),
        since: float | None = Query(None),
    ) -> dict[str, dict[str, Any]]:
        """Get spend breakdown by model.

        Each key is a model name with cost, request count, and token
        totals.
        """
        return await store.get_spend_by_model(
            org_id=org_id, since=since,
        )

    # ── Audit ────────────────────────────────────────────────────

    @router.get("/audit")
    async def get_audit_events(
        event_type: str | None = Query(None),
        limit: int = Query(100, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        """Get audit events with optional filtering."""
        events = await store.get_audit(
            event_type=event_type, limit=limit,
        )
        return [e.model_dump() for e in events]

    # ── Config ───────────────────────────────────────────────────

    @router.get("/config")
    async def get_config() -> HarnessConfig:
        """Get the global harness configuration."""
        return config

    @router.put("/config")
    async def update_config(body: HarnessConfig) -> HarnessConfig:
        """Update the global harness configuration.

        Replaces the entire config object. Fields not provided in the
        request body will use their default values.
        """
        # Mutate the shared config reference in place.
        config.enabled = body.enabled
        config.default_tier_config = body.default_tier_config
        config.default_action_on_budget_exceeded = (
            body.default_action_on_budget_exceeded
        )
        config.allow_model_override = body.allow_model_override
        config.log_requests = body.log_requests
        config.transparency_headers = body.transparency_headers
        return config

    # ── Classification dry-run ───────────────────────────────────

    @router.post("/test-classify")
    async def test_classify(body: TestClassifyRequest) -> dict[str, Any]:
        """Dry-run request classification.

        Returns the classification result without routing or forwarding
        the request. Useful for testing policy and tier configuration.
        """
        result = classifier.classify(
            body.messages,
            tools=body.tools,
            model=body.model,
        )
        return {
            "tier": result.tier.value,
            "score": result.score,
            "confidence": result.confidence,
            "reason": result.reason,
            "signals": result.signals,
        }

    return router
