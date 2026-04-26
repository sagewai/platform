# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Admin routes for /api/v1/admin/sealed/revocations (Sealed-iii.A)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from sagewai.sealed.revocation import Revocation


class RevokeRequest(BaseModel):
    profile_id: str
    secret_key: str | None = None  # None = bulk profile revoke
    reason: str = Field(min_length=1)
    hard: bool = False
    current_keys: list[str] | None = None  # required when secret_key is None


class RevokeResponse(BaseModel):
    revocations: list[Revocation]
    affected_runs: list[str] = Field(default_factory=list)


class PreviewResponse(BaseModel):
    affected_runs: list[str]


# Per-admin revoke counters
_REVOKE_HISTORY: dict[str, deque] = defaultdict(deque)


def _check_revoke_rate_limit(admin_id: str, limit_per_hour: int) -> bool:
    now = time.time()
    cutoff = now - 3600
    history = _REVOKE_HISTORY[admin_id]
    while history and history[0] < cutoff:
        history.popleft()
    if len(history) >= limit_per_hour:
        return False
    history.append(now)
    return True


router = APIRouter(prefix="/api/v1/admin/sealed/revocations", tags=["sealed"])


def _registry(app: FastAPI) -> Any:
    """Pull the registry off app.state (set by register())."""
    return app.state.sealed_revocation_registry


def register(app: FastAPI, store: Any) -> None:
    """Wire revocation routes into the admin app.

    `store` is a PostgresStore. The registry uses store + a derived AuditWriter.
    """
    from sagewai.sealed.audit import AuditWriter
    from sagewai.sealed.revocation import RevocationRegistry

    app.state.sealed_revocation_registry = RevocationRegistry(
        store, audit_writer=AuditWriter(store)
    )
    app.include_router(router)


@router.post("", response_model=RevokeResponse, status_code=201)
async def post_revocation(request: Request, body: RevokeRequest) -> RevokeResponse:
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.sealed.revocation import RevocationConflictError

    state = AdminStateFile()
    rate_limit = (
        state.get_sealed_config()
        .get("revoke_rate_limit_per_admin_per_hour", 60)
    )
    admin_id = "default-admin"  # TODO: extract from auth context when integrated

    if not _check_revoke_rate_limit(admin_id, rate_limit):
        raise HTTPException(
            status_code=429,
            detail={"retry_after": 3600, "limit": rate_limit},
        )

    reg = request.app.state.sealed_revocation_registry
    try:
        rows = await reg.revoke(
            profile_id=body.profile_id,
            secret_key=body.secret_key,
            reason=body.reason,
            actor_id=admin_id,
            hard=body.hard,
            current_keys=body.current_keys,
        )
    except RevocationConflictError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc)}) from None

    affected: list[str] = []
    if body.hard and rows:
        for r in rows:
            affected.extend(await reg.runs_using_revocation(r.id))
        affected = sorted(set(affected))

    return RevokeResponse(revocations=rows, affected_runs=affected)


@router.get("", response_model=list[Revocation])
async def list_revocations(
    request: Request,
    profile_id: str | None = None,
    include_lifted: bool = False,
    limit: int = 200,
) -> list[Revocation]:
    reg = request.app.state.sealed_revocation_registry
    return await (
        reg.list_all(profile_id=profile_id, include_lifted=True, limit=limit)
        if include_lifted
        else reg.list_active(profile_id=profile_id, limit=limit)
    )


@router.get("/preview", response_model=PreviewResponse)
async def preview_revocation(
    request: Request,
    profile_id: str,
    secret_key: str | None = None,
) -> PreviewResponse:
    """Read-only: list runs that would be affected by a hard revoke."""
    reg = request.app.state.sealed_revocation_registry
    if secret_key is not None:
        rows = await reg._store._pool.fetch(
            """
            SELECT run_id FROM workflow_runs
            WHERE status = 'running'
              AND security_profile_ref = $1
              AND $2 = ANY(effective_secret_keys)
            """,
            profile_id, secret_key,
        )
    else:
        rows = await reg._store._pool.fetch(
            """
            SELECT run_id FROM workflow_runs
            WHERE status = 'running'
              AND security_profile_ref = $1
              AND effective_secret_keys IS NOT NULL
              AND array_length(effective_secret_keys, 1) > 0
            """,
            profile_id,
        )
    return PreviewResponse(affected_runs=sorted({r["run_id"] for r in rows}))


@router.delete("/{revocation_id}", response_model=Revocation)
async def lift_revocation(request: Request, revocation_id: int) -> Revocation:
    from sagewai.sealed.revocation import RevocationConflictError

    reg = request.app.state.sealed_revocation_registry
    try:
        return await reg.lift(revocation_id, actor_id="default-admin")
    except LookupError:
        raise HTTPException(status_code=404, detail={"id": revocation_id}) from None
    except RevocationConflictError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc)}) from None
