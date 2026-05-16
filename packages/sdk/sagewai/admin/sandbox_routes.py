# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Admin routes for sandbox configuration (Plan 3b-i).

Three endpoint pairs:
  - /api/v1/admin/projects/<slug>/sandbox-defaults      (GET, PUT, DELETE)
  - /api/v1/admin/agents/<name>/sandbox-requirements    (GET, PUT, DELETE)
  - /api/v1/admin/sandbox/preview                       (GET — read-only cascade preview)

Wired into the admin app via register(app, store) called from admin/serve.py.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from sagewai.sandbox.models import (
    NetworkPolicy,
    SandboxImageVariant,
    SandboxMode,
)


class SandboxRequirementsPayload(BaseModel):
    """Request body for PUT (form submit). All required fields enforced."""

    sandbox_mode: SandboxMode
    image: str = Field(..., min_length=1)
    network_policy: NetworkPolicy
    required_secret_scopes: list[str] = Field(default_factory=list)


class SandboxRequirementsResponse(BaseModel):
    """Response shape for GET. Includes resolved variant for UI annotation."""

    sandbox_mode: SandboxMode
    image: str
    variant: SandboxImageVariant | None
    network_policy: NetworkPolicy
    required_secret_scopes: list[str]


class SandboxConfigDeleteResponse(BaseModel):
    cleared: bool


class SandboxResolutionOriginRoute(str, Enum):
    """Per-field cascade origin for the preview endpoint response."""

    EXPLICIT = "explicit"
    ADMIN_OVERRIDE = "admin_override"
    BLUEPRINT = "blueprint"
    PROJECT_DEFAULT = "project_default"
    SDK_DEFAULT = "sdk_default"


class SandboxResolutionField(BaseModel):
    value: str
    origin: SandboxResolutionOriginRoute


class SandboxResolutionPreview(BaseModel):
    sandbox_mode: SandboxResolutionField
    image: SandboxResolutionField
    variant: SandboxImageVariant | None
    network_policy: SandboxResolutionField
    resolved: SandboxRequirementsResponse


router = APIRouter(prefix="/api/v1/admin", tags=["sandbox-config"])


# ── helpers ──────────────────────────────────────────────────────────


def _get_project_or_404(state: Any, slug: str) -> dict:
    project = state.get_project(slug)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "type": "/errors/not-found",
                "title": "project not found",
                "scope": "project",
                "id": slug,
            },
        )
    return project


def _build_response(payload_dict: dict) -> SandboxRequirementsResponse:
    """Build a typed response with variant resolved via image_manifest."""
    from sagewai.sandbox import image_manifest

    return SandboxRequirementsResponse(
        sandbox_mode=SandboxMode(payload_dict["sandbox_mode"]),
        image=payload_dict["image"],
        variant=image_manifest.lookup_variant(payload_dict["image"]),
        network_policy=NetworkPolicy(payload_dict["network_policy"]),
        required_secret_scopes=payload_dict.get("required_secret_scopes", []),
    )


# ── project defaults ──────────────────────────────────────────────────


@router.get(
    "/projects/{slug}/sandbox-defaults",
    response_model=SandboxRequirementsResponse,
    responses={404: {"description": "project or sandbox config not found"}},
)
async def get_project_defaults(slug: str) -> SandboxRequirementsResponse:
    from sagewai.admin.state_file import AdminStateFile

    state = AdminStateFile()
    project = _get_project_or_404(state, slug)
    defaults = project.get("default_sandbox_requirements")
    if not defaults:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "type": "/errors/not-configured",
                "title": "no sandbox configuration set",
                "scope": "project",
                "id": slug,
            },
        )
    return _build_response(defaults)


@router.put(
    "/projects/{slug}/sandbox-defaults",
    response_model=SandboxRequirementsResponse,
)
async def put_project_defaults(
    slug: str, payload: SandboxRequirementsPayload
) -> SandboxRequirementsResponse:
    from sagewai.admin.state_file import AdminStateFile

    state = AdminStateFile()
    data = state._read()
    projects = data.setdefault("projects", [])
    target = None
    if isinstance(projects, list):
        target = next((p for p in projects if p.get("slug") == slug), None)
        if target is None:
            target = {"slug": slug}
            projects.append(target)
    elif isinstance(projects, dict):
        target = projects.setdefault(slug, {})

    target["default_sandbox_requirements"] = {
        "sandbox_mode": payload.sandbox_mode.value,
        "image": payload.image,
        "network_policy": payload.network_policy.value,
        "required_secret_scopes": payload.required_secret_scopes,
    }
    state._write(data)
    return _build_response(target["default_sandbox_requirements"])


@router.delete(
    "/projects/{slug}/sandbox-defaults",
    response_model=SandboxConfigDeleteResponse,
)
async def delete_project_defaults(slug: str) -> SandboxConfigDeleteResponse:
    from sagewai.admin.state_file import AdminStateFile

    state = AdminStateFile()
    project = state.get_project(slug)
    if project is None or "default_sandbox_requirements" not in project:
        return SandboxConfigDeleteResponse(cleared=False)

    data = state._read()
    projects = data.get("projects", [])
    if isinstance(projects, list):
        for p in projects:
            if p.get("slug") == slug:
                p.pop("default_sandbox_requirements", None)
                break
    elif isinstance(projects, dict):
        projects.get(slug, {}).pop("default_sandbox_requirements", None)
    state._write(data)
    return SandboxConfigDeleteResponse(cleared=True)


# ── agent overrides ──────────────────────────────────────────────────


def _get_or_create_agent(data: dict, name: str) -> dict:
    """Find or create an agent record in admin-state data."""
    agents = data.setdefault("agents", [])
    if isinstance(agents, list):
        for a in agents:
            if a.get("name") == name:
                return a
        new_agent = {"name": name}
        agents.append(new_agent)
        return new_agent
    if isinstance(agents, dict):
        return agents.setdefault(name, {})
    raise RuntimeError(f"unexpected agents shape: {type(agents)}")


@router.get(
    "/agents/{name}/sandbox-requirements",
    response_model=SandboxRequirementsResponse,
    responses={404: {"description": "agent or override not found"}},
)
async def get_agent_overrides(name: str) -> SandboxRequirementsResponse:
    from sagewai.admin.state_file import AdminStateFile

    state = AdminStateFile()
    agent = state.get_agent(name)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "type": "/errors/not-found",
                "title": "agent not found",
                "scope": "agent",
                "id": name,
            },
        )
    override = agent.get("sandbox_requirements_override")
    if not override:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "type": "/errors/not-configured",
                "title": "no sandbox override set",
                "scope": "agent",
                "id": name,
            },
        )
    return _build_response(override)


@router.put(
    "/agents/{name}/sandbox-requirements",
    response_model=SandboxRequirementsResponse,
)
async def put_agent_overrides(
    name: str, payload: SandboxRequirementsPayload
) -> SandboxRequirementsResponse:
    from sagewai.admin.state_file import AdminStateFile

    state = AdminStateFile()
    data = state._read()
    target = _get_or_create_agent(data, name)
    target["sandbox_requirements_override"] = {
        "sandbox_mode": payload.sandbox_mode.value,
        "image": payload.image,
        "network_policy": payload.network_policy.value,
        "required_secret_scopes": payload.required_secret_scopes,
    }
    state._write(data)
    return _build_response(target["sandbox_requirements_override"])


@router.delete(
    "/agents/{name}/sandbox-requirements",
    response_model=SandboxConfigDeleteResponse,
)
async def delete_agent_overrides(name: str) -> SandboxConfigDeleteResponse:
    from sagewai.admin.state_file import AdminStateFile

    state = AdminStateFile()
    agent = state.get_agent(name)
    if agent is None or "sandbox_requirements_override" not in agent:
        return SandboxConfigDeleteResponse(cleared=False)
    data = state._read()
    agents = data.get("agents", [])
    if isinstance(agents, list):
        for a in agents:
            if a.get("name") == name:
                a.pop("sandbox_requirements_override", None)
                break
    elif isinstance(agents, dict):
        agents.get(name, {}).pop("sandbox_requirements_override", None)
    state._write(data)
    return SandboxConfigDeleteResponse(cleared=True)


# ── preview endpoint ──────────────────────────────────────────────────


@router.get("/sandbox/preview", response_model=SandboxResolutionPreview)
async def get_sandbox_preview(
    project: str | None = None,
    agent: str | None = None,
    draft_mode: str | None = None,
    draft_image: str | None = None,
    draft_network_policy: str | None = None,
) -> SandboxResolutionPreview:
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.sandbox import image_manifest
    from sagewai.sandbox.resolution import (
        SandboxRequirements,
        SandboxResolutionOrigin,
        resolve_agent_requirements,
        resolve_requirements,
    )

    if not project and not agent:
        raise HTTPException(
            status_code=400,
            detail="At least one of 'project' or 'agent' query params required",
        )

    state = AdminStateFile()

    # Project defaults
    project_defaults = None
    if project:
        proj = state.get_project(project)
        if proj and (defaults := proj.get("default_sandbox_requirements")):
            project_defaults = SandboxRequirements(
                sandbox_mode=SandboxMode(defaults["sandbox_mode"]),
                image=defaults["image"],
                variant=image_manifest.lookup_variant(defaults["image"]),
                network_policy=NetworkPolicy(defaults["network_policy"]),
            )

    # Agent admin override (Blueprint requirements not visible from admin-state)
    agent_admin_override = None
    if agent:
        agent_admin_override = await resolve_agent_requirements(
            agent, blueprint_requirements=None
        )

    # Draft overrides come in as level-1 explicit
    explicit_mode = SandboxMode(draft_mode) if draft_mode else None
    explicit_image = draft_image
    explicit_network = NetworkPolicy(draft_network_policy) if draft_network_policy else None

    resolved, origins = await resolve_requirements(
        explicit_mode=explicit_mode,
        explicit_image=explicit_image,
        explicit_network_policy=explicit_network,
        agent_requirements=agent_admin_override,
        project_defaults=project_defaults,
        with_origins=True,
    )

    # Re-tag generic AGENT origin → ADMIN_OVERRIDE
    # (Blueprint sourcing is not visible from admin-state alone — those
    # values would only appear via FleetMissionAdapter at run time.)
    for field, origin in list(origins.items()):
        if origin is SandboxResolutionOrigin.AGENT:
            origins[field] = SandboxResolutionOrigin.ADMIN_OVERRIDE

    def _field(field_name: str, value: str) -> SandboxResolutionField:
        return SandboxResolutionField(
            value=value,
            origin=SandboxResolutionOriginRoute(origins[field_name].value),
        )

    return SandboxResolutionPreview(
        sandbox_mode=_field("sandbox_mode", resolved.sandbox_mode.value),
        image=_field("image", resolved.image),
        variant=resolved.variant,
        network_policy=_field("network_policy", resolved.network_policy.value),
        resolved=SandboxRequirementsResponse(
            sandbox_mode=resolved.sandbox_mode,
            image=resolved.image,
            variant=resolved.variant,
            network_policy=resolved.network_policy,
            required_secret_scopes=[],
        ),
    )


def register(app: FastAPI, store: Any) -> None:
    """Wire the sandbox-config router onto the admin app.

    Called from admin/serve.py during app construction.
    """
    app.state.sandbox_store = store
    app.include_router(router)
