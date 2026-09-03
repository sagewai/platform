# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Append-only Task events and pure projection folds."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

from sagewai.work.tasks.models import (
    TERMINAL_STATUSES,
    AttentionOwner,
    BoardColumn,
    BudgetUsed,
    TaskRecord,
    TaskStatus,
)


class TaskEventType(str, Enum):
    TASK_CREATED = "TASK_CREATED"
    BRIEF_RECORDED = "BRIEF_RECORDED"
    BRIEF_AMENDED = "BRIEF_AMENDED"
    INTAKE_RECORDED = "INTAKE_RECORDED"
    CLARIFICATION_REQUESTED = "CLARIFICATION_REQUESTED"
    CLARIFICATION_ANSWERED = "CLARIFICATION_ANSWERED"
    CLARIFICATION_DEFAULTED = "CLARIFICATION_DEFAULTED"
    TASK_MESSAGE = "TASK_MESSAGE"
    PLAN_PROPOSED = "PLAN_PROPOSED"
    PLAN_ACCEPTED = "PLAN_ACCEPTED"
    GATE_REQUESTED = "GATE_REQUESTED"
    GATE_DECIDED = "GATE_DECIDED"
    CYCLE_STARTED = "CYCLE_STARTED"
    STEP_WORK_STARTED = "STEP_WORK_STARTED"
    STEP_WORK_OUTCOME = "STEP_WORK_OUTCOME"
    STEP_WORK_SUPERSEDED = "STEP_WORK_SUPERSEDED"
    RUNTIME_SELECTED = "RUNTIME_SELECTED"
    BASE_ADVANCED = "BASE_ADVANCED"
    REPOSITORY_LEASE_ACQUIRED = "REPOSITORY_LEASE_ACQUIRED"
    REPOSITORY_LEASE_RELEASED = "REPOSITORY_LEASE_RELEASED"
    ASSESSMENT_RECORDED = "ASSESSMENT_RECORDED"
    REPLAN_PROPOSED = "REPLAN_PROPOSED"
    SPEND_RESERVED = "SPEND_RESERVED"
    SPEND_SETTLED = "SPEND_SETTLED"
    BUDGET_RECORDED = "BUDGET_RECORDED"
    BUDGET_UPDATED = "BUDGET_UPDATED"
    HEALTH_SIGNAL = "HEALTH_SIGNAL"
    HEALTH_ACTION = "HEALTH_ACTION"
    DECISION_RECORDED = "DECISION_RECORDED"
    DECISION_SCHEDULED = "DECISION_SCHEDULED"
    NOTIFICATION_PRESENTED = "NOTIFICATION_PRESENTED"
    ACTION_INTENT_RECORDED = "ACTION_INTENT_RECORDED"
    ACTION_RESULT_RECORDED = "ACTION_RESULT_RECORDED"
    OBSERVATION_RECORDED = "OBSERVATION_RECORDED"
    TRACKING_ISSUE_RECORDED = "TRACKING_ISSUE_RECORDED"
    ATTENTION_CHANGED = "ATTENTION_CHANGED"
    CYCLE_COMPLETED = "CYCLE_COMPLETED"
    TASK_STATUS_CHANGED = "TASK_STATUS_CHANGED"
    CONTROL_DEGRADED = "CONTROL_DEGRADED"
    CONTROL_RESTORED = "CONTROL_RESTORED"
    COMMAND_RECEIPT = "COMMAND_RECEIPT"


class TaskEvent(BaseModel):
    """One immutable event in a Task stream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    task_id: str
    sequence: int
    event_type: TaskEventType
    actor_type: str
    actor_ref: str | None
    payload_json: dict[str, Any]
    created_at: datetime


_USER_STATUSES = frozenset(
    {TaskStatus.BLOCKED, TaskStatus.BUDGET_EXHAUSTED, TaskStatus.CONTROL_DEGRADED}
)
_ACTIVE_STATUSES = frozenset(
    {
        TaskStatus.PLANNING,
        TaskStatus.CLARIFYING,
        TaskStatus.PLAN_PROPOSED,
        TaskStatus.EXECUTING,
        TaskStatus.ASSESSING,
    }
)
_PLANNED_STATUSES = frozenset({TaskStatus.SCHEDULED, TaskStatus.PAUSED})


def derive_attention(
    *,
    status: TaskStatus,
    pending_gate: str | None,
    pending_material_questions: int,
    explicit: tuple[AttentionOwner, str] | None,
) -> tuple[AttentionOwner | None, str | None]:
    """Who the Task waits on. A human-owed wait always wins over explicit owners."""
    if status in _USER_STATUSES:
        return AttentionOwner.USER, status.value.lower()
    if pending_gate is not None:
        return AttentionOwner.USER, f"gate:{pending_gate}"
    if pending_material_questions > 0:
        return AttentionOwner.USER, f"questions:{pending_material_questions}"
    if status is TaskStatus.CLARIFYING:
        return AttentionOwner.SYSTEM, "awaiting defaults"
    if explicit is not None and status not in TERMINAL_STATUSES:
        return explicit
    if status not in _ACTIVE_STATUSES:
        return None, None
    return AttentionOwner.SYSTEM, "working"


def board_column(status: TaskStatus, owner: AttentionOwner | None) -> BoardColumn:
    if status in TERMINAL_STATUSES:
        return BoardColumn.DONE
    if owner is AttentionOwner.USER:
        return BoardColumn.NEEDS_YOU
    if status is TaskStatus.PLANNING:
        return BoardColumn.INBOX
    if status in _PLANNED_STATUSES:
        return BoardColumn.PLANNED
    return BoardColumn.IN_PROGRESS


def fold_record(previous: TaskRecord, events: Iterable[TaskEvent]) -> TaskRecord:
    """Apply events not yet reflected in ``previous``, in sequence order.

    Pure; events at or below ``previous.last_event_sequence`` are ignored.

    Payload keys read by the projection:
    TASK_STATUS_CHANGED: status
    CLARIFICATION_REQUESTED: questions[*].defaultable
    CLARIFICATION_ANSWERED: material
    CLARIFICATION_DEFAULTED: none
    PLAN_ACCEPTED: version
    GATE_REQUESTED: gate_id
    GATE_DECIDED: gate_id
    CYCLE_STARTED: cycle
    CYCLE_COMPLETED: next_run_at
    ATTENTION_CHANGED: owner, reason
    BUDGET_RECORDED: budget_used
    TRACKING_ISSUE_RECORDED: url
    """
    values = previous.model_dump()
    explicit: tuple[AttentionOwner, str] | None = None
    if previous.attention_owner in {AttentionOwner.SYSTEM, AttentionOwner.EXTERNAL}:
        explicit = (previous.attention_owner, previous.waiting_reason or "")
    updated_at = previous.updated_at
    for event in sorted(events, key=lambda item: item.sequence):
        if event.task_id != previous.task_id or event.project_id != previous.project_id:
            continue
        if event.sequence <= values["last_event_sequence"]:
            continue
        payload = event.payload_json
        event_type = event.event_type
        if event_type is TaskEventType.TASK_STATUS_CHANGED:
            explicit = None
            values["status"] = TaskStatus(str(payload["status"]))
            if values["status"] in TERMINAL_STATUSES:
                values["pending_gate"] = None
                values["pending_questions"] = 0
                values["pending_material_questions"] = 0
        elif event_type is TaskEventType.CLARIFICATION_REQUESTED:
            questions = payload["questions"]
            values["pending_questions"] += len(questions)
            values["pending_material_questions"] += sum(
                1 for question in questions if not bool(question["defaultable"])
            )
        elif event_type in {
            TaskEventType.CLARIFICATION_ANSWERED,
            TaskEventType.CLARIFICATION_DEFAULTED,
        }:
            values["pending_questions"] = max(0, values["pending_questions"] - 1)
            if event_type is TaskEventType.CLARIFICATION_ANSWERED and bool(
                payload["material"]
            ):
                values["pending_material_questions"] = max(
                    0,
                    values["pending_material_questions"] - 1,
                )
        elif event_type is TaskEventType.PLAN_ACCEPTED:
            values["plan_version"] = int(payload["version"])
        elif event_type is TaskEventType.GATE_REQUESTED:
            values["pending_gate"] = str(payload["gate_id"])
        elif event_type is TaskEventType.GATE_DECIDED:
            if values["pending_gate"] == payload.get("gate_id"):
                values["pending_gate"] = None
        elif event_type is TaskEventType.CYCLE_STARTED:
            values["current_cycle"] = int(payload["cycle"])
            values["next_run_at"] = None
        elif event_type is TaskEventType.CYCLE_COMPLETED:
            raw = payload.get("next_run_at")
            values["next_run_at"] = datetime.fromisoformat(raw) if raw else None
            explicit = None
        elif event_type is TaskEventType.ATTENTION_CHANGED:
            explicit = (
                AttentionOwner(str(payload["owner"])),
                str(payload.get("reason") or ""),
            )
        elif event_type is TaskEventType.BUDGET_RECORDED:
            values["budget_used"] = BudgetUsed.model_validate(payload["budget_used"])
        elif event_type is TaskEventType.TRACKING_ISSUE_RECORDED:
            values["tracking_issue_url"] = str(payload["url"])
        updated_at = max(updated_at, event.created_at)
        values["last_event_sequence"] = event.sequence
    owner, reason = derive_attention(
        status=values["status"],
        pending_gate=values["pending_gate"],
        pending_material_questions=values["pending_material_questions"],
        explicit=explicit,
    )
    values["attention_owner"] = owner
    values["waiting_reason"] = reason
    values["board_column"] = board_column(values["status"], owner)
    values["updated_at"] = updated_at
    return TaskRecord.model_validate(values)


__all__ = ["TaskEvent", "TaskEventType", "board_column", "derive_attention", "fold_record"]
