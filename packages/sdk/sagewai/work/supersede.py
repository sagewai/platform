# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Terminal supersession of a Work by its replacement."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import WorkRecord
from sagewai.work.store import WorkStore

SUPERSEDED = "SUPERSEDED"


async def supersede_work(
    store: WorkStore,
    *,
    work_id: str,
    project_id: str,
    superseded_by: str,
    reason: str,
    actor_ref: str,
) -> WorkRecord:
    """Mark a Work superseded once; the replacement Work owns the step from here on."""
    record = await store.load_work(work_id, project_id=project_id)
    if record is None:
        raise KeyError(work_id)
    events = await store.read_events(work_id, project_id=project_id)
    if not any(event.event_type is WorkEventType.WORK_SUPERSEDED for event in events):
        await store.append_event(
            WorkEvent(
                id=str(uuid.uuid4()),
                project_id=project_id,
                work_id=work_id,
                sequence=events[-1].sequence + 1 if events else 1,
                event_type=WorkEventType.WORK_SUPERSEDED,
                actor_type="system",
                actor_ref=actor_ref,
                payload_json={"superseded_by": superseded_by, "reason": reason},
                created_at=datetime.now(timezone.utc),
            )
        )
    if record.status != SUPERSEDED:
        record = record.model_copy(
            update={
                "status": SUPERSEDED,
                "pending_gate": None,
                "active_run_id": None,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await store.save_work(record)
    return record


__all__ = ["SUPERSEDED", "supersede_work"]
