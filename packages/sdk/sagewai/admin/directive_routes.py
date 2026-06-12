# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Admin routes for /api/v1/admin/directives (Sealed-v).

Eight endpoints:
  GET    /policies                         — read directive cascade config
  PUT    /policies                         — replace cascade config
  GET    /preview                          — resolve cascade for given workflow
  GET    /evaluations                      — list directive_evaluations rows
  GET    /approvals                        — pending HITL approvals
  POST   /approvals/{decision_id}/approve  — operator approves a pending decision
  POST   /approvals/{decision_id}/deny     — operator denies a pending decision
  GET    /runs/{run_id}                    — directive event timeline for a run

Wiring: `register(app, state_file)` mounts the router and stores the
state-file handle on `app.state.admin_state_file`. The directive
approvals registry and evaluations adapter (Postgres-backed) are
optional — when absent (no Postgres), the read endpoints degrade to
empty responses and the approve/deny endpoints return 503.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request

from sagewai.admin._actor import actor_id_for
from sagewai.admin.authz import (
    in_read_scope,
    require_in_project_scope,
    require_org_admin,
)
from sagewai.sealed.directives.approvals import (
    AlreadyDecidedError,
    PendingApprovalsRegistry,
    SuppressedAlreadyPendingError,
)
from sagewai.sealed.directives.policies import (
    DirectivesConfig,
    resolve_directive_policies,
)

router = APIRouter(prefix="/api/v1/admin/directives", tags=["sealed-v"])


def register(
    app: FastAPI,
    state_file: Any,
    *,
    approvals: PendingApprovalsRegistry | None = None,
    evaluations_adapter: Any | None = None,
) -> None:
    """Wire directive routes into the admin app.

    `state_file` is an AdminStateFile (provides get/set_directives_config).
    `approvals` and `evaluations_adapter` are Postgres-backed; when None,
    the corresponding endpoints return graceful empty responses or 503.
    """
    app.state.admin_state_file = state_file
    app.state.directive_approvals = approvals
    app.state.directive_evaluations_adapter = evaluations_adapter
    app.include_router(router)


def _state(request: Request) -> Any:
    return request.app.state.admin_state_file


def _approvals(request: Request) -> PendingApprovalsRegistry | None:
    return getattr(request.app.state, "directive_approvals", None)


def _evaluations(request: Request) -> Any | None:
    return getattr(request.app.state, "directive_evaluations_adapter", None)


def _ctx(request: Request) -> Any:
    """The session-derived RequestContext, or None when unwired (no AuthMiddleware).

    The authz toolkit no-ops in single-org and when ``ctx is None`` (unwired test
    apps), so the multi-tenant gates below are inert outside multi-tenant mode."""
    return getattr(request.state, "context", None)


@router.get("/policies")
async def get_policies(request: Request) -> dict[str, Any]:
    # Org-level: returns project policies across the whole org -> org-admin only.
    ctx = _ctx(request)
    if ctx is not None:
        require_org_admin(ctx)
    cfg = _state(request).get_directives_config()
    return cfg.model_dump(mode="json")


@router.put("/policies")
async def put_policies(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    # Org-level: replaces the org-wide directive cascade config -> org-admin only.
    ctx = _ctx(request)
    if ctx is not None:
        require_org_admin(ctx)
    cfg = DirectivesConfig.model_validate(body)
    _state(request).set_directives_config(cfg)
    return {"ok": True}


@router.get("/preview")
async def preview_cascade(
    request: Request,
    workflow: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    # Org-level read: resolves the cascade for an arbitrary project_id -> org-admin only.
    ctx = _ctx(request)
    if ctx is not None:
        require_org_admin(ctx)
    cfg = _state(request).get_directives_config()
    pols = resolve_directive_policies(
        workflow_name=workflow, project_id=project_id, config=cfg,
    )
    return {"active_policies": [p.model_dump(mode="json") for p in pols]}


@router.get("/evaluations")
async def list_evaluations(
    request: Request,
    run_id: str | None = None,
    policy_id: str | None = None,
    event_type: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    adapter = _evaluations(request)
    if adapter is None:
        return {"events": []}
    rows = await adapter.list_filtered(
        run_id=run_id, policy_id=policy_id, event_type=event_type, limit=limit,
    )
    # Project-scoped read: a project sees only its own + org-shared eval rows.
    ctx = _ctx(request)
    if ctx is not None:
        rows = [r for r in rows if in_read_scope(r.get("project_id"), ctx)]
    return {"events": rows}


@router.get("/approvals")
async def list_approvals(request: Request) -> dict[str, Any]:
    reg = _approvals(request)
    if reg is None:
        return {"pending": []}
    pending = await reg.list_pending()
    # Project-scoped read: a project sees only its own + org-shared approvals.
    ctx = _ctx(request)
    if ctx is not None:
        pending = [r for r in pending if in_read_scope(r.get("project_id"), ctx)]
    return {"pending": pending}


async def _guard_approval_scope(
    reg: PendingApprovalsRegistry, decision_id: str, request: Request
) -> None:
    """Tenant-scope a by-id approval write before the decision/deny transition.

    Each pending row carries the ``project_id`` of its triggering signal. We
    enforce :func:`require_in_project_scope` (write) so a project may only act on
    its own (or org-shared) approvals; a cross-project decision is hidden (404).
    Unknown/already-decided ids fall through to the registry, which raises the
    KeyError/AlreadyDecidedError the routes already translate to 404/409."""
    ctx = _ctx(request)
    if ctx is None:
        return
    row = next(
        (r for r in await reg.list_pending() if r.get("decision_id") == decision_id),
        None,
    )
    if row is not None:
        require_in_project_scope(row.get("project_id"), ctx, write=True)


@router.post("/approvals/{decision_id}/approve")
async def approve(
    request: Request,
    decision_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    reg = _approvals(request)
    if reg is None:
        raise HTTPException(status_code=503, detail="directive approvals not available")
    await _guard_approval_scope(reg, decision_id, request)
    try:
        return await reg.approve(
            decision_id=decision_id,
            actor=actor_id_for(request),  # authenticated actor, not body-spoofable
            note=body.get("note"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown decision") from exc
    except AlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/approvals/{decision_id}/deny")
async def deny(
    request: Request,
    decision_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    reg = _approvals(request)
    if reg is None:
        raise HTTPException(status_code=503, detail="directive approvals not available")
    await _guard_approval_scope(reg, decision_id, request)
    try:
        return await reg.deny(
            decision_id=decision_id,
            actor=actor_id_for(request),  # authenticated actor, not body-spoofable
            note=body.get("note"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown decision") from exc
    except AlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
async def get_run_directive_summary(
    request: Request,
    run_id: str,
) -> dict[str, Any]:
    adapter = _evaluations(request)
    if adapter is None:
        return {"events": []}
    rows = await adapter.list_for_run(run_id=run_id)
    # Project-scoped read: a project sees only its own + org-shared run events.
    ctx = _ctx(request)
    if ctx is not None:
        rows = [r for r in rows if in_read_scope(r.get("project_id"), ctx)]
    return {"events": rows}


__all__ = ["router", "register", "SuppressedAlreadyPendingError"]
