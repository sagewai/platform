# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Append-only Work-domain events."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

from sagewai.work.models import ExecutionAttempt


class WorkEventType(str, Enum):
    """Initial durable Work-domain event vocabulary."""

    WORK_CREATED = "WORK_CREATED"
    CONTRACT_PROPOSED = "CONTRACT_PROPOSED"
    CONTRACT_ACCEPTED = "CONTRACT_ACCEPTED"
    ASSUMPTION_RECORDED = "ASSUMPTION_RECORDED"
    STAGE_STARTED = "STAGE_STARTED"
    STAGE_COMPLETED = "STAGE_COMPLETED"
    EXECUTION_RECORDED = "EXECUTION_RECORDED"
    VERIFICATION_RECORDED = "VERIFICATION_RECORDED"
    REVIEW_RECORDED = "REVIEW_RECORDED"
    GATE_REQUESTED = "GATE_REQUESTED"
    GATE_DECIDED = "GATE_DECIDED"
    RELEASE_CREATED = "RELEASE_CREATED"
    DEPLOYMENT_RECORDED = "DEPLOYMENT_RECORDED"
    OBSERVATION_RECORDED = "OBSERVATION_RECORDED"
    OPERATOR_DISCIPLINE_RECORDED = "OPERATOR_DISCIPLINE_RECORDED"
    CONTROL_DEGRADED = "CONTROL_DEGRADED"
    CONTROL_RESTORED = "CONTROL_RESTORED"
    ROLLBACK_RECORDED = "ROLLBACK_RECORDED"
    TRIAGE_CREATED = "TRIAGE_CREATED"
    WORK_BLOCKED = "WORK_BLOCKED"
    WORK_COMPLETED = "WORK_COMPLETED"


class WorkEvent(BaseModel):
    """One immutable business event in a WorkItem stream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str | None
    work_id: str
    sequence: int
    event_type: WorkEventType
    actor_type: str
    actor_ref: str | None
    payload_json: dict[str, Any]
    created_at: datetime


def active_control_degradations(
    events: list[WorkEvent],
) -> dict[str, WorkEvent]:
    """Fold control events into the active degradation receipts."""

    active: dict[str, WorkEvent] = {}
    for event in events:
        if event.event_type is WorkEventType.CONTROL_DEGRADED:
            for precondition_id in event.payload_json.get("failed_preconditions", ()):
                active[str(precondition_id)] = event
        elif event.event_type is WorkEventType.CONTROL_RESTORED:
            for precondition_id in event.payload_json.get("precondition_ids", ()):
                active.pop(str(precondition_id), None)
    return active


def active_control_precondition_ids(events: list[WorkEvent]) -> set[str]:
    """Return the currently degraded precondition IDs."""

    return set(active_control_degradations(events))


def execution_attempt_from_events(
    events: list[WorkEvent],
    run_id: str,
) -> ExecutionAttempt | None:
    """Project one canonical attempt receipt from the existing Work events."""

    started = next(
        (
            event
            for event in events
            if event.event_type is WorkEventType.STAGE_STARTED
            and event.payload_json.get("run_id") == run_id
        ),
        None,
    )
    if started is None:
        return None
    execution = next(
        (
            event
            for event in reversed(events)
            if event.event_type is WorkEventType.EXECUTION_RECORDED
            and event.payload_json.get("run_id") == run_id
        ),
        None,
    )
    completed = next(
        (
            event
            for event in reversed(events)
            if event.event_type is WorkEventType.STAGE_COMPLETED
            and event.payload_json.get("run_id") == run_id
        ),
        None,
    )
    degraded = next(
        (
            event
            for event in reversed(events)
            if event.event_type is WorkEventType.CONTROL_DEGRADED
            and event.payload_json.get("run_id") == run_id
        ),
        None,
    )
    status = "running"
    completed_at = None
    if execution is not None:
        status = str(execution.payload_json["status"])
        completed_at = execution.created_at
    elif degraded is not None:
        status = "blocked"
        completed_at = degraded.created_at
    profile_context = (
        completed.payload_json.get("profile_context", {})
        if completed is not None
        else execution.payload_json.get("profile_context", {})
        if execution is not None
        else {}
    )
    return ExecutionAttempt.model_validate(
        {
            "id": run_id,
            "project_id": started.project_id,
            "work_id": started.work_id,
            "stage": started.payload_json["stage"],
            "runtime": started.payload_json["runtime"],
            "workspace_ref": started.payload_json.get("workspace_ref"),
            "artifact_refs": (
                execution.payload_json.get("artifact_refs", ())
                if execution is not None
                else ()
            ),
            "status": status,
            "started_at": started.created_at,
            "completed_at": completed_at,
            "profile_context": profile_context,
        }
    )
