# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Autopilot API routes for the admin FastAPI application.

This module provides :func:`create_autopilot_router` which returns an
:class:`APIRouter` that is included by :func:`create_admin_serve_app`
under the ``/api/v1`` prefix.

Routes
------
``GET  /api/v1/autopilot/status``
    Returns the current autopilot state: enabled flag, tier, stored
    instance ID (secret never exposed), and a quota snapshot.

``POST /api/v1/autopilot/enable``
    Body: ``{"tier": "anonymous" | "free" | "premium" | "skip"}``
    Sets ``enabled=True`` and persists the tier.  Generates and stores
    a new :class:`InstanceIdentity` if one does not already exist.

``POST /api/v1/autopilot/disable``
    Sets ``enabled=False``.  The identity is preserved so the same
    instance ID is used when autopilot is re-enabled.

``POST /api/v1/autopilot/goal``
    Body: ``{"goal": "<plain English goal>"}``
    Builds a :class:`GoalRouter` from the stored config and runs it.
    Returns the :class:`RoutingResult` as JSON.  Degrades gracefully to
    ``synthesis_needed`` when the hosted service is unreachable (the
    standard open-source behavior).

``POST /api/v1/autopilot/approve``
    Body: ``{"result": <AutoRouted JSON>, "project_id": "<slug>"}``
    Accepts an ``AutoRouted`` routing result, creates a mission record,
    persists it, and returns the mission dict.  Returns 422 if the
    result kind is not ``auto_routed``.

``GET  /api/v1/autopilot/missions``
    Returns the list of stored mission records, project-scoped via the
    ``X-Project-ID`` header.

All routes require a valid ``sagewai_auth`` cookie (or Bearer token in
the ``Authorization`` header).  Missing / invalid auth returns 401.
"""

from __future__ import annotations

import datetime
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from sagewai.admin.autopilot_state import (
    AdminStateIdentityStore,
    get_autopilot_config,
    get_autopilot_identity,
    list_missions,
    save_mission,
    set_autopilot_config,
)
from sagewai.admin.serve import _extract_token, _project_id
from sagewai.admin.state_file import AdminStateFile
from sagewai.autopilot.routing import ConfidenceConfig, GoalRouter, RoutingResult
from sagewai.autopilot.sagewai_llm import BlueprintCache, SagewaiLLMClient
from sagewai.autopilot.sagewai_llm.identity import ensure_identity

logger = logging.getLogger("sagewai.admin.autopilot")

_VALID_TIERS = frozenset({"anonymous", "free", "premium", "skip"})


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _require_auth(request: Request, sf: AdminStateFile) -> JSONResponse | None:
    """Return a 401 JSONResponse if the request is not authenticated, else None."""
    token = _extract_token(request)
    if not token:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    user = sf.get_user_by_token(token)
    if user is None:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=401)
    return None


def create_autopilot_router(sf: AdminStateFile) -> APIRouter:
    """Return an :class:`APIRouter` with all autopilot routes.

    Parameters
    ----------
    sf:
        The admin state file store.  All autopilot state is persisted
        inside the same JSON file used by the rest of the admin panel.
    """
    router = APIRouter(tags=["autopilot"])

    # ── GET /autopilot/status ─────────────────────────────────────────

    @router.get("/autopilot/status")
    async def autopilot_status(request: Request) -> JSONResponse:
        """Return the current autopilot configuration and identity summary."""
        err = _require_auth(request, sf)
        if err is not None:
            return err

        config = get_autopilot_config(sf)
        identity = get_autopilot_identity(sf)
        return JSONResponse(
            {
                "enabled": config.get("enabled", False),
                "tier": config.get("tier", "anonymous"),
                "instance_id": identity.instance_id if identity else None,
                "base_url": config.get("base_url"),
                "confidence_high": config.get("confidence_high"),
                "confidence_low": config.get("confidence_low"),
                "cache_ttl_seconds": config.get("cache_ttl_seconds"),
            }
        )

    # ── POST /autopilot/enable ────────────────────────────────────────

    @router.post("/autopilot/enable")
    async def autopilot_enable(request: Request) -> JSONResponse:
        """Enable autopilot and persist a chosen tier."""
        err = _require_auth(request, sf)
        if err is not None:
            return err

        body: dict[str, Any] = await request.json()
        tier = body.get("tier", "anonymous")
        if tier not in _VALID_TIERS:
            return JSONResponse(
                {"ok": False, "error": f"Invalid tier '{tier}'. Valid: {sorted(_VALID_TIERS)}"},
                status_code=422,
            )

        # Ensure an identity exists.
        store = AdminStateIdentityStore(sf)
        identity = ensure_identity(store)

        set_autopilot_config(sf, {"enabled": True, "tier": tier})

        logger.info("Autopilot enabled (tier=%s, instance_id=%s)", tier, identity.instance_id)
        return JSONResponse(
            {
                "ok": True,
                "enabled": True,
                "tier": tier,
                "instance_id": identity.instance_id,
            }
        )

    # ── POST /autopilot/disable ───────────────────────────────────────

    @router.post("/autopilot/disable")
    async def autopilot_disable(request: Request) -> JSONResponse:
        """Disable autopilot (identity is preserved)."""
        err = _require_auth(request, sf)
        if err is not None:
            return err

        set_autopilot_config(sf, {"enabled": False})
        logger.info("Autopilot disabled")
        return JSONResponse({"ok": True, "enabled": False})

    # ── POST /autopilot/goal ──────────────────────────────────────────

    @router.post("/autopilot/goal")
    async def autopilot_goal(request: Request) -> JSONResponse:
        """Run GoalRouter against a plain-English goal string.

        Degrades gracefully to ``synthesis_needed`` when the hosted
        Sagewai LLM service is unreachable.
        """
        err = _require_auth(request, sf)
        if err is not None:
            return err

        body: dict[str, Any] = await request.json()
        goal: str = body.get("goal", "").strip()
        if not goal:
            return JSONResponse(
                {"ok": False, "error": "goal is required and must not be empty"},
                status_code=422,
            )

        config = get_autopilot_config(sf)
        store = AdminStateIdentityStore(sf)
        identity = ensure_identity(store)

        cache_dir = Path(
            os.environ.get("SAGEWAI_CACHE_DIR", Path.home() / ".sagewai" / "blueprint_cache")
        )
        cache = BlueprintCache(
            cache_dir,
            ttl_seconds=int(config.get("cache_ttl_seconds", 3600)),
        )
        client = SagewaiLLMClient(
            identity=identity,
            cache=cache,
            base_url=config.get("base_url", "https://llm.sagewai.ai"),
        )
        routing_config = ConfidenceConfig(
            auto_route_threshold=config.get("confidence_high", 0.85),
            picker_threshold=config.get("confidence_low", 0.65),
        )
        router_obj = GoalRouter(client=client, config=routing_config)

        result: RoutingResult = await router_obj.route(goal)
        # Frontend expects `routing_result` as the discriminator field name;
        # the Pydantic models emit `kind`. Translate at the boundary.
        payload = result.model_dump()
        if "kind" in payload and "routing_result" not in payload:
            payload["routing_result"] = payload.pop("kind")
        return JSONResponse(payload)

    # ── POST /autopilot/approve ───────────────────────────────────────

    @router.post("/autopilot/approve")
    async def autopilot_approve(request: Request) -> JSONResponse:
        """Approve an AutoRouted result and create a mission record.

        Body fields:

        ``result``
            The :class:`AutoRouted` JSON object returned by ``/goal``.
        ``project_id`` *(optional)*
            Project scope; falls back to the ``X-Project-ID`` header.
        """
        err = _require_auth(request, sf)
        if err is not None:
            return err

        body: dict[str, Any] = await request.json()
        result = body.get("result", {})
        kind = result.get("kind")
        if kind != "auto_routed":
            return JSONResponse(
                {
                    "ok": False,
                    "error": (f"Only 'auto_routed' results can be approved; got '{kind}'."),
                },
                status_code=422,
            )

        pid = body.get("project_id") or _project_id(request)
        mission: dict[str, Any] = {
            "mission_id": secrets.token_hex(12),
            "project_id": pid,
            "status": "pending",
            "created_at": _now_iso(),
            "goal_preview": result.get("preview", ""),
            "slots": result.get("slots", {}),
            "blueprint_json": result.get("ranked", {}).get("blueprint_json", ""),
            "score": result.get("ranked", {}).get("score"),
        }
        saved = save_mission(sf, mission)
        logger.info("Mission created (id=%s, project=%s)", saved["mission_id"], pid)
        return JSONResponse({"ok": True, "mission": saved}, status_code=201)

    # ── GET /autopilot/missions ───────────────────────────────────────

    @router.get("/autopilot/missions")
    async def autopilot_missions(request: Request) -> JSONResponse:
        """List stored autopilot missions, optionally scoped to a project."""
        err = _require_auth(request, sf)
        if err is not None:
            return err

        pid = _project_id(request)
        missions = list_missions(sf, project_id=pid)
        return JSONResponse({"missions": missions, "count": len(missions)})

    return router
