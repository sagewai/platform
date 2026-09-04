# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task API routes for feed replay and live events."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sse_starlette.sse import EventSourceResponse

from sagewai.admin.serve import _emit_audit, _require_project_admin, _work_project_scope
from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.store import WorkStore
from sagewai.work.tasks.events import TaskEventType
from sagewai.work.tasks.feed import FeedEntry
from sagewai.work.tasks.intake import route as intake_route
from sagewai.work.tasks.models import (
    Authority,
    BoardColumn,
    Budget,
    ExecutionRoute,
    Sensitivity,
    TaskDefaults,
    TaskKind,
    TaskOrigin,
    TaskRecord,
    TaskStatus,
    TaskTarget,
    TaskTriggerSpec,
)
from sagewai.work.tasks.plan import plan_from_events
from sagewai.work.tasks.service import (
    TaskCreationError,
    TaskDecisionError,
    TaskNotFoundError,
    TaskService,
)
from sagewai.work.tasks.store import StaleTaskError, TaskStore
from sagewai.work.tasks.telemetry import derive_task_telemetry
from sagewai.work.tasks.templates import CATALOGUE, RESERVED_TEMPLATE_IDS
from sagewai.work.tasks.transitions import IllegalTransitionError
from sagewai.work.tasks.views import actions_from_events, referenced_artifacts, thread_from_events

router = APIRouter(prefix="/api/v1/tasks")
work_router = APIRouter(prefix="/api/v1/work")
artifacts_router = APIRouter(prefix="/api/v1/artifacts")
_EXTERNAL_EFFECT_GATES = ("deliver:", "rollback:")
_ARTIFACT_MEDIA_TYPE = "application/octet-stream"
_CURSOR_SEPARATOR = "|"
_CURSOR_ORDER_SEPARATOR = ":"
_CURSOR_PREFIX_BY_ORDER = {"created_at": "c", "updated_at": "u"}
_CURSOR_ORDER_BY_PREFIX = {"c": "created_at", "u": "updated_at"}


def _task_project_scope(request: Request) -> str:
    """The Task read and write scope: one explicit project, never the global scope.

    ``_work_project_scope`` maps ``X-Project-ID: global`` to ``None``, which is a real
    organization-global scope for Work. A Task cannot live there — ``Task.project_id`` is
    ``Field(min_length=1)`` and section 19 says there is no global Task scope — so the header
    is refused here instead of resolving to an always-empty scope that 404s.
    """
    project_id = _work_project_scope(request)
    if project_id is None:
        raise HTTPException(
            status_code=400,
            detail="Tasks require an explicit project; there is no global Task scope",
        )
    return project_id


def _actor_ref(request: Request) -> str:
    """Who a mutation is attributed to: middleware actor, or admin without middleware."""
    context = getattr(request.state, "context", None)
    return "admin" if context is None else context.actor.label


@contextmanager
def _service_errors() -> Iterator[None]:
    """Map an unknown Task to 404 and the Task layer's write refusals to 409."""
    try:
        yield
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    except (TaskDecisionError, IllegalTransitionError, StaleTaskError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _strip_brief(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("brief must be non-empty text")
    return value.strip()


def _encode_cursor(record: TaskRecord, order_by: Literal["created_at", "updated_at"]) -> str:
    moment = record.created_at if order_by == "created_at" else record.updated_at
    return (
        f"{_CURSOR_PREFIX_BY_ORDER[order_by]}{_CURSOR_ORDER_SEPARATOR}"
        f"{moment.isoformat()}{_CURSOR_SEPARATOR}{record.task_id}"
    )


def _decode_cursor(
    cursor: str, order_by: Literal["created_at", "updated_at"]
) -> tuple[datetime, str]:
    raw_ordered, separator, task_id = cursor.partition(_CURSOR_SEPARATOR)
    if separator == "" or not task_id:
        raise HTTPException(status_code=400, detail="cursor is not a list cursor")
    prefix, order_separator, raw_moment = raw_ordered.partition(_CURSOR_ORDER_SEPARATOR)
    if order_separator == "" or prefix not in _CURSOR_ORDER_BY_PREFIX:
        raise HTTPException(status_code=400, detail="cursor is not a list cursor")
    if _CURSOR_ORDER_BY_PREFIX[prefix] != order_by:
        raise HTTPException(status_code=400, detail="cursor does not match order_by")
    try:
        return datetime.fromisoformat(raw_moment), task_id
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="cursor is not a list cursor") from exc


@router.get("")
async def list_tasks(
    request: Request,
    status: Annotated[list[TaskStatus] | None, Query()] = None,
    kind: Annotated[list[TaskKind] | None, Query()] = None,
    origin: Annotated[list[TaskOrigin] | None, Query()] = None,
    column: Annotated[list[BoardColumn] | None, Query()] = None,
    order_by: Literal["created_at", "updated_at"] = "created_at",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    records = await store.list_records(
        project_id=project_id,
        statuses=status,
        kinds=kind,
        origins=origin,
        board_columns=column,
        order_by=order_by,
        after=None if cursor is None else _decode_cursor(cursor, order_by),
        limit=limit,
    )
    return {
        "tasks": [record.model_dump(mode="json") for record in records],
        "next_cursor": _encode_cursor(records[-1], order_by) if len(records) == limit else None,
    }


@router.get("/board")
async def task_board(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict:
    """The five section 5.2 columns, newest-touched first, every one present even when empty."""
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    records = await store.list_records(
        project_id=project_id, order_by="updated_at", descending=True, limit=limit
    )
    columns: dict[str, list[dict]] = {column.value: [] for column in BoardColumn}
    for record in records:
        columns[record.board_column.value].append(record.model_dump(mode="json"))
    return {"columns": columns}


class _CreateTaskBody(BaseModel):
    """What a console or CLI create carries; ``brief_ref`` and ``issue_url`` are deferred."""

    model_config = ConfigDict(extra="forbid")

    brief: str = Field(min_length=1, max_length=64_000)
    target: TaskTarget | None = None
    execution: ExecutionRoute | None = None
    authority_floor: Authority | None = None
    origin_ref: str | None = None
    source_ref: str | None = None

    @field_validator("brief", mode="before")
    @classmethod
    def _validate_brief(cls, value: object) -> str:
        return _strip_brief(value)


class _BriefBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brief: str = Field(min_length=1, max_length=64_000)

    @field_validator("brief", mode="before")
    @classmethod
    def _validate_brief(cls, value: object) -> str:
        return _strip_brief(value)


class _MessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=8000)


class _AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attention_id: str = Field(min_length=1)
    attention_version: int = Field(ge=1)
    answer: str | None = Field(default=None, min_length=1, max_length=8000)
    use_default: bool = False

    @model_validator(mode="after")
    def _validate_shape(self) -> _AnswerBody:
        if self.use_default:
            if self.answer is not None:
                raise ValueError("defaulted answers do not carry answer")
        elif self.answer is None:
            raise ValueError("answer is required")
        return self


class _GateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow", "deny"]
    note: str | None = Field(default=None, max_length=2000)


class _WorkGateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow", "deny"]


class _CancelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=2000)


class _PatchTaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget: Budget
    revision: int = Field(ge=0)


@router.post("", status_code=201)
async def create_task(request: Request, body: _CreateTaskBody) -> dict:
    project_id = _task_project_scope(request)
    service: TaskService = request.app.state.task_service
    try:
        task, record = await service.create(
            body.brief,
            project_id=project_id,
            origin=TaskOrigin.HUMAN,
            created_by=_actor_ref(request),
            target=body.target,
            execution=body.execution,
            authority_floor=body.authority_floor,
            origin_ref=body.origin_ref,
            source_ref=body.source_ref,
        )
    except TaskCreationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _emit_audit(request, "task.create", target_type="task", target_id=task.id)
    return {"task": task.model_dump(mode="json"), "record": record.model_dump(mode="json")}


@router.post("/intake")
async def preview_intake(request: Request, body: _BriefBody) -> dict:
    """Deterministic preview: the template, band, cron and questions creation would use."""
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    defaults = await store.get_defaults(project_id=project_id)
    return intake_route(body.brief, defaults).model_dump(mode="json")


@router.get("/templates")
async def list_templates(request: Request) -> dict:
    _task_project_scope(request)
    return {
        "templates": [template.model_dump(mode="json") for template in CATALOGUE.values()],
        "reserved": list(RESERVED_TEMPLATE_IDS),
    }


PROJECT_ADMIN_ROUTES: tuple[tuple[str, str], ...] = (
    ("PUT", "/api/v1/tasks/defaults"),
    ("POST", "/api/v1/tasks/triggers"),
    ("DELETE", "/api/v1/tasks/triggers/{trigger_id}"),
    ("POST", "/api/v1/tasks/{task_id}/actions/{action_id}/rollback"),
    ("PATCH", "/api/v1/tasks/{task_id}"),
    ("POST", "/api/v1/work/{work_id}/gates/{gate_id}"),
)
"""Every route the project-admin tier gates as a whole, enumerated by the coverage gate.

The Task gate route is absent on purpose: it is gated per gate class, not per route (a
``plan:`` gate is a member decision), and its tier is asserted in
``tests/admin/test_task_routes_tenancy.py``.
"""


class _DefaultsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    defaults: TaskDefaults
    expected_revision: int = Field(ge=0)

    @field_validator("defaults", mode="before")
    @classmethod
    def _reject_defaults_revision(cls, value: object) -> object:
        if isinstance(value, dict) and "revision" in value:
            raise ValueError("defaults.revision is not accepted; send expected_revision")
        return value


class _TriggerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger: TaskTriggerSpec


@router.get("/defaults")
async def get_task_defaults(request: Request) -> dict:
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    return (await store.get_defaults(project_id=project_id)).model_dump(mode="json")


@router.put("/defaults", dependencies=[Depends(_require_project_admin)])
async def put_task_defaults(request: Request, body: _DefaultsBody) -> dict:
    project_id = _task_project_scope(request)
    if body.defaults.project_id != project_id:
        raise HTTPException(status_code=400, detail="defaults belong to another project")
    store: TaskStore = request.app.state.task_store
    with _service_errors():
        stored = await store.put_defaults(body.defaults, expected_revision=body.expected_revision)
    await _emit_audit(
        request, "task.defaults.put", target_type="task_defaults", target_id=project_id
    )
    return stored.model_dump(mode="json")


@router.get("/triggers")
async def list_task_triggers(request: Request) -> dict:
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    triggers = await store.list_triggers(project_id=project_id, enabled_only=False)
    return {"triggers": [trigger.model_dump(mode="json") for trigger in triggers]}


@router.post("/triggers", status_code=201, dependencies=[Depends(_require_project_admin)])
async def put_task_trigger(request: Request, body: _TriggerBody) -> dict:
    project_id = _task_project_scope(request)
    if body.trigger.project_id != project_id:
        raise HTTPException(status_code=400, detail="trigger belongs to another project")
    store: TaskStore = request.app.state.task_store
    await store.put_trigger(body.trigger)
    await _emit_audit(
        request, "task.trigger.put", target_type="task_trigger", target_id=body.trigger.trigger_id
    )
    return body.trigger.model_dump(mode="json")


@router.delete("/triggers/{trigger_id}", dependencies=[Depends(_require_project_admin)])
async def delete_task_trigger(trigger_id: str, request: Request) -> dict:
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    if not await store.delete_trigger(trigger_id, project_id=project_id):
        raise HTTPException(status_code=404, detail="Not found")
    await _emit_audit(
        request, "task.trigger.delete", target_type="task_trigger", target_id=trigger_id
    )
    return {"status": "ok"}


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request) -> dict:
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    loaded = await store.load(task_id, project_id=project_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Not found")
    task, record = loaded
    plan = None
    if record.plan_version != 0:
        events = await store.read_events(task_id, project_id=project_id)
        plan = plan_from_events(events, version=record.plan_version)
    return {
        "task": task.model_dump(mode="json"),
        "record": record.model_dump(mode="json"),
        "plan": None if plan is None else plan.model_dump(mode="json"),
    }


@router.get("/{task_id}/thread")
async def get_task_thread(task_id: str, request: Request) -> dict:
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    if await store.load_record(task_id, project_id=project_id) is None:
        raise HTTPException(status_code=404, detail="Not found")
    events = await store.read_events(task_id, project_id=project_id)
    return thread_from_events(events).model_dump(mode="json")


@router.get("/{task_id}/actions")
async def list_task_actions(task_id: str, request: Request) -> dict:
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    if await store.load_record(task_id, project_id=project_id) is None:
        raise HTTPException(status_code=404, detail="Not found")
    events = await store.read_events(task_id, project_id=project_id)
    return {"actions": [action.model_dump(mode="json") for action in actions_from_events(events)]}


@router.post(
    "/{task_id}/actions/{action_id}/rollback",
    dependencies=[Depends(_require_project_admin)],
)
async def rollback_task_action(task_id: str, action_id: str, request: Request) -> dict:
    """Request the recorded rollback; the coordinator executes it (section 19)."""
    project_id = _task_project_scope(request)
    service: TaskService = request.app.state.task_service
    with _service_errors():
        record = await service.request_rollback(
            task_id, project_id=project_id, action_id=action_id, actor_ref=_actor_ref(request)
        )
    await _emit_audit(request, "task.action.rollback", target_type="task", target_id=task_id)
    return record.model_dump(mode="json")


@router.post("/{task_id}/messages", status_code=201)
async def post_task_message(task_id: str, request: Request, body: _MessageBody) -> dict:
    """``Idempotency-Key`` makes a retried POST safe; without it a retry duplicates."""
    project_id = _task_project_scope(request)
    service: TaskService = request.app.state.task_service
    with _service_errors():
        record = await service.add_message(
            task_id,
            project_id=project_id,
            text=body.text,
            actor_ref=_actor_ref(request),
            idempotency_key=request.headers.get("idempotency-key"),
        )
    await _emit_audit(request, "task.message", target_type="task", target_id=task_id)
    return record.model_dump(mode="json")


@router.post("/{task_id}/answers")
async def post_task_answer(task_id: str, request: Request, body: _AnswerBody) -> dict:
    project_id = _task_project_scope(request)
    service: TaskService = request.app.state.task_service
    with _service_errors():
        record = await service.answer_clarification(
            task_id,
            project_id=project_id,
            question_id=body.attention_id,
            attention_version=body.attention_version,
            answer=body.answer,
            actor_ref=_actor_ref(request),
        )
    await _emit_audit(request, "task.answer", target_type="task", target_id=task_id)
    return record.model_dump(mode="json")


@router.post("/{task_id}/gates/{gate_id}")
async def decide_task_gate(task_id: str, gate_id: str, request: Request, body: _GateBody) -> dict:
    """Decide one Task gate.

    ``deliver:`` and ``rollback:`` have external side effects, so section 17 puts them behind
    a project admin; ``plan:`` and ``replan:`` are member decisions.
    """
    project_id = _task_project_scope(request)
    if gate_id.startswith(_EXTERNAL_EFFECT_GATES):
        _require_project_admin(request)
    service: TaskService = request.app.state.task_service
    with _service_errors():
        record = await service.decide_gate(
            task_id,
            project_id=project_id,
            gate_id=gate_id,
            decision=body.decision,
            actor_ref=_actor_ref(request),
            note=body.note,
        )
    await _emit_audit(request, "task.gate.decide", target_type="task", target_id=task_id)
    return record.model_dump(mode="json")


@router.post("/{task_id}/pause")
async def pause_task(task_id: str, request: Request) -> dict:
    project_id = _task_project_scope(request)
    service: TaskService = request.app.state.task_service
    with _service_errors():
        record = await service.pause(task_id, project_id=project_id, actor_ref=_actor_ref(request))
    await _emit_audit(request, "task.pause", target_type="task", target_id=task_id)
    return record.model_dump(mode="json")


@router.post("/{task_id}/resume")
async def resume_task(task_id: str, request: Request) -> dict:
    project_id = _task_project_scope(request)
    service: TaskService = request.app.state.task_service
    with _service_errors():
        record = await service.resume(task_id, project_id=project_id, actor_ref=_actor_ref(request))
    await _emit_audit(request, "task.resume", target_type="task", target_id=task_id)
    return record.model_dump(mode="json")


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request, body: _CancelBody) -> dict:
    project_id = _task_project_scope(request)
    service: TaskService = request.app.state.task_service
    with _service_errors():
        record = await service.cancel(
            task_id, project_id=project_id, actor_ref=_actor_ref(request), note=body.note
        )
    await _emit_audit(request, "task.cancel", target_type="task", target_id=task_id)
    return record.model_dump(mode="json")


@router.patch("/{task_id}", dependencies=[Depends(_require_project_admin)])
async def patch_task(task_id: str, request: Request, body: _PatchTaskBody) -> dict:
    """Budget only (decision 7); raising a budget is spending authority, so it is admin-tier."""
    project_id = _task_project_scope(request)
    service: TaskService = request.app.state.task_service
    with _service_errors():
        task, record = await service.update_budget(
            task_id,
            project_id=project_id,
            budget=body.budget,
            expected_revision=body.revision,
            actor_ref=_actor_ref(request),
        )
    await _emit_audit(request, "task.budget.update", target_type="task", target_id=task_id)
    return {"task": task.model_dump(mode="json"), "record": record.model_dump(mode="json")}


@router.get("/{task_id}/events")
async def task_events(task_id: str, request: Request) -> EventSourceResponse:
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    if await store.load_record(task_id, project_id=project_id) is None:
        raise HTTPException(status_code=404, detail="Not found")
    last_event_id = request.headers.get("last-event-id")
    if last_event_id is None:
        after = 0
    else:
        try:
            after = int(last_event_id)
        except ValueError as exc:
            raise HTTPException(400, "Last-Event-ID must be a non-negative integer") from exc
        if after < 0 or after > 2**63 - 1:
            raise HTTPException(400, "Last-Event-ID must be a non-negative integer")
    heartbeat_seconds = float(os.environ.get("TASK_SSE_HEARTBEAT", "15"))
    queue = store.feed_bus.subscribe(project_id, task_id)
    return EventSourceResponse(
        _task_event_stream(
            store=store,
            project_id=project_id,
            task_id=task_id,
            queue=queue,
            after=after,
            heartbeat_seconds=heartbeat_seconds,
        )
    )


@router.get("/{task_id}/telemetry")
async def task_telemetry(task_id: str, request: Request) -> dict:
    project_id = _task_project_scope(request)
    task_store: TaskStore = request.app.state.task_store
    loaded = await task_store.load(task_id, project_id=project_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Not found")
    task, record = loaded
    task_events = await task_store.read_events(task_id, project_id=project_id)
    work_store: WorkStore = request.app.state.work_store
    work_records = await work_store.list_work(project_id=project_id, active_only=False)
    work_events: dict[str, list[WorkEvent]] = {}
    project_selections: list[WorkEvent] = []
    for work_record in work_records:
        events = await work_store.read_events(work_record.work_id, project_id=project_id)
        project_selections.extend(
            event for event in events if event.event_type is WorkEventType.RUNTIME_SELECTED
        )
        if work_record.profile_context.get("task_id") == task_id:
            work_events[work_record.work_id] = events
    cycles = {
        int(event.payload_json["cycle"])
        for event in task_events
        if event.event_type is TaskEventType.CYCLE_STARTED
    }
    spend = {
        cycle: await task_store.spend_totals(
            task_id=task_id,
            project_id=project_id,
            cycle=cycle,
        )
        for cycle in cycles
    }
    return derive_task_telemetry(
        record=record,
        task_events=task_events,
        work_events=work_events,
        spend=spend,
        budget=task.budget,
        project_selections=project_selections,
        now=datetime.now(timezone.utc),
    ).model_dump(mode="json")


@work_router.post("/{work_id}/gates/{gate_id}", dependencies=[Depends(_require_project_admin)])
async def decide_work_gate(
    work_id: str, gate_id: str, request: Request, body: _WorkGateBody
) -> dict:
    """Record the decision on a Work's own gate; the coordinator or ``sagewai work resume``
    advances it.

    This writes the durable half of ``GitHubIssueLifecycle.approve`` - the ``GATE_DECIDED``
    event and the projection move - and executes nothing: the merge stage re-reads the
    recorded decision and handles ``allow`` and ``deny`` itself (`github.py:1212-1240`).
    ``pending_gate`` must be cleared here, or the Task never mirrors the decision back
    (`coordinator.py:413-420`). Deciding twice is a no-op, exactly as ``approve`` treats an
    already-decided gate (`github.py:866-868`), so a retried POST cannot append a second
    decision.

    The scope helper is the **Task** one even though the path is under ``/api/v1/work``: a gate
    belongs to a Work that belongs to a project, and ``_work_project_scope`` would map
    ``X-Project-ID: global`` to ``None`` and append the decision under the null scope, where the
    Work it names does not live. The activity read keeps the Work helper; a decision needs a
    project, so this write uses the Task scope.
    """
    project_id = _task_project_scope(request)
    store: WorkStore = request.app.state.work_store
    record = await store.load_work(work_id, project_id=project_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Not found")
    if not gate_id.startswith("merge:"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"gate {gate_id} is not a merge gate: Task gates are decided at "
                "POST /api/v1/tasks/{task_id}/gates/{gate_id}, delivery gates with "
                "sagewai work approve"
            ),
        )
    events = await store.read_events(work_id, project_id=project_id)
    decided = next(
        (
            event
            for event in reversed(events)
            if event.event_type is WorkEventType.GATE_DECIDED
            and event.payload_json["gate_id"] == gate_id
        ),
        None,
    )
    if decided is not None:
        return {
            "work_id": work_id,
            "gate_id": gate_id,
            "decision": str(decided.payload_json["decision"]),
        }
    if record.pending_gate != gate_id:
        raise HTTPException(status_code=409, detail=f"gate is not pending: {gate_id}")
    requested = next(
        (
            event
            for event in reversed(events)
            if event.event_type is WorkEventType.GATE_REQUESTED
            and event.payload_json["gate_id"] == gate_id
        ),
        None,
    )
    if requested is None:
        raise HTTPException(status_code=409, detail=f"gate request is missing: {gate_id}")
    try:
        await store.append_next(
            work_id=work_id,
            project_id=project_id,
            event_type=WorkEventType.GATE_DECIDED,
            payload={
                "gate_id": gate_id,
                "decision": body.decision,
                "action": requested.payload_json["action"],
            },
            actor_type="human",
            actor_ref=_actor_ref(request),
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="concurrent append won the sequence") from exc
    await store.save_work(
        record.model_copy(
            update={
                "status": "MERGING" if body.decision == "allow" else record.status,
                "pending_gate": None,
                "updated_at": datetime.now(timezone.utc),
            }
        )
    )
    await _emit_audit(request, "work.gate.decide", target_type="work", target_id=work_id)
    return {"work_id": work_id, "gate_id": gate_id, "decision": body.decision}


@artifacts_router.get("/{storage_ref}")
async def read_artifact(storage_ref: str, request: Request, task_id: str) -> Response:
    """Serve one artifact the named Task references, unless the Task is ``restricted``.

    ``storage_ref`` is the digest form ``sha256:<64 hex>`` from ``ArtifactRef.digest``; the
    full ``artifact://`` reference carries a ``//`` no path parameter can hold.
    """
    project_id = _task_project_scope(request)
    store: TaskStore = request.app.state.task_store
    loaded = await store.load(task_id, project_id=project_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Not found")
    task, _record = loaded
    if task.sensitivity is Sensitivity.RESTRICTED:
        raise HTTPException(
            status_code=403, detail="restricted content never leaves the console sink"
        )
    reference = f"artifact://{storage_ref}"
    events = await store.read_events(task_id, project_id=project_id)
    if reference not in referenced_artifacts(events):
        raise HTTPException(status_code=404, detail="Not found")
    artifacts: LocalArtifactStore = request.app.state.artifact_store
    try:
        content = artifacts.read(reference, project_id=project_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    media_type = (
        task.brief_ref.media_type
        if task.brief_ref.storage_ref == reference
        else _ARTIFACT_MEDIA_TYPE
    )
    return Response(content=content, media_type=media_type)


async def _task_event_stream(
    *,
    store: TaskStore,
    project_id: str,
    task_id: str,
    queue: asyncio.Queue[FeedEntry],
    after: int,
    heartbeat_seconds: float,
) -> AsyncIterator[dict[str, str]]:
    seen = after
    try:
        while True:
            page = await store.read_feed(task_id, project_id=project_id, after=seen, limit=500)
            if not page:
                break
            for entry in page:
                seen = entry.feed_sequence
                yield _sse(entry)
        while True:
            try:
                entry = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield {"event": "heartbeat", "data": "{}"}
                continue
            if entry.feed_sequence <= seen:
                continue
            seen = entry.feed_sequence
            yield _sse(entry)
    finally:
        store.feed_bus.unsubscribe(project_id, task_id, queue)


def _sse(entry: FeedEntry) -> dict[str, str]:
    return {
        "id": str(entry.feed_sequence),
        "event": entry.event_type,
        "data": entry.model_dump_json(),
    }


__all__ = ["router"]
