# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The Needs-you inbox: one project's Task attention merged with its Work attention.

Clarification attention fans out to one item per non-defaultable open question. Non-``merge:``
Work gates such as ``deploy_production:``, ``promote_rollout:`` and ``rollback:`` also carry
``decided_by="work"`` but are decided with ``sagewai work approve``; the console tells those
from Task gates by the ``gate_id`` prefix.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

from sagewai.work.store import WorkStore
from sagewai.work.tasks.channels import open_item
from sagewai.work.tasks.decisions import DUE_IN, TASK_GATES, URGENCY_BY_KIND
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import AttentionOwner, TaskRecord, TaskStatus
from sagewai.work.tasks.service import _open_questions
from sagewai.work.tasks.store import TaskStore

_NOW_STATUSES = frozenset(
    {TaskStatus.BLOCKED, TaskStatus.BUDGET_EXHAUSTED, TaskStatus.CONTROL_DEGRADED}
)


class DecisionItem(BaseModel):
    """One `Needs you` item, whoever owes it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["task", "work"]
    project_id: str
    task_id: str | None = None
    work_id: str | None = None
    attention_id: str
    attention_version: int | None = None
    summary: str
    urgency: Literal["now", "today", "this_week"]
    due_at: datetime
    gate_id: str | None = None
    decided_by: Literal["task", "work"] | None = None
    evidence_refs: tuple[str, ...] = ()


def _task_urgency(record: TaskRecord) -> str:
    """A Task that was never presented still has an urgency: its status says how hard it is."""
    return "now" if record.status in _NOW_STATUSES else "today"


def _gate_decision_target(
    events: Sequence[TaskEvent], gate_id: str
) -> tuple[Literal["task", "work"], str | None]:
    request = next(
        (
            event
            for event in reversed(sorted(events, key=lambda item: item.sequence))
            if event.event_type is TaskEventType.GATE_REQUESTED
            and str(event.payload_json["gate_id"]) == gate_id
        ),
        None,
    )
    if request is None:
        return ("task" if gate_id.startswith(TASK_GATES) else "work"), None
    payload = request.payload_json
    decided_by = (
        cast(Literal["task", "work"], str(payload["decided_by"]))
        if "decided_by" in payload
        else ("task" if gate_id.startswith(TASK_GATES) else "work")
    )
    return decided_by, str(payload["work_id"]) if decided_by == "work" else None


async def decision_inbox(
    *, task_store: TaskStore, work_store: WorkStore, project_id: str, now: datetime
) -> tuple[DecisionItem, ...]:
    """Every open decision in one project, soonest due first.

    Task items take their urgency, due time and summary from the presentation the coordinator
    recorded; a Task that is waiting on a human but whose presentation failed still appears,
    with an urgency derived from its status. Work items have no presentation, so their urgency
    comes from the attention kind and their due time from when the attention was raised.
    """
    items: list[DecisionItem] = []
    task_gate_ids: set[str] = set()
    for record in await task_store.list_records(project_id=project_id):
        if record.attention_owner is not AttentionOwner.USER:
            continue
        events = await task_store.read_events(record.task_id, project_id=project_id)
        questions = _open_questions(events)
        item = open_item(events)
        material_questions = tuple(
            (question, deadline)
            for question, deadline in questions
            if not bool(question["defaultable"])
        )
        if item is None and record.pending_gate is None and record.status not in _NOW_STATUSES:
            urgency = _task_urgency(record)
            for question, deadline in material_questions:
                items.append(
                    DecisionItem(
                        kind="task",
                        project_id=project_id,
                        task_id=record.task_id,
                        attention_id=str(question["id"]),
                        attention_version=int(question["attention_version"]),
                        summary=str(question["text"]),
                        urgency=urgency,
                        due_at=deadline or now + DUE_IN[urgency],
                    )
                )
            continue
        if item is None:
            urgency = _task_urgency(record)
            gate_id = record.pending_gate
            decided_by = None
            work_id = None
            if gate_id is not None:
                decided_by, work_id = _gate_decision_target(events, gate_id)
                task_gate_ids.add(gate_id)
            items.append(
                DecisionItem(
                    kind="task",
                    project_id=project_id,
                    task_id=record.task_id,
                    work_id=work_id,
                    attention_id=gate_id or record.waiting_reason or record.status.value,
                    summary=record.title,
                    urgency=urgency,
                    due_at=now + DUE_IN[urgency],
                    gate_id=gate_id,
                    decided_by=decided_by,
                )
            )
            continue
        gate_id = record.pending_gate if record.pending_gate == item.attention_id else None
        decided_by = None
        work_id = None
        if gate_id is not None:
            decided_by, work_id = _gate_decision_target(events, gate_id)
            task_gate_ids.add(gate_id)
        items.append(
            DecisionItem(
                kind="task",
                project_id=project_id,
                task_id=record.task_id,
                work_id=work_id,
                attention_id=item.attention_id,
                attention_version=None,
                summary=item.summary,
                urgency=item.urgency,
                due_at=item.due_at,
                gate_id=gate_id,
                decided_by=decided_by,
                evidence_refs=item.evidence_refs,
            )
        )
    for pending in await work_store.pending_attention(project_id=project_id):
        if pending.attention_id in task_gate_ids:
            continue
        urgency = URGENCY_BY_KIND[pending.kind.value]
        gate_id = pending.attention_id if pending.kind.value == "GATE_REQUESTED" else None
        items.append(
            DecisionItem(
                kind="work",
                project_id=project_id,
                work_id=pending.work_id,
                attention_id=pending.attention_id,
                summary=pending.summary,
                urgency=urgency,
                due_at=pending.created_at + DUE_IN[urgency],
                gate_id=gate_id,
                decided_by="work" if gate_id is not None else None,
                evidence_refs=pending.evidence_refs,
            )
        )
    return tuple(sorted(items, key=lambda item: (item.due_at, item.attention_id)))


__all__ = ["DecisionItem", "decision_inbox"]
