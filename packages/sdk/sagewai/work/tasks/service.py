# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Create Tasks from briefs and record the human decisions that unblock them."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.work.tasks import intake as intake_module
from sagewai.work.tasks.decide import fold_cycle
from sagewai.work.tasks.decisions import TASK_GATES
from sagewai.work.tasks.events import TaskEvent, TaskEventType, fold_record
from sagewai.work.tasks.models import (
    Authority,
    ExecutionRoute,
    GateMode,
    RoutingPolicy,
    Schedule,
    Task,
    TaskKind,
    TaskOrigin,
    TaskRecord,
    TaskStatus,
    TaskTarget,
)
from sagewai.work.tasks.plan import plan_from_events
from sagewai.work.tasks.store import StaleTaskError, TaskStore
from sagewai.work.tasks.templates import default_registry, get_template, validate_slots
from sagewai.work.tasks.writer import Entry, TaskWriter, build_events, status_entry

_MAX_TITLE = 200
_MAX_SUMMARY = 2000
_NON_HUMAN_FLOOR = Authority(
    plan=GateMode.REQUIRE, merge=GateMode.REQUIRE, deliver=GateMode.REQUIRE
)


class TaskCreationError(ValueError):
    """The brief cannot become a Task under the project's defaults."""


class TaskNotFoundError(KeyError):
    """No Task with that id in the project."""


class ActionNotFoundError(TaskNotFoundError):
    """No recorded action with that id exists on the Task."""


class TaskDecisionError(ValueError):
    """The decision does not match an open gate or an existing plan version."""


def _title(brief: str) -> str:
    for line in brief.splitlines():
        stripped = line.strip().lstrip("# ").strip()
        if stripped:
            return stripped[:_MAX_TITLE]
    raise TaskCreationError("brief is empty")


def _open_questions(
    events: Sequence[TaskEvent],
) -> list[tuple[dict[str, Any], datetime | None]]:
    """Requested questions with no answer or default yet, each with its deadline."""
    pending: dict[str, tuple[dict[str, Any], datetime | None]] = {}
    for event in sorted(events, key=lambda item: item.sequence):
        payload = event.payload_json
        if event.event_type is TaskEventType.CLARIFICATION_REQUESTED:
            raw = payload.get("deadline_at")
            deadline = datetime.fromisoformat(raw) if raw else None
            for question in payload["questions"]:
                pending[str(question["id"])] = (question, deadline)
        elif event.event_type in {
            TaskEventType.CLARIFICATION_ANSWERED,
            TaskEventType.CLARIFICATION_DEFAULTED,
        }:
            pending.pop(str(payload["question_id"]), None)
    return list(pending.values())


def _default_clarification_entry(question: dict[str, Any]) -> Entry:
    return (
        TaskEventType.CLARIFICATION_DEFAULTED,
        {"question_id": str(question["id"]), "answer": question.get("default")},
    )


class TaskService:
    """The single writer of Task creation and of human answers and plan acceptance."""

    def __init__(self, *, store: TaskStore, artifact_store: LocalArtifactStore | None = None) -> None:
        self._store = store
        self._artifacts = artifact_store or LocalArtifactStore()

    async def _load(self, task_id: str, *, project_id: str) -> tuple[Task, TaskRecord]:
        loaded = await self._store.load(task_id, project_id=project_id)
        if loaded is None:
            raise TaskNotFoundError(task_id)
        return loaded

    async def create(
        self,
        brief: str,
        *,
        project_id: str,
        origin: TaskOrigin,
        created_by: str,
        target: TaskTarget | None = None,
        execution: ExecutionRoute | None = None,
        authority_floor: Authority | None = None,
        origin_ref: str | None = None,
        source_ref: str | None = None,
        now: datetime | None = None,
    ) -> tuple[Task, TaskRecord]:
        """Route the brief, assemble the Task, and write its first events atomically."""
        moment = now or datetime.now(timezone.utc)
        defaults = await self._store.get_defaults(project_id=project_id)
        routed = intake_module.route(brief, defaults)
        template = get_template(routed.template_id)
        chosen = target if target is not None else defaults.target
        if chosen is None or chosen.kind != template.profile:
            raise TaskCreationError(
                f"project {project_id} has no {template.profile} target for template {template.id}"
            )
        authority = Authority.for_kind(template.kind).tighten(template.authority_floor)
        if authority_floor is not None:
            authority = authority.tighten(authority_floor)
        if origin is not TaskOrigin.HUMAN:
            authority = authority.tighten(_NON_HUMAN_FLOOR)
        task = Task(
            id=str(uuid.uuid4()),
            project_id=project_id,
            kind=template.kind,
            origin=origin,
            origin_ref=origin_ref,
            title=_title(brief),
            brief_ref=self._artifacts.put_bytes(
                brief.encode("utf-8"),
                project_id=project_id,
                media_type="text/markdown",
                created_by=created_by,
            ),
            brief_summary=brief[:_MAX_SUMMARY],
            source_ref=source_ref,
            template_id=template.id,
            template_version=template.version,
            slots=({} if routed.questions else validate_slots(template, routed.slots, default_registry)),
            profile=template.profile,
            target=chosen,
            schedule=(
                Schedule(cron=routed.cron, timezone=routed.timezone)
                if template.kind is TaskKind.SCHEDULED
                else None
            ),
            authority=authority,
            routing=RoutingPolicy(
                roles=dict(template.roles),
                prefer_free_implementation=(
                    defaults.routing.prefer_free_implementation and origin is TaskOrigin.HUMAN
                ),
            ),
            execution=execution if execution is not None else defaults.execution,
            created_by=created_by,
            created_at=moment,
        )
        base = TaskRecord(
            task_id=task.id,
            project_id=project_id,
            kind=task.kind,
            origin=task.origin,
            title=task.title,
            profile=task.profile,
            status=TaskStatus.PLANNING,
            last_event_sequence=0,
            created_at=moment,
            updated_at=moment,
        )
        entries: list[Entry] = [
            (
                TaskEventType.TASK_CREATED,
                {
                    "title": task.title,
                    "kind": task.kind.value,
                    "origin": task.origin.value,
                    "profile": task.profile,
                    "template_id": task.template_id,
                    "template_version": task.template_version,
                },
            ),
            (
                TaskEventType.BRIEF_RECORDED,
                {"brief_ref": task.brief_ref.storage_ref, "summary": task.brief_summary},
            ),
            (
                TaskEventType.INTAKE_RECORDED,
                {
                    "template_id": routed.template_id,
                    "template_version": routed.template_version,
                    "band": routed.band,
                    "confidence": routed.confidence,
                    "candidates": list(routed.candidates),
                    "slots": dict(task.slots),
                    "cron": routed.cron,
                    "timezone": routed.timezone,
                    "preview": routed.preview,
                },
            ),
        ]
        if routed.questions:
            deadline = moment + timedelta(seconds=defaults.clarification_deadline_seconds)
            entries.append(
                (
                    TaskEventType.CLARIFICATION_REQUESTED,
                    {
                        "questions": [question.model_dump(mode="json") for question in routed.questions],
                        "deadline_at": deadline.isoformat(),
                    },
                )
            )
            entries.append(status_entry(base, TaskStatus.CLARIFYING))
        events = build_events(
            base,
            entries,
            actor_type="human" if origin is TaskOrigin.HUMAN else "system",
            actor_ref=created_by,
            now=moment,
        )
        record = await self._store.create(task, events=events, record=fold_record(base, events))
        return task, record

    async def answer_clarification(
        self,
        task_id: str,
        *,
        project_id: str,
        question_id: str,
        attention_version: int,
        answer: str | None,
        actor_ref: str,
        now: datetime | None = None,
    ) -> TaskRecord:
        """Record one answer; the last one returns the Task to planning.

        ``answer=None`` applies the question's declared default through
        ``_default_clarification_entry``.
        ``attention_version`` binds the answer to the question as it was presented: a question
        re-asked at a higher version rejects an answer composed against the old text.
        """
        _task, record = await self._load(task_id, project_id=project_id)
        open_questions = _open_questions(await self._store.read_events(task_id, project_id=project_id))
        questions = {str(question["id"]): question for question, _deadline in open_questions}
        try:
            question = questions.pop(question_id)
        except KeyError as exc:
            raise TaskDecisionError(f"no open clarification question {question_id}") from exc
        if int(question["attention_version"]) != attention_version:
            raise TaskDecisionError(
                f"question {question_id} is at attention version {question['attention_version']}"
            )
        if answer is None:
            if not bool(question["defaultable"]):
                raise TaskDecisionError(f"question {question_id} is not defaultable")
            if question.get("default") is None:
                raise TaskDecisionError(f"question {question_id} has no default")
            entries: list[Entry] = [_default_clarification_entry(question)]
        else:
            material = not bool(question["defaultable"])
            entries = [
                (
                    TaskEventType.CLARIFICATION_ANSWERED,
                    {"question_id": question_id, "answer": answer, "material": material},
                )
            ]
        if record.status is TaskStatus.CLARIFYING and not questions:
            entries.append(status_entry(record, TaskStatus.PLANNING))
        writer = TaskWriter(self._store, actor_type="human", actor_ref=actor_ref)
        return await writer.append(record, entries, now=now)

    async def add_message(
        self,
        task_id: str,
        *,
        project_id: str,
        text: str,
        actor_ref: str,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> TaskRecord:
        """Append one human message to the thread; it changes no status and opens no gate.

        With an ``idempotency_key`` the message is written at most once: the key becomes a
        ``task_commands`` receipt, so a retried POST returns the current projection instead
        of a second copy of the same sentence. Without one a retry duplicates, which is why
        the route forwards the ``Idempotency-Key`` header.
        """
        _task, record = await self._load(task_id, project_id=project_id)
        command_id = None if idempotency_key is None else f"message:{idempotency_key}"
        if command_id is not None and not await self._store.record_command(
            task_id=task_id,
            project_id=project_id,
            command_id=command_id,
            payload={"text": text},
        ):
            return record
        writer = TaskWriter(self._store, actor_type="human", actor_ref=actor_ref)
        try:
            return await writer.append(
                record,
                [(TaskEventType.TASK_MESSAGE, {"author": "human", "text": text, "refs": []})],
                now=now,
            )
        except Exception:
            if command_id is not None:
                await self._store.delete_command(
                    task_id=task_id, project_id=project_id, command_id=command_id
                )
            raise

    async def accept_plan(
        self,
        task_id: str,
        *,
        project_id: str,
        version: int,
        actor_ref: str,
        now: datetime | None = None,
    ) -> TaskRecord:
        """Decide the plan gate and accept the proposed plan at ``version``."""
        _task, record = await self._load(task_id, project_id=project_id)
        events = await self._store.read_events(task_id, project_id=project_id)
        if plan_from_events(events, version=version) is None:
            raise TaskDecisionError(f"no plan proposed at version {version}")
        if record.plan_version == version:
            return record
        if record.pending_gate is not None and not record.pending_gate.startswith("plan:"):
            raise TaskDecisionError(
                f"gate {record.pending_gate} is not the plan gate; decide it where it was raised "
                "(Work gates: sagewai work approve)"
            )
        entries: list[Entry] = []
        if record.pending_gate is not None:
            entries.append(
                (TaskEventType.GATE_DECIDED, {"gate_id": record.pending_gate, "decision": "allow"})
            )
        entries.append((TaskEventType.PLAN_ACCEPTED, {"version": version}))
        if record.status is TaskStatus.PLAN_PROPOSED:
            entries.append(status_entry(record, TaskStatus.EXECUTING))
        writer = TaskWriter(self._store, actor_type="human", actor_ref=actor_ref)
        return await writer.append(record, entries, now=now)

    async def decide_gate(
        self,
        task_id: str,
        *,
        project_id: str,
        gate_id: str,
        decision: Literal["allow", "deny"],
        actor_ref: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> TaskRecord:
        """Decide one gate the Task itself opened; a refusal blocks the Task for a human."""
        if not gate_id.startswith(TASK_GATES):
            raise TaskDecisionError(
                f"gate {gate_id} belongs to a Work; decide it at "
                "POST /api/v1/work/{work_id}/gates/{gate_id} (or sagewai work approve)"
            )
        _task, record = await self._load(task_id, project_id=project_id)
        if record.pending_gate != gate_id:
            raise TaskDecisionError(f"no open gate {gate_id}")
        entries: list[Entry] = [
            (TaskEventType.GATE_DECIDED, {"gate_id": gate_id, "decision": decision})
        ]
        if decision == "allow" and gate_id.startswith("replan:"):
            entries.append(status_entry(record, TaskStatus.PLANNING))
        elif (
            decision == "allow"
            and gate_id.startswith("rollback:")
            and record.status is not TaskStatus.EXECUTING
        ):
            entries.append(status_entry(record, TaskStatus.EXECUTING))
        elif decision != "allow":
            entries.append(
                (
                    TaskEventType.TASK_MESSAGE,
                    {
                        "author": "human",
                        "text": note or f"gate {gate_id} decided {decision}",
                        "refs": [],
                    },
                )
            )
            entries.append(status_entry(record, TaskStatus.BLOCKED))
        writer = TaskWriter(self._store, actor_type="human", actor_ref=actor_ref)
        return await writer.append(record, entries, now=now)

    async def request_rollback(
        self,
        task_id: str,
        *,
        project_id: str,
        action_id: str,
        actor_ref: str,
        now: datetime | None = None,
    ) -> TaskRecord:
        """Open and allow the rollback gate for one recorded action.

        Section 19 keeps rollback execution in the coordinator, so this writes the durable
        request the coordinator reads — ``GATE_REQUESTED`` carrying the recorded
        ``ActionRequest`` plus an allowed ``GATE_DECIDED`` — and ``decide`` turns it into
        ``RollbackWork`` on the next tick.
        """
        _task, record = await self._load(task_id, project_id=project_id)
        events = await self._store.read_events(task_id, project_id=project_id)
        intent = next(
            (
                event
                for event in reversed(events)
                if event.event_type is TaskEventType.ACTION_INTENT_RECORDED
                and event.payload_json["action_id"] == action_id
            ),
            None,
        )
        if intent is None:
            raise ActionNotFoundError(f"no recorded action {action_id}")
        action = dict(intent.payload_json["action"])
        if action["rollback"] is None:
            raise TaskDecisionError(f"action {action_id} declares no rollback recipe")
        work_id = str(intent.payload_json["work_id"])
        gate_id = f"rollback:{work_id}"
        if any(
            event.event_type
            in {TaskEventType.GATE_REQUESTED, TaskEventType.GATE_DECIDED}
            and event.payload_json["gate_id"] == gate_id
            for event in events
        ):
            return record
        state = fold_cycle(events, plan_version=record.plan_version)
        if work_id not in state.step_works.values():
            raise TaskDecisionError(f"work {work_id} is not in the current cycle")
        if work_id in state.rolled_back:
            raise TaskDecisionError(f"work {work_id} was already rolled back")
        if record.pending_gate is not None:
            raise TaskDecisionError(f"gate {record.pending_gate} is still open")
        external_ref = next(
            (
                event.payload_json["external_ref"]
                for event in reversed(events)
                if event.event_type is TaskEventType.ACTION_RESULT_RECORDED
                and event.payload_json["action_id"] == action_id
            ),
            None,
        )
        if external_ref is not None:
            action["scope"] = str(external_ref)
        entries: list[Entry] = [
            (
                TaskEventType.GATE_REQUESTED,
                {
                    "gate_id": gate_id,
                    "question": f"Allow the recorded rollback ({action['rollback']}) of {action['scope']}?",
                    "action": action,
                    "work_id": work_id,
                },
            ),
            (TaskEventType.GATE_DECIDED, {"gate_id": gate_id, "decision": "allow"}),
        ]
        if record.status not in {TaskStatus.EXECUTING, TaskStatus.ASSESSING}:
            entries.append(status_entry(record, TaskStatus.EXECUTING))
        writer = TaskWriter(self._store, actor_type="human", actor_ref=actor_ref)
        return await writer.append(record, entries, now=now)

    async def default_expired_clarifications(
        self, task_id: str, *, project_id: str, now: datetime | None = None
    ) -> TaskRecord:
        """Default every defaultable question past its deadline (section 8.2).

        A non-defaultable question is never defaulted: it stays open, keeps
        ``pending_material_questions`` above zero, and so keeps the attention on the user.
        """
        moment = now or datetime.now(timezone.utc)
        _task, record = await self._load(task_id, project_id=project_id)
        if record.status is not TaskStatus.CLARIFYING:
            return record
        events = await self._store.read_events(task_id, project_id=project_id)
        open_questions = _open_questions(events)
        expired = [
            question
            for question, deadline in open_questions
            if bool(question["defaultable"]) and deadline is not None and deadline <= moment
        ]
        if not expired:
            return record
        entries: list[Entry] = [_default_clarification_entry(question) for question in expired]
        if len(expired) == len(open_questions):
            entries.append(status_entry(record, TaskStatus.PLANNING))
        writer = TaskWriter(self._store)
        return await writer.append(record, entries, now=moment)


class ClarificationDeadlines:
    """Sweep one project's clarifying Tasks; the coordinator tick runs it (section 8.2)."""

    def __init__(self, *, store: TaskStore, service: TaskService) -> None:
        self._store = store
        self._service = service

    async def run(self, *, project_id: str, now: datetime) -> int:
        """Default what is past its deadline; returns how many questions were defaulted."""
        defaulted = 0
        for record in await self._store.list_records(
            project_id=project_id, statuses=(TaskStatus.CLARIFYING,)
        ):
            try:
                after = await self._service.default_expired_clarifications(
                    record.task_id, project_id=project_id, now=now
                )
            except StaleTaskError:
                continue
            defaulted += record.pending_questions - after.pending_questions
        return defaulted


__all__ = ["ClarificationDeadlines", "TaskCreationError", "TaskDecisionError", "TaskService"]
