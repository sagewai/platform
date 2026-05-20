# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Mission-state writes: record_result, progress_track, request_approval.

These builtins reach the running ``Mission`` through a resolver injected
by the autopilot at startup. Calling them before the resolver is bound
raises :class:`MissionNotBoundError` — explicit, fail-loud — so missing
wire-up is immediately visible.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable


class MissionNotBoundError(RuntimeError):
    """The autopilot hasn't called set_mission_resolver yet."""


def _default_resolver(mission_id: str) -> Any:
    raise MissionNotBoundError(
        "No mission resolver bound. The autopilot binds one at startup; "
        "this likely means the tool was called before runtime init."
    )


_resolver: Callable[[str], Any] = _default_resolver


def set_mission_resolver(resolver: Callable[[str], Any]) -> None:
    """Called once by the autopilot at startup to bind mission lookup."""
    global _resolver
    _resolver = resolver


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def record_result(payload: dict[str, Any]) -> dict[str, Any]:
    m = _resolver(payload["mission_id"])
    m.record_step_result(payload["step_id"], payload["payload"])
    return {"stored": True, "timestamp": _now_iso()}


async def progress_track(payload: dict[str, Any]) -> dict[str, Any]:
    m = _resolver(payload["mission_id"])
    done = payload["units_done"]
    total = payload.get("units_total")
    progress = (done / total) if total and total > 0 else 0.0
    note = payload.get("note")
    m.record_progress(progress, note=note)
    return {"progress": progress, "note": note}


async def request_approval(payload: dict[str, Any]) -> dict[str, Any]:
    m = _resolver(payload["mission_id"])
    request_id = str(uuid.uuid4())
    m.emit_hitl_request(request_id, reason=payload["reason"], payload=payload.get("payload"))
    return {"request_id": request_id, "status": "pending"}
