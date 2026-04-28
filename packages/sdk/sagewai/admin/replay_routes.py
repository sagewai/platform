# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Admin routes for /api/v1/admin/workflows/runs/{run_id}/replay (Sealed-iii.C).

Three endpoints:

- POST /preview — surface warnings + blockers before mutating
- POST       — commit; creates a new replay run linked to original
- GET  /replays — list replays of an original run

Workflow lookup uses ``app.state.workflow_registry`` populated by the
admin app (or the test harness). Stores expose
``list_replays_of(run_id)`` from Task 5.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel


class ReplayPreviewRequest(BaseModel):
    from_step: int = 0


class ReplayCommitRequest(BaseModel):
    from_step: int = 0
    confirm_warnings: bool = False


router = APIRouter(
    prefix="/api/v1/admin/workflows/runs",
    tags=["sealed-replay"],
)


def _registry(app: FastAPI) -> dict:
    return getattr(app.state, "workflow_registry", {}) or {}


def _store(app: FastAPI) -> Any:
    return getattr(app.state, "replay_store", None)


def register(
    app: FastAPI,
    store: Any,
    workflow_registry: dict,
) -> None:
    """Wire replay routes into the admin app."""
    app.state.replay_store = store
    app.state.workflow_registry = workflow_registry
    app.include_router(router)


async def _load_run_or_404(store, run_id: str):
    rows = await store._pool.fetch(
        "SELECT workflow_name FROM workflow_runs WHERE run_id = $1 LIMIT 1",
        run_id,
    )
    if not rows:
        raise HTTPException(404, f"run {run_id!r} not found")
    run = await store.load_run(rows[0]["workflow_name"], run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    return run


async def _preview_for(store, registry, run_id: str, from_step: int) -> dict:
    run = await _load_run_or_404(store, run_id)
    wf = registry.get(run.workflow_name)
    if wf is None:
        raise HTTPException(
            404,
            f"workflow {run.workflow_name!r} not registered with this admin app",
        )

    from sagewai.sealed.replay import compute_code_hash

    warnings: list[dict] = []
    blockers: list[dict] = []

    current_hash = compute_code_hash(wf)
    if run.code_hash and run.code_hash != current_hash:
        blockers.append(
            {
                "type": "workflow_version_mismatch",
                "original_hash": run.code_hash,
                "current_hash": current_hash,
            }
        )

    if run.execution_mode.value != "bare":
        for idx, step_def in enumerate(wf._steps):
            if idx >= from_step:
                break
            rec = run.steps.get(step_def.name)
            if rec is None or rec.injection_snapshot is None:
                blockers.append(
                    {
                        "type": "legacy_run_no_snapshot",
                        "step_name": step_def.name,
                    }
                )
                break

    if run.execution_mode.value == "full_jit":
        had = await wf._original_run_used_callbacks(run)
        if had:
            blockers.append(
                {"type": "mode_not_replayable", "mode": "full_jit"}
            )

    snapshot_keys: dict[str, dict] = {}
    for name, rec in run.steps.items():
        if rec.injection_snapshot is not None:
            snapshot_keys[name] = {
                "effective_env_keys": list(
                    rec.injection_snapshot.effective_env_keys
                ),
                "effective_secret_keys": list(
                    rec.injection_snapshot.effective_secret_keys
                ),
            }

    return {
        "original_run_id": run_id,
        "execution_mode": run.execution_mode.value,
        "security_profile_ref": run.security_profile_ref,
        "snapshot_keys_per_step": snapshot_keys,
        "warnings": warnings,
        "blockers": blockers,
    }


@router.post("/{run_id}/replay/preview")
async def preview(request: Request, run_id: str, body: ReplayPreviewRequest):
    return await _preview_for(
        _store(request.app), _registry(request.app), run_id, body.from_step
    )


@router.post("/{run_id}/replay", status_code=201)
async def commit(request: Request, run_id: str, body: ReplayCommitRequest):
    store = _store(request.app)
    registry = _registry(request.app)
    preview_body = await _preview_for(store, registry, run_id, body.from_step)
    if preview_body["blockers"]:
        raise HTTPException(
            422, detail={"blockers": preview_body["blockers"]}
        )
    if preview_body["warnings"] and not body.confirm_warnings:
        raise HTTPException(
            422,
            detail={
                "warnings": preview_body["warnings"],
                "hint": "set confirm_warnings=true to proceed",
            },
        )

    run = await _load_run_or_404(store, run_id)
    wf = registry[run.workflow_name]
    new_run_id = await wf.replay_from(
        run_id,
        from_step=body.from_step,
        actor_id="admin",
    )
    return {"new_run_id": new_run_id, "replay_of_run_id": run_id}


@router.get("/{run_id}/replays")
async def list_replays(request: Request, run_id: str):
    store = _store(request.app)
    runs = await store.list_replays_of(run_id)
    return {
        "replays": [
            {
                "run_id": r.run_id,
                "replay_from_step": r.replay_from_step,
                "started_at": r.started_at,
                "status": r.status.value,
            }
            for r in runs
        ],
    }
