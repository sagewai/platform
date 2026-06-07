# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Admin routes for /api/v1/admin/sealed/* (Sealed-i)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from sagewai.sealed.models import (
    Profile,
    ProfileMetadata,
    ProfileWritePayload,
)


class BackendStatus(BaseModel):
    enabled: bool
    healthy: bool = True
    profile_count: int | None = None
    addr: str | None = None
    namespace: str | None = None
    auth_method: str | None = None
    mount: str | None = None
    last_authenticated_at: str | None = None
    tls_verify: bool | None = None


class SealedStatus(BaseModel):
    master_key_configured: bool
    master_key_source: str
    master_key_last_rotated_at: str | None
    audit_retention_days: int
    reveal_rate_limit_per_admin_per_hour: int
    backends_registered: list[str]
    backends: dict[str, BackendStatus] = Field(default_factory=dict)


class SealedSystemConfig(BaseModel):
    profile_ref: str | None = None
    overrides: dict[str, str] = Field(default_factory=dict)


class SealedWorkflowConfig(BaseModel):
    profile_ref: str | None = None
    overrides: dict[str, str] = Field(default_factory=dict)


class SealedConfigDeleteResponse(BaseModel):
    cleared: bool


class SealedRevealResponse(BaseModel):
    value: str


class SealedAuditEvent(BaseModel):
    id: int
    event_type: str
    actor_type: str
    actor_id: str | None
    profile_id: str | None
    secret_key: str | None
    run_id: str | None
    project_id: str | None
    details: dict[str, Any]
    created_at: str


class EffectiveProfileResponse(BaseModel):
    env: dict[str, str]
    secret_keys: list[str]
    cascade_origins: dict[str, str]


router = APIRouter(prefix="/api/v1/admin/sealed", tags=["sealed"])


def register(app: FastAPI, store: Any) -> None:
    """Wire routes into the admin app."""
    app.state.sealed_store = store
    app.include_router(router)


def _backend():
    from sagewai.sealed.refs import ProfileRef, resolve_backend
    return resolve_backend(ProfileRef(scheme="builtin", path=""))


@router.get("/profiles", response_model=list[ProfileMetadata])
async def list_profiles() -> list[ProfileMetadata]:
    return await _backend().list_profiles()


@router.post("/profiles", response_model=Profile, status_code=201)
async def create_profile(payload: ProfileWritePayload) -> Profile:
    if not payload.id:
        raise HTTPException(status_code=400, detail="profile id required")
    return await _backend().save_profile(payload)


@router.get("/profiles/{profile_id}", response_model=ProfileMetadata)
async def get_profile_md(profile_id: str) -> ProfileMetadata:
    from sagewai.sealed.backend import ProfileNotFoundError
    try:
        return await _backend().get_profile_metadata(profile_id)
    except ProfileNotFoundError:
        raise HTTPException(status_code=404, detail={"id": profile_id}) from None


@router.get("/profiles/{profile_id}/full", response_model=Profile)
async def get_profile_full(profile_id: str) -> Profile:
    from sagewai.sealed.backend import ProfileNotFoundError
    try:
        return await _backend().get_profile(profile_id)
    except ProfileNotFoundError:
        raise HTTPException(status_code=404, detail={"id": profile_id}) from None


@router.put("/profiles/{profile_id}", response_model=Profile)
async def update_profile(profile_id: str, payload: ProfileWritePayload) -> Profile:
    payload_with_id = payload.model_copy(update={"id": profile_id})
    return await _backend().save_profile(payload_with_id)


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: str) -> None:
    from sagewai.sealed.backend import ProfileNotFoundError
    try:
        await _backend().delete_profile(profile_id)
    except ProfileNotFoundError:
        raise HTTPException(status_code=404, detail={"id": profile_id}) from None


# Per-admin reveal counters: admin_id → deque of timestamps
_REVEAL_HISTORY: dict[str, deque] = defaultdict(deque)


def _check_reveal_rate_limit(admin_id: str, limit_per_hour: int) -> bool:
    """Returns True if allowed, False if rate-limited."""
    now = time.time()
    cutoff = now - 3600
    history = _REVEAL_HISTORY[admin_id]
    while history and history[0] < cutoff:
        history.popleft()
    if len(history) >= limit_per_hour:
        return False
    history.append(now)
    return True


@router.post(
    "/profiles/{profile_id}/reveal/{secret_key}",
    response_model=SealedRevealResponse,
)
async def reveal_secret(profile_id: str, secret_key: str, request: Request) -> SealedRevealResponse:
    from sagewai.admin.audit import emit_audit
    from sagewai.sealed.backend import ProfileNotFoundError

    principal = getattr(request.state, "principal", None)
    actor = principal.actor_label if principal else "unknown"
    state = getattr(request.app.state, "sealed_store", None)
    if state is None:
        from sagewai.admin.state_file import AdminStateFile
        state = AdminStateFile()
    rate_limit = state.get_sealed_config().get("reveal_rate_limit_per_admin_per_hour", 30)

    if not _check_reveal_rate_limit(actor, rate_limit):
        raise HTTPException(
            status_code=429,
            detail={"retry_after": 3600, "limit": rate_limit},
        )

    try:
        full = await _backend().get_profile(profile_id)
    except ProfileNotFoundError:
        raise HTTPException(status_code=404, detail={"id": profile_id}) from None

    if secret_key not in full.secrets:
        raise HTTPException(status_code=404, detail={"secret_key": secret_key}) from None

    emit_audit(state, event_type="sealed.reveal", actor_label=actor,
               target=f"{profile_id}#{secret_key}")
    return SealedRevealResponse(value=full.secrets[secret_key])


@router.get("/system", response_model=SealedSystemConfig)
async def get_system_config() -> SealedSystemConfig:
    from sagewai.admin.state_file import AdminStateFile
    cfg = AdminStateFile().get_sealed_config()
    return SealedSystemConfig(
        profile_ref=cfg.get("system_profile_ref"),
        overrides=cfg.get("system_overrides") or {},
    )


@router.put("/system", response_model=SealedSystemConfig)
async def put_system_config(body: SealedSystemConfig) -> SealedSystemConfig:
    from sagewai.admin.state_file import AdminStateFile
    state = AdminStateFile()
    def _apply(d):
        sealed = d.setdefault("sealed", {})
        sealed["system_profile_ref"] = body.profile_ref
        sealed["system_overrides"] = body.overrides or {}
    state.mutate(_apply)
    return body


@router.get("/workflows/{name}", response_model=SealedWorkflowConfig)
async def get_workflow_config(name: str) -> SealedWorkflowConfig:
    from sagewai.admin.state_file import AdminStateFile
    cfg = AdminStateFile().get_workflow_sealed_config(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail={"workflow": name})
    return SealedWorkflowConfig(
        profile_ref=cfg.get("profile_ref"),
        overrides=cfg.get("overrides") or {},
    )


@router.put("/workflows/{name}", response_model=SealedWorkflowConfig)
async def put_workflow_config(name: str, body: SealedWorkflowConfig) -> SealedWorkflowConfig:
    from sagewai.admin.state_file import AdminStateFile
    state = AdminStateFile()
    def _apply(d):
        workflows = d.setdefault("workflows", {})
        if not isinstance(workflows, dict):
            # Coerce to dict shape if list
            workflows = {w["name"]: w for w in workflows if "name" in w}
            d["workflows"] = workflows
        workflows.setdefault(name, {})
        workflows[name]["security_profile_ref"] = body.profile_ref
        workflows[name]["security_overrides"] = body.overrides or {}
    state.mutate(_apply)
    return body


@router.delete("/workflows/{name}", status_code=204)
async def delete_workflow_config(name: str) -> None:
    from sagewai.admin.state_file import AdminStateFile
    state = AdminStateFile()
    def _apply(d):
        workflows = d.get("workflows") or {}
        if isinstance(workflows, dict) and name in workflows:
            workflows[name].pop("security_profile_ref", None)
            workflows[name].pop("security_overrides", None)
    state.mutate(_apply)


@router.get("/preview", response_model=EffectiveProfileResponse)
async def preview_cascade(
    project: str | None = None,
    workflow: str | None = None,
    user_profile_ref: str | None = None,
    user_overrides_json: str | None = None,
) -> EffectiveProfileResponse:
    import json as _json

    from sagewai.admin.state_file import AdminStateFile
    from sagewai.sealed.resolution import CascadeLevel, resolve_security_profile

    state = AdminStateFile()
    sealed_cfg = state.get_sealed_config()
    workflow_cfg = (
        state.get_workflow_sealed_config(workflow) if workflow else {}
    ) or {}

    user_overrides = (
        _json.loads(user_overrides_json) if user_overrides_json else None
    )

    levels = [
        CascadeLevel(
            name="system",
            profile_ref=sealed_cfg.get("system_profile_ref"),
            overrides=sealed_cfg.get("system_overrides"),
        ),
        CascadeLevel(
            name="workflow",
            profile_ref=workflow_cfg.get("profile_ref"),
            overrides=workflow_cfg.get("overrides"),
        ),
        CascadeLevel(
            name="user",
            profile_ref=user_profile_ref,
            overrides=user_overrides,
        ),
    ]

    # Audit-less preview to avoid noise — preview reads happen frequently
    eff = await resolve_security_profile(
        levels=levels,
        audit_writer=None,
        audit_context={"workflow_name": workflow, "project_id": project},
    )
    return EffectiveProfileResponse(
        env=eff.env,
        secret_keys=sorted(eff.secret_keys),
        cascade_origins=eff.cascade_origins,
    )


@router.get("/audit", response_model=list[SealedAuditEvent])
async def list_audit(
    profile_id: str | None = None,
    event_type: str | None = None,
    actor_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
) -> list[SealedAuditEvent]:
    # Stub for Sealed-i.A — populate via store query when admin app provides
    # the Postgres store via dependency injection. Plan 3b-i pattern.
    return []


@router.get("/status", response_model=SealedStatus)
async def sealed_status() -> SealedStatus:
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.sealed.master_key import MasterKeyMissing, resolve_master_key
    from sagewai.sealed.refs import _BACKENDS, list_registered_schemes

    try:
        _, source = resolve_master_key()
        configured = True
    except MasterKeyMissing:
        configured = False
        source = "none"
    state = AdminStateFile()
    cfg = state.get_sealed_config()
    vault_cfg = state.get_vault_config()

    schemes = list_registered_schemes()
    backends: dict[str, BackendStatus] = {}
    if "builtin" in schemes:
        try:
            metas = await _BACKENDS["builtin"].list_profiles()
            count = len(metas)
        except Exception:
            count = None
        backends["builtin"] = BackendStatus(
            enabled=True, healthy=True, profile_count=count,
        )
    if "vault" in schemes:
        try:
            metas = await _BACKENDS["vault"].list_profiles()
            count = len(metas)
            healthy = True
        except Exception:
            count = None
            healthy = False
        backends["vault"] = BackendStatus(
            enabled=True,
            healthy=healthy,
            profile_count=count,
            addr=vault_cfg.get("addr"),
            namespace=vault_cfg.get("namespace"),
            auth_method=vault_cfg.get("auth_method"),
            mount=vault_cfg.get("mount", "kv"),
            tls_verify=vault_cfg.get("tls_verify", True),
        )
    elif vault_cfg.get("enabled") is False:
        backends["vault"] = BackendStatus(enabled=False, healthy=False)

    return SealedStatus(
        master_key_configured=configured,
        master_key_source=source,
        master_key_last_rotated_at=cfg.get("master_key_last_rotated_at"),
        audit_retention_days=cfg.get("audit_retention_days", 365),
        reveal_rate_limit_per_admin_per_hour=cfg.get(
            "reveal_rate_limit_per_admin_per_hour", 30
        ),
        backends_registered=schemes,
        backends=backends,
    )
