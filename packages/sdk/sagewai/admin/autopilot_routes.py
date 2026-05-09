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
    ``X-Project-ID`` header.  Each item is enriched with full blueprint
    metadata (same shape as the detail endpoint) so the list can render
    a preview without a second fetch.

``GET  /api/v1/autopilot/missions/{mission_id}``
    Returns the full mission record enriched with blueprint metadata:
    agent graph, tools, providers, slots, success criteria, training
    hooks, description, and estimated_cost.

``GET  /api/v1/autopilot/missions/events``
    Server-sent events stream.  Emits ``mission.status_changed`` events
    whenever a mission status changes.  Clients receive all events for
    the current org regardless of project scope.

``POST /api/v1/autopilot/missions/{mission_id}/explain``
    Returns a templated markdown brief built from the mission's blueprint
    metadata.  v1.0 is pure templating — no LLM calls.  The brief has
    four fixed sections: "What this will do", "Resources allocated",
    "How to run", "How to debug".

``POST /api/v1/autopilot/missions/{mission_id}/run``
    Spawns the background mission driver for a PENDING mission.
    Returns ``{"run_id": "...", "started_at": "..."}`` with HTTP 202.
    Execution proceeds in a detached task; progress streams via the
    SSE trace endpoint and is persisted into the mission's ``trace``.

``POST /api/v1/autopilot/missions/{mission_id}/cancel``
    Body: ``{"reason": "<string>"}``
    Cancels a RUNNING mission with cooperative cancellation.

All routes require a valid ``sagewai_auth`` cookie (or Bearer token in
the ``Authorization`` header).  Missing / invalid auth returns 401.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from sagewai.admin.autopilot_explain import render_brief
from sagewai.autopilot.tool_risk_profile import SandboxTier, get_tier, is_downgrade, tier_for_tools
from sagewai.admin.autopilot_lifecycle import (
    IllegalTransition,
    MissionStatus,
    transition_mission,
)
from sagewai.admin.autopilot_lifecycle_bus import MissionStatusChanged, get_lifecycle_bus
from sagewai.admin.autopilot_run_bus import get_run_bus
from sagewai.admin.autopilot_run_observer import run_mission_with_observer
from sagewai.admin.autopilot_state import (
    AdminStateIdentityStore,
    get_autopilot_config,
    get_autopilot_identity,
    get_mission,
    list_missions,
    save_mission,
    set_autopilot_config,
    update_mission,
)
from sagewai.admin.serve import _extract_token, _project_id
from sagewai.admin.state_file import AdminStateFile
from sagewai.autopilot._types import MissionState
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.controller.driver import MissionDriver
from sagewai.autopilot.mission import Mission
from sagewai.autopilot.routing import ConfidenceConfig, GoalRouter, RoutingResult
from sagewai.autopilot.sagewai_llm import BlueprintCache, SagewaiLLMClient
from sagewai.autopilot.sagewai_llm.identity import ensure_identity

logger = logging.getLogger("sagewai.admin.autopilot")

_VALID_TIERS = frozenset({"anonymous", "free", "premium", "skip"})

# Module-level fleet registry singleton (one per process).  Tests can patch
# _get_fleet_registry_snapshot to inject a fake snapshot.
_fleet_registry: object | None = None


async def _get_fleet_registry_snapshot() -> list:
    """Return the list of WorkerRecord objects from the global fleet registry.

    In production, this returns the workers registered with the admin's
    in-process :class:`~sagewai.fleet.registry.InMemoryFleetRegistry`.
    Tests override this function via ``unittest.mock.patch``.
    """
    from sagewai.fleet.registry import InMemoryFleetRegistry

    global _fleet_registry  # noqa: PLW0603
    if _fleet_registry is None:
        _fleet_registry = InMemoryFleetRegistry()
    registry = _fleet_registry
    # InMemoryFleetRegistry.list_workers requires org_id; use "default" for the
    # single-tenant admin.
    try:
        return await registry.list_workers(org_id="default")
    except Exception:
        return []


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _org_id(sf: AdminStateFile) -> str:
    """Return the org slug used as the bus key (single-tenant admin panel)."""
    data = sf._read()
    return data.get("org_slug") or "default"


def _require_auth(request: Request, sf: AdminStateFile) -> JSONResponse | None:
    """Return a 401 JSONResponse if the request is not authenticated, else None."""
    token = _extract_token(request)
    if not token:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    user = sf.get_user_by_token(token)
    if user is None:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=401)
    return None


async def _transition_and_publish(
    sf: AdminStateFile,
    mission_id: str,
    new_status: MissionStatus,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Apply a lifecycle transition and publish the event to the bus."""
    current = get_mission(sf, mission_id)
    old_status = current["status"] if current else "unknown"
    result = transition_mission(sf, mission_id, new_status, reason=reason)
    bus = get_lifecycle_bus()
    org = _org_id(sf)
    await bus.publish(
        org,
        MissionStatusChanged(
            mission_id=mission_id,
            old_status=old_status,
            new_status=new_status.value,
            ts=datetime.datetime.now(datetime.timezone.utc),
        ),
    )
    return result


class CancelBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class SandboxOverrideBody(BaseModel):
    step_id: str
    tier: str  # "TRUSTED" | "SANDBOXED" | "UNTRUSTED"


class AutopilotMissionDetail(BaseModel):
    """Mission record enriched with blueprint metadata for the detail page."""

    id: str
    mission_id: str  # Mirrors ``id`` — kept for backward-compat with existing API consumers.
    status: str
    goal_text: str
    created_at: str
    updated_at: str
    project_id: str | None = None
    blueprint_id: str | None = None
    description: str = ""
    agent_graph_json: dict[str, Any] = Field(default_factory=lambda: {"nodes": [], "edges": []})
    tools_required: list[dict[str, Any]] = Field(default_factory=list)
    providers_required: list[dict[str, Any]] = Field(default_factory=list)
    slots: dict[str, Any] = Field(default_factory=dict)
    success_criteria: list[dict[str, Any]] = Field(default_factory=list)
    training_data_hooks: list[dict[str, Any]] = Field(default_factory=list)
    estimated_cost: dict[str, Any] | None = None


def _translate_mission_detail(mission: dict[str, Any]) -> AutopilotMissionDetail:
    """Translate a raw mission dict into an enriched :class:`AutopilotMissionDetail`.

    Parses ``blueprint_json`` from the mission record into typed
    :class:`~sagewai.autopilot.blueprint.Blueprint` fields.  Falls back
    to empty defaults on missing or invalid blueprint JSON.
    """
    blueprint_json = mission.get("blueprint_json") or ""

    # Default / empty values used when blueprint is absent or unparseable.
    blueprint_id: str | None = None
    description: str = ""
    agent_graph_json: dict[str, Any] = {"nodes": [], "edges": []}
    tools_required: list[dict[str, Any]] = []
    providers_required: list[dict[str, Any]] = []
    success_criteria: list[dict[str, Any]] = []
    training_data_hooks: list[dict[str, Any]] = []

    if blueprint_json:
        try:
            bp = Blueprint.model_validate_json(blueprint_json)
            blueprint_id = bp.id
            description = bp.description

            # Agent graph — nodes and edges.
            if bp.agent_graph is not None:
                nodes = [
                    {
                        "id": agent.id,
                        "role": agent.role or agent.id,
                        "kind": agent.kind.value if hasattr(agent.kind, "value") else str(agent.kind),
                        "tools": list(agent.tools),
                        "prompt_ref": agent.prompt_ref,
                    }
                    for agent in bp.agent_graph.nodes
                ]
                edges = [
                    {"from": src, "to": dst}
                    for src, dst in bp.agent_graph.edges
                ]
                agent_graph_json = {"nodes": nodes, "edges": edges}

            # Tools required.
            tools_required = [{"name": s} for s in bp.tools_required]

            # Providers required.
            providers_required = [
                {
                    "role": p.role,
                    "capability": p.capability,
                    "tier": p.tier,
                    "name": p.role,
                }
                for p in bp.providers_required
            ]

            # Success criteria — flatten EvalRef metrics.
            success_criteria = [
                {
                    "metric": m.name,
                    "op": m.op.value if hasattr(m.op, "value") else str(m.op),
                    "target": m.value,
                }
                for m in bp.success_criteria.metrics
            ]

            # Training hooks.
            training_data_hooks = [
                {"event": h.event, "dataset": h.dataset, "format": h.format}
                for h in bp.training_data_hooks
            ]
        except Exception:
            # Validation error or malformed JSON — return empty defaults.
            logger.debug("Failed to parse blueprint_json for mission %s", mission.get("mission_id"))

    # Filter out internal keys injected by the controller (e.g. __blueprint_json__).
    raw_slots: dict[str, Any] = mission.get("slots") or {}
    slots = {k: v for k, v in raw_slots.items() if not k.startswith("__")}

    created_at = mission.get("created_at", "")
    updated_at = mission.get("updated_at") or created_at

    mid = mission.get("mission_id", "")
    return AutopilotMissionDetail(
        id=mid,
        mission_id=mid,  # backward-compat alias
        status=mission.get("status", ""),
        goal_text=mission.get("goal_preview", ""),
        created_at=created_at,
        updated_at=updated_at,
        project_id=mission.get("project_id"),
        blueprint_id=blueprint_id,
        description=description,
        agent_graph_json=agent_graph_json,
        tools_required=tools_required,
        providers_required=providers_required,
        slots=slots,
        success_criteria=success_criteria,
        training_data_hooks=training_data_hooks,
        estimated_cost=None,  # Plan H surfaces real cost.
    )


def _build_mission_driver(record: dict[str, Any], blueprint: Blueprint) -> Any:
    """Construct the :class:`MissionDriver` used to execute a mission run.

    Tests monkey-patch this factory to inject a fake driver
    (``monkeypatch.setattr(autopilot_routes, "_build_mission_driver", ...)``)
    without having to mock LLM provider wiring.
    """
    return MissionDriver()


async def _persist_loop(
    sf: AdminStateFile, mission_id: str, q: asyncio.Queue[dict[str, Any]]
) -> None:
    """Drain *q* and persist every event into the mission's stored trace.

    Walks the bus subscriber queue forever, applying each event into the
    mission record under ``trace`` and updating cost / step counters.
    Returns once a ``mission.finished`` event has been persisted, or on
    cancellation.

    Cost is summed from ``agent.llm_call.cost_usd`` only — the SDK's
    ``agent.tool_result`` events do not yet carry cost.  This mirrors
    the observer's bookkeeping in
    :func:`sagewai.admin.autopilot_run_observer.run_mission_with_observer`.
    """
    try:
        while True:
            ev = await q.get()

            def _apply(rec: dict[str, Any], _ev: dict[str, Any] = ev) -> None:
                trace = rec.setdefault("trace", [])
                trace.append(_ev)
                rec["last_event_at"] = _ev.get("ts") or _now_iso()
                kind = _ev.get("kind")
                if kind == "agent.llm_call":
                    rec["total_cost_usd"] = round(
                        (rec.get("total_cost_usd") or 0.0)
                        + float(_ev.get("cost_usd") or 0.0),
                        6,
                    )
                if kind == "agent.finished":
                    rec["step_count"] = (rec.get("step_count") or 0) + 1
                if kind == "mission.finished":
                    rec["finished_at"] = _now_iso()

            try:
                update_mission(sf, mission_id, _apply)
            except KeyError:
                # Mission was deleted underneath us — nothing more to persist.
                return

            if ev.get("kind") == "mission.finished":
                return
    except asyncio.CancelledError:
        return


async def _execute_mission_run(
    sf: AdminStateFile, mission_id: str, run_id: str
) -> None:
    """Background task body — drives a mission to completion or failure.

    This is the load-bearing seam wired up by ``POST /missions/{id}/run``.
    It must never raise — every failure path emits a terminal lifecycle
    transition so the org-wide bus (Plan M) sees a coherent status.
    """
    bus = get_run_bus()
    persist_task: asyncio.Task[None] | None = None
    sink_q: asyncio.Queue[dict[str, Any]] | None = None
    final_status: MissionStatus = MissionStatus.FAILED
    final_reason: str | None = None
    final_summary: dict[str, Any] | None = None

    try:
        record = get_mission(sf, mission_id)
        if record is None:
            logger.warning("mission %s vanished before execution started", mission_id)
            return

        try:
            blueprint = Blueprint.model_validate_json(record["blueprint_json"])
        except Exception as exc:  # noqa: BLE001 — surface invalid blueprint as failed run
            final_reason = f"invalid blueprint: {exc}"
            logger.exception("failed to parse blueprint for mission %s", mission_id)
            return

        slots = dict(record.get("slots") or {})
        slots["__blueprint_json__"] = record["blueprint_json"]
        try:
            mission = Mission(
                mission_id=record["mission_id"],
                project_id=record.get("project_id") or "default",
                blueprint_id=blueprint.id,
                blueprint_version=blueprint.version,
                slots=slots,
            )
            mission.transition_to(MissionState.APPROVED)
            mission.transition_to(MissionState.SCHEDULED)
        except Exception as exc:  # noqa: BLE001
            final_reason = f"mission setup failed: {exc}"
            logger.exception("mission %s setup failed", mission_id)
            return

        try:
            driver = _build_mission_driver(record, blueprint)
        except Exception as exc:  # noqa: BLE001
            final_reason = f"driver construction failed: {exc}"
            logger.exception("driver construction for mission %s failed", mission_id)
            return

        # Subscribe the persist sink BEFORE the observer publishes
        # mission.started so no events are missed.  Replay would catch
        # them anyway, but a hot subscriber halves event-to-disk latency.
        sink_q = bus.subscribe(mission_id)
        persist_task = asyncio.create_task(_persist_loop(sf, mission_id, sink_q))

        try:
            summary = await run_mission_with_observer(
                bus=bus,
                mission_id=mission_id,
                run_id=run_id,
                blueprint=blueprint,
                mission=mission,
                driver=driver,
            )
        except BaseException as exc:  # noqa: BLE001
            final_reason = f"{type(exc).__name__}: {exc}"
            logger.exception("observer raised for mission %s", mission_id)
            return

        final_summary = summary
        observer_status = (summary.get("status") or "").lower()
        if observer_status == "completed":
            final_status = MissionStatus.COMPLETED
            final_reason = None
        else:
            final_status = MissionStatus.FAILED
            final_reason = summary.get("error") or f"mission ended with status '{observer_status}'"
    finally:
        # Wait briefly for the persist sink to drain — the observer's
        # mission.finished event is the loop's exit signal.  Cancel if
        # the queue stalls so we never leak the task.
        if persist_task is not None:
            try:
                await asyncio.wait_for(persist_task, timeout=2.0)
            except asyncio.TimeoutError:
                persist_task.cancel()
                try:
                    await persist_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                # Persist sink errors are non-fatal — the lifecycle
                # transition still has to land.
                logger.exception("persist sink for mission %s raised", mission_id)
        if sink_q is not None:
            bus.unsubscribe(mission_id, sink_q)

        # Persist final summary fields (cost / step / output / error)
        # before the terminal transition so the lifecycle event the
        # frontend reacts to sees a coherent record.
        if final_summary is not None:
            def _stamp_summary(
                rec: dict[str, Any], _summary: dict[str, Any] = final_summary
            ) -> None:
                if _summary.get("total_cost_usd") is not None:
                    rec["total_cost_usd"] = round(float(_summary["total_cost_usd"]), 6)
                if _summary.get("step_count") is not None:
                    rec["step_count"] = int(_summary["step_count"])
                rec["output"] = _summary.get("output")
                rec["error"] = _summary.get("error")
                rec["finished_at"] = rec.get("finished_at") or _now_iso()

            try:
                update_mission(sf, mission_id, _stamp_summary)
            except KeyError:
                pass
        else:
            # No summary → something blew up before the observer
            # produced one.  Persist the error string so the UI can
            # show why the run died.
            def _stamp_error(
                rec: dict[str, Any], _reason: str | None = final_reason
            ) -> None:
                rec["error"] = _reason
                rec["finished_at"] = rec.get("finished_at") or _now_iso()

            try:
                update_mission(sf, mission_id, _stamp_error)
            except KeyError:
                pass

        # Terminal lifecycle transition — must always run so the
        # org-wide bus (Plan M) sees the mission close.
        try:
            current = get_mission(sf, mission_id)
            if current and current.get("status") == MissionStatus.RUNNING.value:
                await _transition_and_publish(
                    sf, mission_id, final_status, reason=final_reason
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to apply terminal transition for mission %s", mission_id
            )


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
        """List stored autopilot missions, optionally scoped to a project.

        Each item is enriched with full blueprint metadata so the list
        view can render a preview without a separate detail fetch.
        """
        err = _require_auth(request, sf)
        if err is not None:
            return err

        pid = _project_id(request)
        missions = list_missions(sf, project_id=pid)
        enriched = [_translate_mission_detail(m).model_dump() for m in missions]
        return JSONResponse({"missions": enriched, "count": len(enriched)})

    # ── GET /autopilot/missions/events ────────────────────────────────

    @router.get("/autopilot/missions/events")
    async def autopilot_mission_events(request: Request):
        """SSE stream of mission status-change events for the current org.

        One ``EventSource`` covers all missions in the org.  Per-mission
        execution trace events are a separate concern (Plan H).
        """
        err = _require_auth(request, sf)
        if err is not None:
            return err

        org = _org_id(sf)
        bus = get_lifecycle_bus()

        async def _gen():
            async for evt in bus.subscribe(org):
                if await request.is_disconnected():
                    break
                yield {"event": "mission.status_changed", "data": evt.model_dump_json()}

        return EventSourceResponse(_gen())

    # ── GET /autopilot/missions/{mission_id}/events ───────────────────

    @router.get("/autopilot/missions/{mission_id}/events")
    async def autopilot_mission_run_events(mission_id: str, request: Request):
        """SSE stream of run-events for a single mission_id.

        Subscribes to :class:`MissionRunBus` and yields each event as a
        typed SSE message (``event: <kind>``). Heartbeat every
        ``AUTOPILOT_SSE_HEARTBEAT`` seconds (default 15) so reverse proxies
        don't time the connection out. Closes on ``mission.finished``.
        """
        err = _require_auth(request, sf)
        if err is not None:
            return err

        bus = get_run_bus()
        q = bus.subscribe(mission_id)

        heartbeat_seconds = float(os.environ.get("AUTOPILOT_SSE_HEARTBEAT", "15"))

        async def _gen():
            # ``EventSourceResponse`` cancels this generator on client
            # disconnect, so we don't poll ``request.is_disconnected``
            # ourselves — that call blocks under httpx ASGITransport
            # when no body chunks are pending and would deadlock the
            # heartbeat loop in tests.
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=heartbeat_seconds)
                    except asyncio.TimeoutError:
                        yield {"event": "heartbeat", "data": "{}"}
                        continue
                    yield {"event": ev.get("kind") or "message", "data": json.dumps(ev)}
                    if ev.get("kind") == "mission.finished":
                        return
            finally:
                bus.unsubscribe(mission_id, q)

        return EventSourceResponse(_gen())

    # ── GET /autopilot/missions/{mission_id}/trace ────────────────────

    @router.get("/autopilot/missions/{mission_id}/trace")
    async def autopilot_mission_trace(mission_id: str, request: Request) -> JSONResponse:
        """Return the persisted run trace + summary for *mission_id*.

        Used by the frontend to replay the live trace on page reload —
        the response shape mirrors what was streamed on
        ``/autopilot/missions/{id}/events`` but is delivered as a single
        JSON object (no SSE).
        """
        err = _require_auth(request, sf)
        if err is not None:
            return err

        mission = get_mission(sf, mission_id)
        if mission is None:
            raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found")

        return JSONResponse({
            "mission_id": mission.get("mission_id"),
            "run_id": mission.get("run_id"),
            "status": mission.get("status"),
            "started_at": mission.get("started_at"),
            "finished_at": mission.get("finished_at"),
            "last_event_at": mission.get("last_event_at"),
            "total_cost_usd": float(mission.get("total_cost_usd") or 0.0),
            "step_count": int(mission.get("step_count") or 0),
            "events": list(mission.get("trace") or []),
            "output": mission.get("output"),
            "error": mission.get("error"),
        })

    # ── GET /autopilot/fleet/workers ──────────────────────────────────

    @router.get("/autopilot/fleet/workers")
    async def autopilot_fleet_workers(request: Request) -> JSONResponse:
        """Return a snapshot of fleet workers visible to this admin instance.

        Used by the Fleet panel header to display the worker pool summary.
        In the single-tenant admin (file-backed state), there is no live
        worker registry — this endpoint exposes the same InMemoryFleetRegistry
        used by the autopilot runner.  The registry is injected via
        :func:`_get_fleet_registry_snapshot` so tests can patch it.
        """
        err = _require_auth(request, sf)
        if err is not None:
            return err

        workers = await _get_fleet_registry_snapshot()
        out = [
            {
                "id": w.id,
                "name": w.name,
                "models_canonical": w.capabilities.models_canonical,
                "pool": w.capabilities.pool,
                "labels": w.capabilities.labels,
                "probe_status": w.probe_status,
                "approval_status": w.approval_status.value
                if hasattr(w.approval_status, "value")
                else str(w.approval_status),
                "last_heartbeat": w.last_heartbeat.isoformat()
                if w.last_heartbeat
                else None,
            }
            for w in workers
        ]
        return JSONResponse(out)

    # ── GET /autopilot/missions/{mission_id}/sandbox-allocation ──────

    @router.get("/autopilot/missions/{mission_id}/sandbox-allocation")
    async def autopilot_sandbox_allocation(mission_id: str, request: Request) -> JSONResponse:
        """Return sandbox tier for each agent step in the mission blueprint."""
        err = _require_auth(request, sf)
        if err is not None:
            return err

        record = get_mission(sf, mission_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found")

        overrides: dict[str, str] = record.get("sandbox_overrides") or {}

        bp_raw = record.get("blueprint_json") or "{}"
        try:
            bp_data = json.loads(bp_raw)
            nodes = bp_data.get("agent_graph", {}).get("nodes", [])
        except (json.JSONDecodeError, AttributeError):
            nodes = []

        result = []
        for node in nodes:
            step_id = node.get("id", "")
            tools: list[str] = node.get("tools") or []
            base_tier = tier_for_tools(tools)
            if step_id in overrides:
                effective_tier = SandboxTier[overrides[step_id]]
                overridden = True
            else:
                effective_tier = base_tier
                overridden = False
            result.append({
                "step_id": step_id,
                "role": node.get("role"),
                "tools": tools,
                "tier": effective_tier.name,
                "base_tier": base_tier.name,
                "overridden": overridden,
            })

        return JSONResponse(result)

    # ── POST /autopilot/missions/{mission_id}/sandbox-override ────────

    @router.post("/autopilot/missions/{mission_id}/sandbox-override")
    async def autopilot_sandbox_override(
        mission_id: str, body: SandboxOverrideBody, request: Request
    ) -> JSONResponse:
        """Apply a downgrade-only sandbox tier override for one step."""
        err = _require_auth(request, sf)
        if err is not None:
            return err

        record = get_mission(sf, mission_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found")

        try:
            proposed = SandboxTier[body.tier]
        except KeyError:
            raise HTTPException(status_code=422, detail=f"Unknown tier: {body.tier!r}")

        bp_raw = record.get("blueprint_json") or "{}"
        try:
            bp_data = json.loads(bp_raw)
            nodes = bp_data.get("agent_graph", {}).get("nodes", [])
        except (json.JSONDecodeError, AttributeError):
            nodes = []

        node = next((n for n in nodes if n.get("id") == body.step_id), None)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Step '{body.step_id}' not found")

        overrides: dict[str, str] = dict(record.get("sandbox_overrides") or {})
        tools: list[str] = node.get("tools") or []
        current_tier_name = overrides.get(body.step_id) or tier_for_tools(tools).name
        current_tier = SandboxTier[current_tier_name]

        if not is_downgrade(proposed, current_tier):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Override rejected: {proposed.name!r} is not a downgrade of "
                    f"{current_tier.name!r}. Only downgrades (to a less trusted tier) "
                    "are accepted."
                ),
            )

        overrides[body.step_id] = proposed.name

        def _apply(rec: dict[str, Any]) -> None:
            rec["sandbox_overrides"] = overrides

        update_mission(sf, mission_id, _apply)

        return JSONResponse({
            "step_id": body.step_id,
            "tier": proposed.name,
            "previous_tier": current_tier.name,
        })

    # ── GET /autopilot/missions/{mission_id}/fleet-allocation ─────────

    @router.get("/autopilot/missions/{mission_id}/fleet-allocation")
    async def autopilot_fleet_allocation(
        mission_id: str, request: Request
    ) -> JSONResponse:
        """Return per-step worker allocation for a mission.

        Pre-run: returns top-5 eligible workers per agent step based on
        live pool snapshot.  Running/finished: includes the ``claimed_worker_id``
        captured in the mission trace when a worker claimed the task.
        """
        err = _require_auth(request, sf)
        if err is not None:
            return err

        mission = get_mission(sf, mission_id)
        if mission is None:
            raise HTTPException(
                status_code=404, detail=f"Mission '{mission_id}' not found"
            )

        blueprint_json = mission.get("blueprint_json") or ""
        if not blueprint_json:
            return JSONResponse([])

        try:
            bp = Blueprint.model_validate_json(blueprint_json)
        except Exception:
            return JSONResponse([])

        ag = bp.agent_graph
        if ag is None:
            return JSONResponse([])

        from sagewai.autopilot.controller.fleet_match import match_workers

        pool = await _get_fleet_registry_snapshot()

        # Build claimed_worker_id lookup from trace events.
        trace: list[dict] = mission.get("trace") or []
        claimed: dict[str, str] = {}
        for ev in trace:
            if ev.get("kind") == "agent.worker_claimed":
                step_id = ev.get("step_id")
                worker_id = ev.get("worker_id")
                if step_id and worker_id:
                    claimed[step_id] = worker_id

        rows = []
        for node in ag.nodes:
            matched = match_workers(node, pool)[:5]
            rows.append(
                {
                    "step_id": node.id,
                    "agent_id": node.id,
                    "role": node.role,
                    "tools": list(node.tools),
                    "matched_workers": [
                        {
                            "worker_id": w.id,
                            "worker_name": w.name,
                            "probe_status": w.probe_status,
                        }
                        for w in matched
                    ],
                    "claimed_worker_id": claimed.get(node.id),
                }
            )
        return JSONResponse(rows)

    # ── GET /autopilot/missions/{mission_id} ──────────────────────────

    @router.get("/autopilot/missions/{mission_id}")
    async def autopilot_get_mission(mission_id: str, request: Request) -> JSONResponse:
        """Return a single mission enriched with full blueprint metadata."""
        err = _require_auth(request, sf)
        if err is not None:
            return err

        mission = get_mission(sf, mission_id)
        if mission is None:
            raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found")

        detail = _translate_mission_detail(mission)
        return JSONResponse(detail.model_dump())

    # ── POST /autopilot/missions/{mission_id}/explain ─────────────────

    @router.post("/autopilot/missions/{mission_id}/explain")
    async def autopilot_explain_mission(mission_id: str, request: Request) -> JSONResponse:
        """Return a templated markdown brief for the mission detail page."""
        err = _require_auth(request, sf)
        if err is not None:
            return err
        mission = get_mission(sf, mission_id)
        if mission is None:
            raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found")
        detail = _translate_mission_detail(mission).model_dump()
        return JSONResponse(render_brief(detail))

    # ── POST /autopilot/missions/{mission_id}/run ─────────────────────

    @router.post("/autopilot/missions/{mission_id}/run", status_code=202)
    async def autopilot_run_mission(mission_id: str, request: Request) -> JSONResponse:
        """Spawn the background mission driver for a pending mission.

        Returns ``202 Accepted`` immediately — execution proceeds in a
        detached :func:`asyncio.create_task`.  Per-event progress is
        published to :class:`MissionRunBus` and persisted into the
        mission record's ``trace`` field.  The terminal lifecycle
        transition (``running → completed/failed``) is published to the
        org-wide lifecycle bus by the background task.
        """
        err = _require_auth(request, sf)
        if err is not None:
            return err

        record = get_mission(sf, mission_id)
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"Mission '{mission_id}' not found"
            )

        current_status = record.get("status")
        if current_status == MissionStatus.RUNNING.value:
            raise HTTPException(status_code=409, detail="mission already running")
        if current_status != MissionStatus.PENDING.value:
            raise HTTPException(
                status_code=409,
                detail=f"cannot run mission in status '{current_status}'",
            )

        run_id = f"run_{secrets.token_hex(6)}"

        # Reset trace + cost on a fresh run.  Re-runs aren't allowed at
        # v1.0 (the state machine forbids the transition) but the schema
        # supports them; clear stale state defensively so that a future
        # re-run path doesn't surface a half-overwritten record.
        # ``started_at`` is stamped by ``transition_mission`` on the
        # PENDING → RUNNING transition; we read it back below so the
        # value returned to the caller matches what's in the file.
        def _start(rec: dict[str, Any]) -> None:
            rec["run_id"] = run_id
            rec["finished_at"] = None
            rec["total_cost_usd"] = 0.0
            rec["step_count"] = 0
            rec["last_event_at"] = None
            rec["trace"] = []
            rec["error"] = None

        update_mission(sf, mission_id, _start)

        try:
            updated = await _transition_and_publish(
                sf, mission_id, MissionStatus.RUNNING
            )
        except IllegalTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        started_at = updated.get("started_at") or _now_iso()

        # Detached background task — must NOT be awaited here.  The
        # request thread returns 202 within milliseconds.
        asyncio.create_task(_execute_mission_run(sf, mission_id, run_id))

        logger.info("Mission run started (id=%s, run_id=%s)", mission_id, run_id)
        return JSONResponse(
            {"run_id": run_id, "started_at": started_at}, status_code=202
        )

    # ── POST /autopilot/missions/{mission_id}/cancel ──────────────────

    @router.post("/autopilot/missions/{mission_id}/cancel", status_code=202)
    async def autopilot_cancel_mission(
        mission_id: str, body: CancelBody, request: Request
    ) -> JSONResponse:
        """Cancel a running mission with a required reason string."""
        err = _require_auth(request, sf)
        if err is not None:
            return err

        record = get_mission(sf, mission_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found")

        if record.get("status") != MissionStatus.RUNNING.value:
            raise HTTPException(
                status_code=409,
                detail=f"cannot cancel mission in status '{record.get('status')}'",
            )

        await _transition_and_publish(
            sf, mission_id, MissionStatus.CANCELLED, reason=body.reason
        )
        logger.info("Mission cancelled (id=%s, reason=%s)", mission_id, body.reason)
        return JSONResponse({"mission_id": mission_id, "status": "cancelled"}, status_code=202)

    return router
