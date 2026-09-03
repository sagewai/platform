# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Number, fold, and fence one batch of Task events."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sagewai.work.tasks.events import TaskEvent, TaskEventType, fold_record
from sagewai.work.tasks.models import TaskRecord, TaskStatus
from sagewai.work.tasks.store import TaskStore
from sagewai.work.tasks.transitions import assert_transition

Entry = tuple[TaskEventType, dict[str, Any]]


def status_entry(record: TaskRecord, new: TaskStatus) -> Entry:
    """The only way a status changes; every change asserts the transition table."""
    assert_transition(record.status, new)
    return (TaskEventType.TASK_STATUS_CHANGED, {"status": new.value})


def build_events(
    record: TaskRecord,
    entries: Sequence[Entry],
    *,
    actor_type: str,
    actor_ref: str,
    now: datetime,
) -> tuple[TaskEvent, ...]:
    """Number entries consecutively from the projection's last folded sequence."""
    current = record.status
    events: list[TaskEvent] = []
    for offset, (event_type, payload) in enumerate(entries):
        if event_type is TaskEventType.TASK_STATUS_CHANGED:
            new = TaskStatus(payload["status"])
            assert_transition(current, new)
            current = new
        events.append(
            TaskEvent(
                id=str(uuid.uuid4()),
                project_id=record.project_id,
                task_id=record.task_id,
                sequence=record.last_event_sequence + offset + 1,
                event_type=event_type,
                actor_type=actor_type,
                actor_ref=actor_ref,
                payload_json=payload,
                created_at=now,
            )
        )
    return tuple(events)


class TaskWriter:
    """Append a fenced batch and return the projection folded over it."""

    def __init__(
        self, store: TaskStore, *, actor_type: str = "system", actor_ref: str = "coordinator"
    ) -> None:
        self._store = store
        self._actor_type = actor_type
        self._actor_ref = actor_ref

    async def append(
        self,
        record: TaskRecord,
        entries: Sequence[Entry],
        *,
        lease_epoch: int | None = None,
        now: datetime | None = None,
    ) -> TaskRecord:
        events = build_events(
            record,
            entries,
            actor_type=self._actor_type,
            actor_ref=self._actor_ref,
            now=now or datetime.now(timezone.utc),
        )
        return await self._store.append(
            task_id=record.task_id,
            project_id=record.project_id,
            events=events,
            expected_sequence=record.last_event_sequence + 1,
            record=fold_record(record, events),
            lease_epoch=lease_epoch,
        )


__all__ = ["Entry", "TaskWriter", "build_events", "status_entry"]
