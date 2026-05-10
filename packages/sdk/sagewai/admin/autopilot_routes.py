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
from sagewai.autopilot.sealed_matcher import ProfileRecord, match_profile
from sagewai.autopilot.tool_scopes import scopes_for_tools
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
from sagewai.admin.autopilot_default_tools import get_default_tool_registry
from sagewai.autopilot.controller.driver import MissionDriver
from sagewai.autopilot.controller.executor import ExecutorConfig
from sagewai.autopilot.mission import Mission
from sagewai.autopilot.routing import ConfidenceConfig, GoalRouter, RoutingResult
from sagewai.autopilot.routing.types import AutoRouted, PickerNeeded, SynthesisNeeded
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


class SealedOverrideBody(BaseModel):
    step_id: str
    profile_id: str


async def _get_sealed_profiles_snapshot() -> list[ProfileRecord]:
    """Return the current Sealed profile pool as ProfileRecord objects.

    Tests patch this at the module level to inject controlled profile lists
    without requiring a live Sealed backend.
    """
    try:
        from sagewai.sealed.refs import ProfileRef, resolve_backend
        backend = resolve_backend(ProfileRef(scheme="builtin", path=""))
        metas = await backend.list_profiles()
    except Exception:  # noqa: BLE001 — degrade gracefully if no backend
        return []

    from datetime import timezone

    return [
        ProfileRecord(
            id=m.id,
            name=m.name,
            # Sealed profile tags carry granted scope strings for autopilot matching.
            granted_scopes=frozenset(m.tags),
            last_used_at=m.last_rotated_at or datetime.datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        for m in metas
    ]


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


def _translate_mission_list_item(mission: dict[str, Any]) -> dict[str, Any]:
    """Build the list-endpoint payload for one mission.

    The list payload merges:

    * the enriched ``AutopilotMissionDetail`` fields (agent graph, tools,
      providers, slots, success criteria, training hooks, estimated
      cost, ``mission_id`` alias) — kept so the list view can render a
      preview without a separate detail fetch and so existing API
      consumers / tests that read those fields keep working;
    * the slim ``AutopilotMission`` fields the list-row React component
      reads (``blueprint_title``, ``blueprint_category``, ``mode``,
      ``started_at``, ``finished_at``, ``steps``) — without these the
      expand-row branch crashes on ``mission.steps.length``.
    """
    detail = _translate_mission_detail(mission).model_dump()

    blueprint_json = mission.get("blueprint_json") or ""
    blueprint_title = ""
    blueprint_category = ""
    mode = "scheduled"
    if blueprint_json:
        try:
            bp = Blueprint.model_validate_json(blueprint_json)
            blueprint_title = bp.title
            blueprint_category = bp.category
            if bp.mode is not None:
                mode = bp.mode.value if hasattr(bp.mode, "value") else str(bp.mode)
        except Exception:
            logger.debug(
                "Failed to parse blueprint_json for mission %s",
                mission.get("mission_id"),
            )

    raw_steps = mission.get("steps") or []
    steps: list[dict[str, Any]] = []
    for s in raw_steps:
        if isinstance(s, dict):
            steps.append(
                {
                    "step": s.get("step", ""),
                    "status": s.get("status", ""),
                    "output": s.get("output"),
                    "started_at": s.get("started_at"),
                    "finished_at": s.get("finished_at"),
                }
            )

    detail.update(
        {
            "blueprint_title": blueprint_title,
            "blueprint_category": blueprint_category,
            "mode": mode,
            "started_at": mission.get("started_at"),
            "finished_at": mission.get("finished_at"),
            "steps": steps,
        }
    )
    return detail


def _blueprint_summary(
    blueprint_json: str,
    *,
    slots: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Translate a ``blueprint_json`` string into the frontend's ``AutopilotBlueprint`` shape.

    Returns a dict with ``id``, ``title``, ``category``, ``mode``, ``slots``,
    ``estimated_cost``, or ``None`` if the blueprint cannot be parsed.  When
    ``slots`` is provided (e.g. extracted slot values for an auto-routed
    result) they are emitted as ``[{"key", "value"}]`` pairs for the
    plan-preview UI; otherwise ``slots`` is an empty list.
    """
    try:
        bp = Blueprint.model_validate_json(blueprint_json)
    except Exception:
        logger.debug("Failed to parse blueprint_json for summary")
        return None

    if bp.mode is not None:
        mode_value = bp.mode.value if hasattr(bp.mode, "value") else str(bp.mode)
    else:
        mode_value = "scheduled"

    slot_pairs: list[dict[str, str]] = []
    if slots:
        slot_pairs = [
            {"key": k, "value": str(v)}
            for k, v in slots.items()
            if not k.startswith("__")
        ]

    return {
        "id": bp.id,
        "title": bp.title,
        "category": bp.category,
        "mode": mode_value,
        "slots": slot_pairs,
        "estimated_cost": None,
    }


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


# litellm model name per cloud-provider key. Used when the operator
# configured a provider in /api/v1/providers but didn't pin a specific
# model in its config dict.
_PROVIDER_DEFAULT_MODEL = {
    "openai": "gpt-4o-mini",
    "anthropic": "anthropic/claude-haiku-4-5",
    "google": "gemini/gemini-2.0-flash",
    "groq": "groq/llama-3.3-70b-versatile",
    "mistral": "mistral/mistral-small-latest",
    "together": "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "xai": "xai/grok-2-latest",
    "perplexity": "perplexity/llama-3.1-sonar-small-128k-online",
    "cohere": "command-r",
}

# Self-hosted providers don't need an API key — a base_url and a model
# pull are enough. They're "configured" by virtue of being added; the
# executor reaches them over HTTP.
_SELF_HOSTED_PROVIDERS = {"ollama", "lmstudio", "vllm"}

_SELF_HOSTED_DEFAULT_BASE_URL = {
    "ollama": "http://localhost:11434",
    "lmstudio": "http://localhost:1234/v1",
}


def _resolve_executor_config(
    sf: AdminStateFile, project_id: str | None
) -> ExecutorConfig:
    """Pick a configured LLM provider and build an :class:`ExecutorConfig`.

    Looks at the providers stored in the admin state file (project-scoped
    first, then org-global). Uses the first provider whose API key is
    available — either persisted in ``config["api_key"]`` or already
    exported via the matching env var (``env_var_set`` flag from
    :meth:`AdminStateFile.list_providers`). Populates the env var if
    needed so litellm picks up the credentials, and pins the executor's
    model to the provider's chosen model (or a per-provider default).

    Returns the default :class:`ExecutorConfig` when no provider is
    configured — the executor then surfaces a clear "no provider"
    failure via the all-skipped → FAILED path in :class:`MissionDriver`.
    """
    providers = sf.list_providers(project_id=project_id)
    if project_id and not providers:
        # Fall back to org-global providers if the project has none.
        providers = sf.list_providers(project_id=None)

    def _has_creds(p: dict[str, Any]) -> bool:
        # Self-hosted providers (ollama, lmstudio, vllm) don't need an
        # API key — they're reachable over HTTP at a known base_url.
        # Treat them as configured by virtue of being added.
        if p.get("provider_name") in _SELF_HOSTED_PROVIDERS:
            return True
        cfg = p.get("config") or {}
        return bool(cfg.get("api_key") or p.get("env_var_set"))

    # Prefer the explicitly-marked default; fall back to the first
    # provider with credentials. This lets operators pin a deterministic
    # provider via ``set_default_provider`` instead of relying on
    # insertion order.
    chosen: dict[str, Any] | None = next(
        (p for p in providers if p.get("default") and _has_creds(p)),
        None,
    )
    if chosen is None:
        chosen = next((p for p in providers if _has_creds(p)), None)

    tool_registry = get_default_tool_registry()

    if chosen is None:
        return ExecutorConfig(tool_registry=tool_registry)

    cfg = chosen.get("config") or {}
    provider_name = chosen.get("provider_name", "")
    api_key = cfg.get("api_key", "")
    env_var = chosen.get("env_var_key", "")
    if api_key and env_var and not os.environ.get(env_var):
        os.environ[env_var] = api_key

    # Self-hosted providers: export base_url to the env var litellm
    # honours for that backend, so the executor reaches the local
    # server instead of a public API. Default to the well-known port
    # when the operator didn't specify one.
    if provider_name in _SELF_HOSTED_PROVIDERS:
        base_url = (
            cfg.get("base_url")
            or _SELF_HOSTED_DEFAULT_BASE_URL.get(provider_name, "")
        )
        if provider_name == "ollama" and base_url:
            os.environ.setdefault("OLLAMA_API_BASE", base_url)
        elif provider_name == "lmstudio" and base_url:
            os.environ.setdefault("OPENAI_API_BASE", base_url)
            # litellm's openai-compat path requires a non-empty key
            # even when the local server doesn't validate it.
            os.environ.setdefault("OPENAI_API_KEY", "lm-studio")

    model = cfg.get("model") or _PROVIDER_DEFAULT_MODEL.get(
        provider_name, ExecutorConfig.model_fields["model"].default
    )
    # Auto-prefix the model name for self-hosted backends so litellm
    # routes to the right transport. ``ollama/<name>`` → Ollama HTTP
    # API; ``openai/<name>`` → OpenAI-compatible (LM Studio, vLLM).
    if provider_name == "ollama" and "/" not in model:
        model = f"ollama/{model}"
    elif provider_name == "lmstudio" and "/" not in model:
        model = f"openai/{model}"
    return ExecutorConfig(model=model, tool_registry=tool_registry)


def _build_mission_driver(
    record: dict[str, Any],
    blueprint: Blueprint,
    sf: AdminStateFile | None = None,
) -> Any:
    """Construct the :class:`MissionDriver` used to execute a mission run.

    When *sf* is provided, the driver is built with an
    :class:`ExecutorConfig` derived from the configured admin providers
    (project-scoped, falling back to org-global). When *sf* is ``None``
    (legacy callers / tests that don't need provider wiring) the
    executor uses its compiled-in defaults and litellm's env-var
    fallback — the same behavior as before this helper was added.

    Tests monkey-patch this factory to inject a fake driver
    (``monkeypatch.setattr(autopilot_routes, "_build_mission_driver", ...)``)
    without having to mock LLM provider wiring.
    """
    if sf is None:
        return MissionDriver()
    project_id = record.get("project_id")
    config = _resolve_executor_config(sf, project_id)
    return MissionDriver(executor_config=config)


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
        # Surface the original goal to the executor so LLM agents have the
        # user's intent in their context — without this, synthesised
        # blueprints (which often declare zero required_slots) leave each
        # agent with an empty user message and the LLM falls back to
        # asking for clarification.
        goal_text = record.get("goal_preview") or ""
        if goal_text and "goal" not in slots:
            slots["goal"] = goal_text
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
            driver = _build_mission_driver(record, blueprint, sf=sf)
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

    # ── GET /autopilot/system-readiness ───────────────────────────────

    @router.get("/autopilot/system-readiness")
    async def autopilot_system_readiness(request: Request) -> JSONResponse:
        """Report what the operator has configured for autopilot to run well.

        Surfaces missing pieces a first-time user is most likely to
        miss: an LLM provider (without one, every mission fails fast),
        and a real search backend (without one, web_search falls back
        to DuckDuckGo HTML scraping which rate-limits and hits regional
        consent walls). Never returns secret values — only booleans
        and provider names.
        """
        err = _require_auth(request, sf)
        if err is not None:
            return err

        pid = _project_id(request)
        providers = sf.list_providers(project_id=pid)
        if pid and not providers:
            providers = sf.list_providers(project_id=None)

        def _has_creds(p: dict[str, Any]) -> bool:
            if p.get("provider_name") in _SELF_HOSTED_PROVIDERS:
                return True
            cfg = p.get("config") or {}
            return bool(cfg.get("api_key") or p.get("env_var_set"))

        configured = [p for p in providers if _has_creds(p)]
        default = next((p for p in configured if p.get("default")), None)
        provider_summary = [
            {
                "id": p.get("id", ""),
                "provider_name": p.get("provider_name", ""),
                "display_name": p.get("display_name") or p.get("provider_name", ""),
                "default": bool(p.get("default")),
                "model": (p.get("config") or {}).get("model"),
                "type": p.get("provider_type", "cloud"),
            }
            for p in configured
        ]

        # Search backend ladder — return which env vars are set so the
        # frontend can show a single clear message about which backend
        # will actually run. Never the values, just booleans.
        search_keys = {
            "serper": bool(os.environ.get("SERPER_API_KEY")),
            "tavily": bool(os.environ.get("TAVILY_API_KEY")),
            "brave": bool(os.environ.get("BRAVE_SEARCH_API_KEY")),
        }
        if search_keys["serper"]:
            active_search = "serper"
        elif search_keys["tavily"]:
            active_search = "tavily"
        elif search_keys["brave"]:
            active_search = "brave"
        else:
            active_search = "duckduckgo_html"

        warnings: list[dict[str, str]] = []
        if not configured:
            warnings.append(
                {
                    "code": "no_llm_provider",
                    "severity": "error",
                    "message": (
                        "No LLM provider is configured. Autopilot missions "
                        "will fail until you add one."
                    ),
                    "fix": (
                        "Run `sagewai provider add ollama --model qwen2.5:14b "
                        "--default` for a free local model, or `sagewai "
                        "provider add openai --api-key sk-... --default` "
                        "for hosted."
                    ),
                }
            )
        elif default is None:
            warnings.append(
                {
                    "code": "no_default_provider",
                    "severity": "warning",
                    "message": (
                        f"You have {len(configured)} provider(s) configured "
                        "but none is marked default. The first one with "
                        "credentials will be used."
                    ),
                    "fix": (
                        f"Run `sagewai provider set-default "
                        f"{configured[0].get('provider_name','')}`."
                    ),
                }
            )
        if active_search == "duckduckgo_html":
            warnings.append(
                {
                    "code": "no_search_api_key",
                    "severity": "info",
                    "message": (
                        "web_search is using the DuckDuckGo HTML fallback. "
                        "It's rate-limited and hits consent walls in some "
                        "regions. Set a search API key for production-grade "
                        "results."
                    ),
                    "fix": (
                        "Export one of: SERPER_API_KEY (serper.dev — free "
                        "2.5K queries/month), TAVILY_API_KEY (tavily.com — "
                        "agent-tuned), or BRAVE_SEARCH_API_KEY "
                        "(api.search.brave.com — independent index)."
                    ),
                }
            )

        return JSONResponse(
            {
                "providers": provider_summary,
                "default_provider": (
                    {
                        "id": default.get("id", ""),
                        "provider_name": default.get("provider_name", ""),
                        "model": (default.get("config") or {}).get("model"),
                    }
                    if default
                    else None
                ),
                "search_keys_set": search_keys,
                "active_search_backend": active_search,
                "warnings": warnings,
                "ready": not any(w["severity"] == "error" for w in warnings),
            }
        )

    # ── GET /autopilot/status ─────────────────────────────────────────

    @router.get("/autopilot/status")
    async def autopilot_status(request: Request) -> JSONResponse:
        """Return the current autopilot configuration and identity summary."""
        err = _require_auth(request, sf)
        if err is not None:
            return err

        config = get_autopilot_config(sf)
        identity = get_autopilot_identity(sf)
        instance_id = identity.instance_id if identity else None
        return JSONResponse(
            {
                "enabled": config.get("enabled", False),
                "tier": config.get("tier", "anonymous"),
                "instance_id": instance_id,
                "install_id": instance_id,
                "quota_used": 0,
                "quota_limit": None,
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
        pid = _project_id(request)

        if isinstance(result, AutoRouted):
            bp_json = result.ranked.blueprint_json
            slot_values = dict(result.slots)
            blueprint = _blueprint_summary(bp_json, slots=slot_values)
            mission_id = secrets.token_hex(12)
            save_mission(
                sf,
                {
                    "mission_id": mission_id,
                    "project_id": pid,
                    "status": "pending",
                    "created_at": _now_iso(),
                    "goal_preview": result.preview,
                    "slots": slot_values,
                    "blueprint_json": bp_json,
                    "score": result.ranked.score,
                },
            )
            return JSONResponse(
                {
                    "routing_result": "auto_routed",
                    "mission_id": mission_id,
                    "blueprint": blueprint,
                    "candidates": [],
                    "message": None,
                }
            )

        if isinstance(result, PickerNeeded):
            summaries: list[dict[str, Any]] = []
            candidate_blueprints: dict[str, str] = {}
            for c in result.candidates:
                summary = _blueprint_summary(c.blueprint_json)
                if summary is None:
                    continue
                summaries.append(summary)
                candidate_blueprints[summary["id"]] = c.blueprint_json
            if not summaries:
                return JSONResponse(
                    {
                        "routing_result": "synthesis_needed",
                        "goal": goal,
                        "mission_id": None,
                        "blueprint": None,
                        "candidates": [],
                        "message": "No candidate blueprint could be parsed.",
                    }
                )
            top_bp_json = result.candidates[0].blueprint_json
            mission_id = secrets.token_hex(12)
            save_mission(
                sf,
                {
                    "mission_id": mission_id,
                    "project_id": pid,
                    "status": "pending",
                    "created_at": _now_iso(),
                    "goal_preview": goal,
                    "slots": {},
                    "blueprint_json": top_bp_json,
                    "score": result.candidates[0].score,
                    "candidate_blueprints": candidate_blueprints,
                },
            )
            return JSONResponse(
                {
                    "routing_result": "picker_needed",
                    "mission_id": mission_id,
                    "blueprint": None,
                    "candidates": summaries,
                    "message": None,
                }
            )

        # SynthesisNeeded — keep `goal` for backward-compat with existing
        # tests + frontend's "synthesis_needed" branch which doesn't need a
        # mission record.
        return JSONResponse(
            {
                "routing_result": "synthesis_needed",
                "goal": result.goal if isinstance(result, SynthesisNeeded) else goal,
                "mission_id": None,
                "blueprint": None,
                "candidates": [],
                "message": "No matching blueprint found.",
            }
        )

    # ── POST /autopilot/synthesize ────────────────────────────────────

    @router.post("/autopilot/synthesize")
    async def autopilot_synthesize(request: Request) -> JSONResponse:
        """Synthesize a custom blueprint when retrieval found no match.

        Body: ``{"goal": "<plain English goal>"}``.

        Calls :meth:`SagewaiLLMClient.generate_blueprint` against the
        hosted service, parses the returned blueprint JSON, eagerly
        creates a PENDING mission, and returns the same shape as
        ``/autopilot/goal`` for the ``auto_routed`` branch so the
        frontend can drop directly into the plan-preview UI.

        Degrades gracefully: returns 503 with a helpful message when
        the hosted service is unreachable or returns an unparseable
        blueprint, so the UI can show a real error instead of a silent
        no-op.
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
        # Synthesis can take minutes when Ollama is running a large local
        # model (a 108B network on a laptop is in the 1–3 min range for a
        # full blueprint), so override the client's default 30 s read
        # timeout. Retrieval calls below stay on the default — they
        # don't run a model and complete in <1 s.
        client = SagewaiLLMClient(
            identity=identity,
            cache=cache,
            base_url=config.get("base_url", "https://llm.sagewai.ai"),
            timeout_seconds=600.0,
        )

        # Forward the operator's configured provider so sagewai-llm uses
        # the same model for synthesis. For self-hosted backends running
        # on the host (Ollama, LM Studio), translate ``localhost`` →
        # ``host.docker.internal`` so the sagewai-llm container can
        # reach back out. Cloud providers fall through with no rewrite.
        synthesis_context: dict[str, Any] = {}
        pid = _project_id(request)
        try:
            executor_cfg = _resolve_executor_config(sf, pid)
            synthesis_context["model"] = executor_cfg.model
            for env_name in ("OLLAMA_API_BASE", "OPENAI_API_BASE"):
                base = os.environ.get(env_name, "")
                if base:
                    synthesis_context["base_url"] = base.replace(
                        "localhost", "host.docker.internal"
                    ).replace("127.0.0.1", "host.docker.internal")
                    break
        except Exception:  # noqa: BLE001
            pass

        try:
            response = await client.generate_blueprint(
                goal=goal, context=synthesis_context or None
            )
        except Exception as exc:  # noqa: BLE001 — hosted-service failure
            logger.warning("Synthesize: generate_blueprint failed: %s", exc)
            # Timeout reads differently from "down" — surface the
            # distinction so the operator knows whether to wait, retry,
            # or switch to a faster model.
            msg = str(exc).lower()
            is_timeout = "timeout" in msg or "timed out" in msg
            error = (
                "Blueprint synthesis timed out. The configured model is "
                "taking longer than 10 minutes. Switch to a smaller / "
                "faster local model (e.g. `sagewai provider add ollama "
                "--model qwen2.5:7b --default`) or run the synthesis on a "
                "cloud provider."
                if is_timeout
                else "Blueprint synthesis service is unreachable. "
                f"({type(exc).__name__}: {exc})"
            )
            return JSONResponse({"ok": False, "error": error}, status_code=503)

        bp_json = response.blueprint_json
        summary = _blueprint_summary(bp_json)
        if summary is None:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Synthesis returned a blueprint that could not be parsed.",
                },
                status_code=502,
            )

        pid = _project_id(request)
        mission_id = secrets.token_hex(12)
        save_mission(
            sf,
            {
                "mission_id": mission_id,
                "project_id": pid,
                "status": "pending",
                "created_at": _now_iso(),
                "goal_preview": goal,
                "slots": {},
                "blueprint_json": bp_json,
                "score": float(response.confidence),
            },
        )
        return JSONResponse(
            {
                "routing_result": "auto_routed",
                "mission_id": mission_id,
                "blueprint": summary,
                "candidates": [],
                "message": None,
            }
        )

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

        # New shape: ``{mission_id, blueprint_id}`` — the mission was already
        # created on /goal (auto_routed or picker_needed top candidate). If
        # the user picked a non-top candidate from the picker, swap the
        # stored ``blueprint_json`` to the chosen one.
        if "mission_id" in body and "result" not in body:
            mission_id = body.get("mission_id")
            blueprint_id = body.get("blueprint_id")
            if not mission_id:
                return JSONResponse(
                    {"ok": False, "error": "mission_id is required"},
                    status_code=422,
                )
            mission = get_mission(sf, mission_id)
            if mission is None:
                return JSONResponse(
                    {"ok": False, "error": "mission not found"},
                    status_code=404,
                )
            current_bp_json = mission.get("blueprint_json") or ""
            current_bp_id: str | None = None
            try:
                if current_bp_json:
                    current_bp_id = Blueprint.model_validate_json(current_bp_json).id
            except Exception:
                current_bp_id = None
            candidates_blueprints: dict[str, str] = mission.get("candidate_blueprints") or {}
            if (
                blueprint_id
                and blueprint_id != current_bp_id
                and blueprint_id in candidates_blueprints
            ):
                new_bp_json = candidates_blueprints[blueprint_id]

                def _swap(m: dict[str, Any]) -> None:
                    m["blueprint_json"] = new_bp_json

                update_mission(sf, mission_id, _swap)
            logger.info(
                "Mission approved (id=%s, project=%s)", mission_id, mission.get("project_id")
            )
            return JSONResponse({"status": "approved", "mission_id": mission_id})

        # Legacy shape: ``{result, project_id}`` — kept for backward-compat
        # with existing tests and any callers that haven't moved to the new
        # /goal-creates-mission flow.
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
        mission_payload: dict[str, Any] = {
            "mission_id": secrets.token_hex(12),
            "project_id": pid,
            "status": "pending",
            "created_at": _now_iso(),
            "goal_preview": result.get("preview", ""),
            "slots": result.get("slots", {}),
            "blueprint_json": result.get("ranked", {}).get("blueprint_json", ""),
            "score": result.get("ranked", {}).get("score"),
        }
        saved = save_mission(sf, mission_payload)
        logger.info("Mission created (id=%s, project=%s)", saved["mission_id"], pid)
        return JSONResponse({"ok": True, "mission": saved}, status_code=201)

    # ── GET /autopilot/missions ───────────────────────────────────────

    @router.get("/autopilot/missions")
    async def autopilot_missions(request: Request) -> JSONResponse:
        """List stored autopilot missions, sorted newest-first.

        Query parameters:

        ``q``
            Case-insensitive substring filter applied to the mission id,
            goal_preview, blueprint title and category.
        ``limit``
            Page size (default 25, max 200).
        ``offset``
            Number of items to skip (default 0).
        ``status``
            Optional exact-match status filter (e.g. ``pending``,
            ``running``, ``completed``, ``failed``).

        The response always carries ``total`` so the client can render
        pagination, plus ``count`` (size of this page) and
        ``has_more`` for "load more"-style UIs.
        """
        err = _require_auth(request, sf)
        if err is not None:
            return err

        params = request.query_params
        q = (params.get("q") or "").strip().lower()
        status_filter = (params.get("status") or "").strip().lower()
        try:
            limit = max(1, min(200, int(params.get("limit") or 25)))
        except ValueError:
            limit = 25
        try:
            offset = max(0, int(params.get("offset") or 0))
        except ValueError:
            offset = 0

        pid = _project_id(request)
        missions = list_missions(sf, project_id=pid)
        items = [_translate_mission_list_item(m) for m in missions]

        # Sort newest-first by created_at; ties fall back to mission_id
        # so the order is deterministic for the same input.
        items.sort(
            key=lambda it: (it.get("created_at") or "", it.get("id") or ""),
            reverse=True,
        )

        if status_filter:
            items = [it for it in items if (it.get("status") or "").lower() == status_filter]

        if q:
            def _matches(it: dict[str, Any]) -> bool:
                hay = " ".join(
                    str(it.get(k) or "")
                    for k in ("id", "goal_text", "blueprint_title", "blueprint_category")
                ).lower()
                return q in hay
            items = [it for it in items if _matches(it)]

        total = len(items)
        page = items[offset : offset + limit]
        return JSONResponse(
            {
                "missions": page,
                "count": len(page),
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(page) < total,
            }
        )

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

    # ── GET /autopilot/missions/{mission_id}/sealed-allocation ────────

    @router.get("/autopilot/missions/{mission_id}/sealed-allocation")
    async def autopilot_sealed_allocation(mission_id: str, request: Request) -> JSONResponse:
        """Return Sealed profile match per agent step."""
        err = _require_auth(request, sf)
        if err is not None:
            return err

        record = get_mission(sf, mission_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found")

        sealed_overrides: dict[str, str] = record.get("sealed_overrides") or {}

        bp_raw = record.get("blueprint_json") or "{}"
        try:
            bp_data = json.loads(bp_raw)
            nodes = bp_data.get("agent_graph", {}).get("nodes", [])
        except (json.JSONDecodeError, AttributeError):
            nodes = []

        profiles = await _get_sealed_profiles_snapshot()

        result = []
        for node in nodes:
            step_id = node.get("id", "")
            tools: list[str] = node.get("tools") or []
            required = scopes_for_tools(tools)
            overridden = step_id in sealed_overrides

            if overridden:
                matched_profile_id: str | None = sealed_overrides[step_id]
            else:
                matched = match_profile(required, profiles)
                matched_profile_id = matched.id if matched else None

            jit_hitl = matched_profile_id is None and bool(required)

            result.append({
                "step_id": step_id,
                "role": node.get("role"),
                "tools": tools,
                "required_scopes": sorted(required),
                "matched_profile_id": matched_profile_id,
                "overridden": overridden,
                "jit_hitl": jit_hitl,
            })

        return JSONResponse(result)

    # ── POST /autopilot/missions/{mission_id}/sealed-override ─────────

    @router.post("/autopilot/missions/{mission_id}/sealed-override")
    async def autopilot_sealed_override(
        mission_id: str, body: SealedOverrideBody, request: Request
    ) -> JSONResponse:
        """Manually assign a Sealed profile to one agent step."""
        err = _require_auth(request, sf)
        if err is not None:
            return err

        record = get_mission(sf, mission_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found")

        overrides: dict[str, str] = dict(record.get("sealed_overrides") or {})
        overrides[body.step_id] = body.profile_id

        def _apply(rec: dict[str, Any]) -> None:
            rec["sealed_overrides"] = overrides

        update_mission(sf, mission_id, _apply)

        return JSONResponse({"step_id": body.step_id, "profile_id": body.profile_id})

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

    # ── POST /autopilot/missions/{mission_id}/rerun ───────────────────

    @router.post("/autopilot/missions/{mission_id}/rerun", status_code=201)
    async def autopilot_rerun_mission(
        mission_id: str, request: Request
    ) -> JSONResponse:
        """Clone *mission_id* as a fresh PENDING mission.

        Lifecycle is one-way (PENDING → RUNNING → COMPLETED/FAILED/
        CANCELLED), so re-running a terminal mission is implemented as
        a clone: a new mission record with a new id is created with the
        same blueprint_json, goal_preview, and project_id as the
        source. The caller is expected to POST ``/run`` on the new
        ``mission_id`` to start execution.
        """
        err = _require_auth(request, sf)
        if err is not None:
            return err

        source = get_mission(sf, mission_id)
        if source is None:
            raise HTTPException(status_code=404, detail=f"Mission '{mission_id}' not found")

        new_id = secrets.token_hex(12)
        clone: dict[str, Any] = {
            "mission_id": new_id,
            "project_id": source.get("project_id"),
            "status": "pending",
            "created_at": _now_iso(),
            "goal_preview": source.get("goal_preview", ""),
            "slots": {
                k: v
                for k, v in (source.get("slots") or {}).items()
                if not k.startswith("__")
            },
            "blueprint_json": source.get("blueprint_json", ""),
            "score": source.get("score"),
        }
        candidates = source.get("candidate_blueprints")
        if candidates:
            clone["candidate_blueprints"] = candidates
        save_mission(sf, clone)
        logger.info(
            "Mission rerun: source=%s new=%s project=%s",
            mission_id, new_id, source.get("project_id"),
        )
        return JSONResponse(
            {"mission_id": new_id, "source_mission_id": mission_id, "status": "pending"},
            status_code=201,
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
