# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Admin routes for /api/v1/admin/workflows/{name}/artifact_destination — Plan ART."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request

from sagewai.admin.authz import Resource, require
from sagewai.artifacts.models import (
    ArtifactDestination,
    ArtifactDestinationConfigError,
)
from sagewai.artifacts.validation import validate_target

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/workflows",
    tags=["artifacts"],
)


def _project_id(request: Request) -> str | None:
    ctx = getattr(request.state, "context", None)
    if ctx is None or ctx.tenancy_mode != "multi":
        return None
    return ctx.project_id


def _gate_resource(request: Request, *, write: bool = False) -> None:
    """Gate artifact destination access to the request's tenant resource scope."""
    ctx = getattr(request.state, "context", None)
    if ctx is None or ctx.tenancy_mode != "multi":
        return
    require(
        "resource:write" if write else "resource:read",
        ctx,
        on=Resource(ctx.org_id, ctx.project_id),
    )


def register(
    app: FastAPI,
    store: Any | None = None,
    state_file: Any | None = None,
) -> None:
    """Wire routes into the admin app.

    ``store`` is the postgres workflow store, optional — used only for
    audit-event emission via AuditWriter when available.
    """
    if store is not None:
        app.state.artifact_store = store
    if state_file is not None:
        app.state.artifact_state_file = state_file
    app.include_router(router)


def _state_file(request: Request):
    sf = getattr(getattr(request.app, "state", None), "artifact_state_file", None)
    if sf is not None:
        return sf
    from sagewai.admin.state_file import AdminStateFile

    return AdminStateFile()


# MT durability: kind for the generic project-scoped AdminResourceStore. The
# resource_id is the workflow name (one destination per workflow per project);
# ``name`` is the workflow name too, so the partial-unique index makes the
# (kind, project, name) tuple the natural key.
_KIND = "artifact_destination"


def _resource_store(request: Request):
    """The durable AdminResourceStore in multi-tenant mode, else ``None``.

    Reads ``request.app.state.resource_stores.admin_resource`` at request time so
    a lazily-built store (lifespan-constructed when none was injected) is picked
    up. In single-org mode this is ``None`` and the routes keep their unchanged
    scoped state-file path. If multi-tenant but unwired, fail closed (503) rather
    than silently fall through to the non-durable file path."""
    ctx = getattr(request.state, "context", None)
    rs = getattr(getattr(request.app, "state", None), "resource_stores", None)
    store = getattr(rs, "admin_resource", None) if rs is not None else None
    if store is None and ctx is not None and getattr(ctx, "tenancy_mode", None) == "multi":
        raise HTTPException(
            status_code=503,
            detail="Tenant resource store unavailable — please retry",
        )
    return store


@router.get(
    "/{name}/artifact_destination",
    response_model=ArtifactDestination,
)
async def get_workflow_artifact_destination(name: str, request: Request) -> ArtifactDestination:
    _gate_resource(request)

    store = _resource_store(request)
    if store is not None:
        row = await store.get_for(request.state.context, _KIND, name)
        dest = (
            ArtifactDestination.model_validate(row["destination"])
            if row and row.get("destination") is not None
            else None
        )
    else:
        dest = _state_file(request).get_workflow_artifact_destination(
            name, project_id=_project_id(request)
        )
    if dest is None:
        raise HTTPException(
            status_code=404,
            detail={"workflow": name, "artifact_destination": "not set"},
        )
    return dest


@router.put(
    "/{name}/artifact_destination",
    response_model=ArtifactDestination,
)
async def put_workflow_artifact_destination(
    name: str, body: ArtifactDestination, request: Request,
) -> ArtifactDestination:
    _gate_resource(request, write=True)
    # Structural validation (target shape per type). env_keys validation
    # against the resolved Sealed cascade happens at enqueue, not here —
    # the cascade isn't known at admin-config time.
    try:
        validate_target(body.type, body.target)
    except ArtifactDestinationConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    store = _resource_store(request)
    ctx = getattr(request.state, "context", None)
    if store is not None:
        await store.upsert_for(
            ctx,
            _KIND,
            name,
            {
                "workflow_name": name,
                "project_id": ctx.project_id,
                "destination": body.model_dump(mode="json"),
            },
            name=name,
        )
    else:
        state = _state_file(request)
        if ctx is not None and ctx.tenancy_mode == "multi":
            state.set_scoped_workflow_artifact_destination(
                name, body, project_id=ctx.project_id,
            )
        else:
            state.set_workflow_artifact_destination(name, body)
    await _emit_admin_audit(
        event_type="artifact.admin_override.set",
        workflow_name=name,
        details={"destination": body.model_dump(mode="json")},
    )
    await _emit_tenant_audit(
        request,
        "artifact_destination.upsert",
        workflow_name=name,
        details={"type": body.type.value},
    )
    return body


@router.delete(
    "/{name}/artifact_destination",
    status_code=204,
)
async def delete_workflow_artifact_destination(name: str, request: Request) -> None:
    _gate_resource(request, write=True)
    store = _resource_store(request)
    ctx = getattr(request.state, "context", None)
    if store is not None:
        # delete_for is write-scoped: a cross-project / org-shared row matches
        # zero rows (no-op), so a PA delete can't destroy PB's destination.
        await store.delete_for(ctx, _KIND, name)
    else:
        state = _state_file(request)
        if ctx is not None and ctx.tenancy_mode == "multi":
            state.clear_scoped_workflow_artifact_destination(
                name, project_id=ctx.project_id,
            )
        else:
            state.clear_workflow_artifact_destination(name)
    await _emit_admin_audit(
        event_type="artifact.admin_override.cleared",
        workflow_name=name,
        details={},
    )
    await _emit_tenant_audit(
        request,
        "artifact_destination.delete",
        workflow_name=name,
        details={},
    )


async def _emit_admin_audit(
    *,
    event_type: str,
    workflow_name: str,
    details: dict,
) -> None:
    """Best-effort audit emit via OTel structured log.

    Postgres emission requires a store + connection pool that the route
    layer doesn't have a clean handle on without a Depends; the Sealed
    audit pipeline handles its own postgres writes. For admin-config
    events we keep parity by emitting the OTel half (visible in
    Grafana / observability). The event_type is identical to what the
    runtime hook emits, so dashboards filter both sources cleanly.
    """
    logger.info(
        f"sagewai.artifacts.{event_type}",
        extra={
            "sagewai.event": f"sagewai.artifacts.{event_type}",
            "sagewai.workflow_name": workflow_name,
            "sagewai.details": details,
        },
    )


async def _emit_tenant_audit(
    request: Request,
    action: str,
    *,
    workflow_name: str,
    details: dict,
) -> None:
    ctx = getattr(request.state, "context", None)
    if ctx is None or ctx.tenancy_mode != "multi":
        return
    store = getattr(getattr(request.app, "state", None), "tenant_audit", None)
    if store is None:
        from sagewai.admin.tenant_audit import TenantAuditStore

        store = TenantAuditStore()
        request.app.state.tenant_audit = store
    try:
        await store.append(
            ctx.org_id,
            ctx.project_id,
            action,
            actor_user_id=ctx.actor.id,
            target_type="artifact_destination",
            target_id=workflow_name,
            metadata=details,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Audit log unavailable; operation not recorded — please retry",
        ) from exc
