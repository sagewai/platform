# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""RotationDriftSource — audit-event-driven profile-rotation signal.

Reads recent profile.drift_at_injection events from sealed_audit_events
(Sealed-i) and emits a SignalEvent for each one not previously seen by
this run. Seen-event ids are recorded in run.signals so each iii.C audit
event maps to exactly one directive signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sagewai.sealed.directives.models import SignalEvent
from sagewai.sealed.directives.signals import SignalContext


class _AuditReader(Protocol):
    async def recent_events(
        self, *, event_type: str, run_id: str, since: Any | None = None,
    ) -> list[Any]: ...


@dataclass
class RotationDriftSource:
    """Signal source — Sealed-i emitted profile.drift_at_injection for this run."""

    name: str = "rotation_drift"

    async def collect(
        self,
        *,
        run,
        step_index: int,
        context: SignalContext,
    ) -> list[SignalEvent]:
        reader: _AuditReader | None = context.audit_reader
        if reader is None:
            return []
        events = await reader.recent_events(
            event_type="profile.drift_at_injection",
            run_id=run.run_id,
            since=run.started_at,
        )
        seen = list(run.signals.get("rotation_drift_seen_event_ids", []))
        new_events = [e for e in events if e.id not in seen]
        if not new_events:
            return []
        run.signals["rotation_drift_seen_event_ids"] = seen + [
            e.id for e in new_events
        ]
        now = datetime.now(tz=timezone.utc)
        return [
            SignalEvent(
                kind="rotation_drift",
                run_id=run.run_id,
                project_id=getattr(run, "project_id", None),
                workflow_name=run.workflow_name,
                step_index=step_index,
                severity="info",
                detail=f"Profile {e.profile_id} rotated mid-run",
                evidence={
                    "profile_id": e.profile_id,
                    "drift_diff": e.details.get("drift_diff", {}),
                    "audit_event_id": e.id,
                },
                emitted_at=now,
            )
            for e in new_events
        ]
