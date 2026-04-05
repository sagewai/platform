# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Admin API router — FastAPI endpoints for agent management.

Provides endpoints for listing agents, querying run history, and
inspecting active sessions. Mount into any FastAPI app.

Usage::

    from fastapi import FastAPI
    from sagewai.admin import create_admin_router, AdminState
    from sagewai.core.registry import AgentRegistry

    state = AdminState()
    registry = AgentRegistry()
    app = FastAPI()
    app.include_router(create_admin_router(state, registry), prefix="/admin")
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from sagewai.admin.controller import RunControlRegistry
from sagewai.admin.models import (
    AgentDetail,
    AgentSummary,
    ConfigUpdateRequest,
    ControlActionResponse,
    RunDetail,
    RunSummary,
    SessionInfo,
    ToolCallRecord,
)
from sagewai.admin.state import AdminState


def create_admin_router(
    state: AdminState,
    registry: Any = None,
    run_controls: RunControlRegistry | None = None,
):
    """Create a FastAPI router with admin endpoints.

    Args:
        state: AdminState instance for run/session tracking.
        registry: Optional AgentRegistry for agent listing.
        run_controls: Optional RunControlRegistry for pause/resume/cancel.

    Returns:
        A FastAPI APIRouter.
    """
    from fastapi import APIRouter, HTTPException, Query, Request

    router = APIRouter(tags=["admin"])

    async def _get_run_count(request: Request, _state: AdminState, agent_name: str) -> int:
        """Get run count from RunStore (Postgres) if available, else in-memory."""
        _run_store = getattr(request.app.state, "run_store", None)
        if _run_store is not None and getattr(_run_store, "is_connected", False):
            try:
                return await _run_store.count(agent_name=agent_name)
            except Exception:
                pass
        return _state.get_agent_run_count(agent_name)

    @router.get("/agents", response_model=list[AgentSummary])
    async def list_agents() -> list[AgentSummary]:
        """List all registered agents (both SDK-registered and playground)."""
        if registry is None:
            return []

        # Lazy import to avoid circular deps — detect playground agents
        try:
            from admin.api.playground import _factory as _pg_factory
        except ImportError:
            _pg_factory = None

        agents_map = registry.list_agents()
        results = []
        for name, capabilities in agents_map.items():
            agent = registry.get(name)
            model = ""
            strategy = ""
            source = "registered"
            if agent and hasattr(agent, "config"):
                model = getattr(agent.config, "model", "")
            tags: list[str] = []
            if _pg_factory is not None and _pg_factory.is_playground_agent(name):
                source = "playground"
                spec = _pg_factory.get_spec(name)
                if spec:
                    strategy = spec.strategy
                    tags = spec.tags
            results.append(
                AgentSummary(
                    name=name,
                    capabilities=capabilities,
                    model=model,
                    source=source,
                    strategy=strategy,
                    tags=tags,
                )
            )
        return results

    @router.get("/agents/{agent_name}", response_model=AgentDetail)
    async def get_agent(agent_name: str, request: Request) -> AgentDetail:
        """Get detailed info about a specific agent."""
        if registry is None:
            raise HTTPException(status_code=404, detail="No registry configured")

        agent = registry.get(agent_name)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

        agents_map = registry.list_agents()
        capabilities = agents_map.get(agent_name, [])

        try:
            from admin.api.playground import _factory as _pg_factory
        except ImportError:
            _pg_factory = None

        config = agent.config
        source = "registered"
        strategy = ""
        mcp_servers: list[str] = []
        memory_backends: list[str] = []
        guardrails: list[str] = []
        tags: list[str] = []
        fallback_models: list[str] = []
        temperature: float | None = None
        top_p: float | None = None
        max_tokens: int | None = None
        frequency_penalty: float | None = None
        presence_penalty: float | None = None
        preset: str | None = None

        if _pg_factory is not None and _pg_factory.is_playground_agent(agent_name):
            source = "playground"
            spec = _pg_factory.get_spec(agent_name)
            if spec:
                strategy = spec.strategy
                mcp_servers = spec.mcp_servers
                memory_backends = spec.memory_backends
                guardrails = spec.guardrails
                tags = spec.tags
                fallback_models = spec.fallback_models
                temperature = spec.temperature
                top_p = spec.top_p
                max_tokens = spec.max_tokens
                frequency_penalty = spec.frequency_penalty
                presence_penalty = spec.presence_penalty
                preset = spec.preset

        inference = getattr(config, "inference", None)
        if temperature is None and inference:
            temperature = getattr(inference, "temperature", None)
        if top_p is None and inference:
            top_p = getattr(inference, "top_p", None)
        if max_tokens is None and inference:
            max_tokens = getattr(inference, "max_tokens", None)
        if frequency_penalty is None and inference:
            frequency_penalty = getattr(inference, "frequency_penalty", None)
        if presence_penalty is None and inference:
            presence_penalty = getattr(inference, "presence_penalty", None)
        if not fallback_models and inference:
            fallback_models = getattr(inference, "fallback_models", [])

        return AgentDetail(
            name=agent_name,
            capabilities=capabilities,
            model=getattr(config, "model", ""),
            system_prompt=getattr(config, "system_prompt", ""),
            max_iterations=getattr(config, "max_iterations", 10),
            tools=[t.name for t in getattr(config, "tools", [])],
            mcp_servers=mcp_servers,
            memory_backends=memory_backends,
            guardrails=guardrails,
            tags=tags,
            fallback_models=fallback_models,
            total_runs=await _get_run_count(request, state, agent_name),
            source=source,
            strategy=strategy,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            preset=preset,
        )

    @router.get("/runs")
    async def list_runs(
        request: Request,
        agent_name: str | None = Query(None),
        status: str | None = Query(None),
        run_type: str | None = Query(None, description="Filter by run type: standalone, workflow_step, directive_delegation"),
        include_workflow_steps: bool = Query(False, description="Include workflow step runs (excluded by default)"),
        cursor: str | None = Query(None, description="Cursor for pagination"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """List agent runs with optional filtering and cursor pagination."""
        import base64
        import json

        # By default, exclude workflow_step runs unless explicitly requested
        exclude_types = None
        if not include_workflow_steps and not run_type:
            exclude_types = ["workflow_step"]

        # Use RunStore (Postgres) when available — survives restarts
        _run_store = getattr(request.app.state, "run_store", None)
        if _run_store is not None and _run_store.is_connected:
            db_runs = await _run_store.list_runs(
                agent_name=agent_name, status=status,
                run_type=run_type or None,
                exclude_run_types=exclude_types,
                limit=limit + 1, offset=offset,
            )
            items_raw = db_runs[:limit]
            has_more = len(db_runs) > limit

            items = [
                RunSummary(
                    run_id=r.run_id,
                    agent_name=r.agent_name,
                    status=r.status,
                    input_preview=r.input_text[:100],
                    output_preview=r.output_text[:100],
                    started_at=r.started_at,
                    completed_at=r.completed_at,
                    total_tokens=r.total_tokens,
                    run_type=r.run_type,
                    parent_workflow_run_id=r.parent_workflow_run_id,
                )
                for r in items_raw
            ]

            next_cursor = None
            if has_more and items:
                last = items[-1]
                payload = json.dumps({
                    "id": last.run_id,
                    "ts": str(last.started_at or ""),
                })
                next_cursor = base64.b64encode(payload.encode()).decode()

            return {
                "items": [r.model_dump() for r in items],
                "next_cursor": next_cursor,
                "has_more": has_more,
            }

        # Fallback: in-memory AdminState
        if cursor:
            decoded = json.loads(base64.b64decode(cursor.encode()).decode())
            cursor_id = decoded.get("id")
            all_runs = state.list_runs(
                agent_name=agent_name, status=status, limit=10000, offset=0,
            )
            found = False
            filtered: list[RunSummary] = []
            for r in all_runs:
                if not found:
                    if r.run_id == cursor_id:
                        found = True
                    continue
                filtered.append(r)
            all_runs = filtered
        else:
            all_runs = state.list_runs(
                agent_name=agent_name, status=status,
                limit=limit + 1, offset=offset,
            )

        items = all_runs[:limit]
        has_more = len(all_runs) > limit

        next_cursor = None
        if has_more and items:
            last = items[-1]
            payload = json.dumps({
                "id": last.run_id,
                "ts": str(last.started_at or ""),
            })
            next_cursor = base64.b64encode(payload.encode()).decode()

        return {
            "items": [r.model_dump() for r in items],
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    @router.get("/runs/{run_id}", response_model=RunDetail)
    async def get_run(run_id: str, request: Request) -> RunDetail:
        """Get detailed info about a specific run."""
        # Try RunStore first for persisted runs
        _run_store = getattr(request.app.state, "run_store", None)
        if _run_store is not None and _run_store.is_connected:
            db_run = await _run_store.get_run(run_id)
            if db_run is not None:
                return RunDetail(
                    run_id=db_run.run_id,
                    agent_name=db_run.agent_name,
                    status=db_run.status,
                    input_text=db_run.input_text,
                    output_text=db_run.output_text,
                    started_at=db_run.started_at,
                    completed_at=db_run.completed_at,
                    total_tokens=db_run.total_tokens,
                    tool_calls=[
                        ToolCallRecord(**tc) if isinstance(tc, dict) else tc
                        for tc in (db_run.tool_calls or [])
                    ],
                    steps=[],
                )

        run = state.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        return run

    @router.get("/sessions")
    async def list_sessions(
        cursor: str | None = Query(None, description="Cursor for pagination"),
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        """List all active sessions with cursor pagination."""
        import base64
        import json

        all_sessions = state.list_sessions()

        if cursor:
            decoded = json.loads(base64.b64decode(cursor.encode()).decode())
            cursor_id = decoded.get("id")
            found = False
            filtered: list[SessionInfo] = []
            for s in all_sessions:
                if not found:
                    if s.session_id == cursor_id:
                        found = True
                    continue
                filtered.append(s)
            all_sessions = filtered

        items = all_sessions[:limit]
        has_more = len(all_sessions) > limit

        next_cursor = None
        if has_more and items:
            last = items[-1]
            payload = json.dumps({
                "id": last.session_id,
                "ts": str(last.started_at),
            })
            next_cursor = base64.b64encode(payload.encode()).decode()

        return {
            "items": [s.model_dump() for s in items],
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    @router.get("/sessions/{session_id}", response_model=SessionInfo)
    async def get_session(session_id: str) -> SessionInfo:
        """Get info about a specific session."""
        session = state.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
        return session

    # ------------------------------------------------------------------
    # Run control endpoints (pause / resume / cancel)
    # ------------------------------------------------------------------

    @router.post("/runs/{run_id}/pause", response_model=ControlActionResponse)
    async def pause_run(run_id: str) -> ControlActionResponse:
        """Pause a running agent invocation."""
        if run_controls is None:
            raise HTTPException(status_code=501, detail="Run controls not configured")
        controller = run_controls.get(run_id)
        if controller is None:
            raise HTTPException(status_code=404, detail=f"No active run '{run_id}'")
        if controller.is_cancelled:
            raise HTTPException(status_code=409, detail="Run is already cancelled")
        controller.pause()
        return ControlActionResponse(run_id=run_id, action="pause", status="paused")

    @router.post("/runs/{run_id}/resume", response_model=ControlActionResponse)
    async def resume_run(run_id: str) -> ControlActionResponse:
        """Resume a paused agent invocation."""
        if run_controls is None:
            raise HTTPException(status_code=501, detail="Run controls not configured")
        controller = run_controls.get(run_id)
        if controller is None:
            raise HTTPException(status_code=404, detail=f"No active run '{run_id}'")
        if controller.is_cancelled:
            raise HTTPException(status_code=409, detail="Run is already cancelled")
        controller.resume()
        return ControlActionResponse(run_id=run_id, action="resume", status="running")

    @router.post("/runs/{run_id}/cancel", response_model=ControlActionResponse)
    async def cancel_run(run_id: str) -> ControlActionResponse:
        """Cancel a running agent invocation."""
        if run_controls is None:
            raise HTTPException(status_code=501, detail="Run controls not configured")
        controller = run_controls.get(run_id)
        if controller is None:
            raise HTTPException(status_code=404, detail=f"No active run '{run_id}'")
        if controller.is_cancelled:
            raise HTTPException(status_code=409, detail="Run is already cancelled")
        controller.cancel()
        return ControlActionResponse(run_id=run_id, action="cancel", status="cancelled")

    # ------------------------------------------------------------------
    # Agent config update
    # ------------------------------------------------------------------

    @router.patch("/agents/{agent_name}/config")
    async def update_agent_config(agent_name: str, request: ConfigUpdateRequest) -> dict[str, Any]:
        """Update an agent's configuration at runtime."""
        if registry is None:
            raise HTTPException(status_code=404, detail="No registry configured")

        agent = registry.get(agent_name)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

        updated: dict[str, Any] = {}
        config = agent.config

        if request.model is not None:
            config.model = request.model
            updated["model"] = request.model
        if request.system_prompt is not None:
            config.system_prompt = request.system_prompt
            updated["system_prompt"] = request.system_prompt[:100] + "..."
        if request.temperature is not None:
            config.inference.temperature = request.temperature
            updated["temperature"] = request.temperature
        if request.top_p is not None:
            config.inference.top_p = request.top_p
            updated["top_p"] = request.top_p
        if request.max_tokens is not None:
            config.inference.max_tokens = request.max_tokens
            updated["max_tokens"] = request.max_tokens
        if request.frequency_penalty is not None:
            config.inference.frequency_penalty = request.frequency_penalty
            updated["frequency_penalty"] = request.frequency_penalty
        if request.presence_penalty is not None:
            config.inference.presence_penalty = request.presence_penalty
            updated["presence_penalty"] = request.presence_penalty
        if request.max_iterations is not None:
            config.max_iterations = request.max_iterations
            updated["max_iterations"] = request.max_iterations
        if request.tags is not None:
            updated["tags"] = request.tags
        if request.fallback_models is not None:
            config.inference.fallback_models = request.fallback_models
            updated["fallback_models"] = request.fallback_models
        if request.context_scopes is not None:
            config.context_scopes = request.context_scopes
            updated["context_scopes"] = request.context_scopes
        if request.retrieval_config is not None:
            config.retrieval_config = request.retrieval_config
            updated["retrieval_config"] = request.retrieval_config
        if request.directive_template is not None:
            config.directive_template = request.directive_template
            updated["directive_template"] = request.directive_template
        if request.auto_learn is not None:
            config.auto_learn = request.auto_learn
            updated["auto_learn"] = request.auto_learn

        # Update playground spec too if this is a playground agent
        try:
            from admin.api.playground import _factory as _pg_factory
        except ImportError:
            _pg_factory = None

        if _pg_factory is not None and _pg_factory.is_playground_agent(agent_name):
            spec = _pg_factory.get_spec(agent_name)
            if spec:
                for field in ("model", "system_prompt", "temperature", "top_p",
                              "max_tokens", "frequency_penalty", "presence_penalty",
                              "max_iterations"):
                    val = getattr(request, field, None)
                    if val is not None:
                        setattr(spec, field, val)
                if request.strategy is not None:
                    spec.strategy = request.strategy
                if request.tags is not None:
                    spec.tags = request.tags
                if request.fallback_models is not None:
                    spec.fallback_models = request.fallback_models

        if not updated:
            raise HTTPException(status_code=400, detail="No fields to update")

        return {"agent": agent_name, "updated": updated}

    return router
