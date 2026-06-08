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

from sagewai.admin.authz import require_org_admin
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


def _gate_org_admin(request: Request) -> None:
    """Org-admin write gate for artifact-destination mutations.

    FLAG: artifact destinations are keyed globally by workflow name in the admin
    state file (``workflows[name].artifact_destination``) with NO project_id
    column, so they cannot be cleanly project-scoped — a project column is a
    schema follow-up. Until then, gate the mutating routes (PUT/DELETE) to org
    owner/admin so a plain project member cannot rewrite or clear another
    project's workflow destination. No-op in single-org, and a no-op when no
    RequestContext is present (router mounted standalone without middleware)."""
    ctx = getattr(request.state, "context", None)
    if ctx is not None:
        require_org_admin(ctx)


def register(app: FastAPI, store: Any | None = None) -> None:
    """Wire routes into the admin app.

    ``store`` is the postgres workflow store, optional — used only for
    audit-event emission via AuditWriter when available.
    """
    if store is not None:
        app.state.artifact_store = store
    app.include_router(router)


@router.get(
    "/{name}/artifact_destination",
    response_model=ArtifactDestination,
)
async def get_workflow_artifact_destination(name: str, request: Request) -> ArtifactDestination:
    _gate_org_admin(request)  # global store, no project_id — org-admin interim (reads leak too)
    from sagewai.admin.state_file import AdminStateFile

    dest = AdminStateFile().get_workflow_artifact_destination(name)
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
    _gate_org_admin(request)
    # Structural validation (target shape per type). env_keys validation
    # against the resolved Sealed cascade happens at enqueue, not here —
    # the cascade isn't known at admin-config time.
    try:
        validate_target(body.type, body.target)
    except ArtifactDestinationConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from sagewai.admin.state_file import AdminStateFile

    AdminStateFile().set_workflow_artifact_destination(name, body)
    await _emit_admin_audit(
        event_type="artifact.admin_override.set",
        workflow_name=name,
        details={"destination": body.model_dump(mode="json")},
    )
    return body


@router.delete(
    "/{name}/artifact_destination",
    status_code=204,
)
async def delete_workflow_artifact_destination(name: str, request: Request) -> None:
    _gate_org_admin(request)
    from sagewai.admin.state_file import AdminStateFile

    AdminStateFile().clear_workflow_artifact_destination(name)
    await _emit_admin_audit(
        event_type="artifact.admin_override.cleared",
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
