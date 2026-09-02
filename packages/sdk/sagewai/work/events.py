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
    EXTERNAL_OUTCOME_RECORDED = "EXTERNAL_OUTCOME_RECORDED"
    OPERATOR_DISCIPLINE_RECORDED = "OPERATOR_DISCIPLINE_RECORDED"
    WORK_SUPERSEDED = "WORK_SUPERSEDED"
    RUNTIME_SELECTED = "RUNTIME_SELECTED"
    BASE_MOVED = "BASE_MOVED"
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

    ordered = sorted(events, key=lambda event: event.sequence)
    started = next(
        (
            event
            for event in reversed(ordered)
            if event.event_type is WorkEventType.STAGE_STARTED
            and event.payload_json.get("run_id") == run_id
        ),
        None,
    )
    if started is None:
        return None

    scoped = (
        event
        for event in ordered
        if event.sequence >= started.sequence
        and event.project_id == started.project_id
        and event.work_id == started.work_id
        and event.payload_json.get("run_id") == run_id
    )
    status = "running"
    completed_at: datetime | None = None
    runtime = str(started.payload_json["runtime"])
    workspace_ref = started.payload_json.get("workspace_ref")
    artifact_refs: tuple[str, ...] = ()
    profile_context: dict[str, Any] = {}
    for event in scoped:
        if event.event_type is WorkEventType.STAGE_STARTED:
            status = "running"
            completed_at = None
            runtime = str(event.payload_json["runtime"])
            workspace_ref = event.payload_json.get("workspace_ref")
        elif event.event_type is WorkEventType.CONTROL_DEGRADED:
            status = "blocked"
            completed_at = event.created_at
        elif event.event_type is WorkEventType.CONTROL_RESTORED:
            status = "running"
            completed_at = None
        elif event.event_type is WorkEventType.EXECUTION_RECORDED:
            status = str(event.payload_json["status"])
            completed_at = event.created_at
            artifact_refs = tuple(event.payload_json.get("artifact_refs", ()))
            profile_context = dict(event.payload_json.get("profile_context", {}))
        elif event.event_type is WorkEventType.STAGE_COMPLETED:
            profile_context = dict(event.payload_json.get("profile_context", {}))

    return ExecutionAttempt.model_validate(
        {
            "id": run_id,
            "project_id": started.project_id,
            "work_id": started.work_id,
            "stage": started.payload_json["stage"],
            "runtime": runtime,
            "workspace_ref": workspace_ref,
            "artifact_refs": artifact_refs,
            "status": status,
            "started_at": started.created_at,
            "completed_at": completed_at,
            "profile_context": profile_context,
        }
    )


def stage_run_ids(events: list[WorkEvent], work_id: str, stage: str) -> list[str]:
    """Run ids of every started attempt of ``stage`` for ``work_id``, in sequence order."""
    prefix = f"{work_id}:{stage}:"
    return [
        str(event.payload_json["run_id"])
        for event in sorted(events, key=lambda item: item.sequence)
        if event.event_type is WorkEventType.STAGE_STARTED
        and event.payload_json.get("stage") == stage
        and str(event.payload_json.get("run_id", "")).startswith(prefix)
    ]


def stage_runtime_failures(events: list[WorkEvent], work_id: str, stage: str) -> int:
    """Attempts of ``stage`` whose execution record says ``failed`` (runtime failures only)."""
    run_ids = set(stage_run_ids(events, work_id, stage))
    return sum(
        event.event_type is WorkEventType.EXECUTION_RECORDED
        and event.payload_json.get("run_id") in run_ids
        and event.payload_json.get("status") == "failed"
        for event in events
    )


def next_stage_run(events: list[WorkEvent], work_id: str, stage: str) -> tuple[str, int]:
    """The run to execute next for ``stage``.

    The latest started run is reused while it is neither completed nor recorded as
    ``failed`` or ``blocked``; otherwise the next attempt number starts a new run.
    """
    ordered = sorted(events, key=lambda item: item.sequence)
    run_ids = stage_run_ids(ordered, work_id, stage)
    if run_ids:
        latest = run_ids[-1]
        completed = any(
            event.event_type is WorkEventType.STAGE_COMPLETED
            and event.payload_json.get("run_id") == latest
            for event in ordered
        )
        status = next(
            (
                event.payload_json.get("status")
                for event in reversed(ordered)
                if event.event_type is WorkEventType.EXECUTION_RECORDED
                and event.payload_json.get("run_id") == latest
            ),
            None,
        )
        if not completed and status not in {"failed", "blocked"}:
            return latest, len(run_ids)
    attempt = len(run_ids) + 1
    return f"{work_id}:{stage}:{attempt}", attempt
