# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pure read-side projections of one Task's event stream, for the console and the CLI."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from sagewai.work.tasks.decisions import TASK_GATES
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import TERMINAL_STATUSES, TaskStatus

ThreadKind = Literal["brief", "message", "question", "gate", "plan", "output", "status"]


class ThreadEntry(BaseModel):
    """One rendered line of the Task thread, in stream order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    sequence: int
    at: datetime
    author: str
    actor_ref: str | None
    kind: ThreadKind
    text: str
    attention_id: str | None = None
    attention_version: int | None = None
    answer: str | None = None
    answered_by: Literal["human", "default"] | None = None
    defaultable: bool | None = None
    deadline_at: datetime | None = None
    gate_id: str | None = None
    decision: Literal["allow", "deny"] | None = None
    decided_by: Literal["task", "work"] | None = None
    work_id: str | None = None
    plan_version: int | None = None
    refs: tuple[str, ...] = ()
    closed: bool = False


class ThreadView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    project_id: str
    brief_ref: str | None
    entries: tuple[ThreadEntry, ...]
    open_question_ids: tuple[str, ...]
    pending_gate: str | None


def thread_from_events(events: Sequence[TaskEvent]) -> ThreadView:
    """Fold one Task stream into the thread the Thread tab renders.

    Questions and gates are folded in place: a later answer or decision fills the entry the
    request created, so the console renders one control per question and one per gate instead
    of a request line followed by an unrelated answer line.
    """
    ordered = sorted(events, key=lambda event: event.sequence)
    entries: list[dict] = []
    questions: dict[str, dict] = {}
    gates: dict[str, list[dict]] = {}
    brief_ref: str | None = None
    pending_gate: str | None = None
    for event in ordered:
        payload = event.payload_json
        base = {
            "id": str(event.sequence),
            "sequence": event.sequence,
            "at": event.created_at,
            "author": event.actor_type,
            "actor_ref": event.actor_ref,
        }
        if event.event_type is TaskEventType.BRIEF_RECORDED:
            brief_ref = str(payload["brief_ref"])
            entries.append(
                {
                    **base,
                    "kind": "brief",
                    "text": str(payload["summary"]),
                    "refs": (brief_ref,),
                }
            )
        elif event.event_type is TaskEventType.CLARIFICATION_REQUESTED:
            deadline = payload.get("deadline_at")
            for question in payload["questions"]:
                question_id = str(question["id"])
                entry = {
                    **base,
                    "id": f"{event.sequence}:{question_id}",
                    "kind": "question",
                    "text": str(question["text"]),
                    "attention_id": question_id,
                    "attention_version": int(question["attention_version"]),
                    "defaultable": bool(question["defaultable"]),
                    "deadline_at": None
                    if deadline is None
                    else datetime.fromisoformat(str(deadline)),
                }
                questions[question_id] = entry
                entries.append(entry)
        elif event.event_type is TaskEventType.CLARIFICATION_ANSWERED:
            answered = questions[str(payload["question_id"])]
            answered["answer"] = str(payload["answer"])
            answered["answered_by"] = "human"
        elif event.event_type is TaskEventType.CLARIFICATION_DEFAULTED:
            defaulted = questions[str(payload["question_id"])]
            defaulted["answer"] = None if payload["answer"] is None else str(payload["answer"])
            defaulted["answered_by"] = "default"
        elif event.event_type is TaskEventType.TASK_MESSAGE:
            entries.append(
                {
                    **base,
                    "kind": "message",
                    "author": str(payload["author"]),
                    "text": str(payload["text"]),
                    "refs": tuple(str(ref) for ref in payload["refs"]),
                }
            )
        elif event.event_type is TaskEventType.PLAN_PROPOSED:
            version = int(payload["version"])
            entries.append(
                {
                    **base,
                    "kind": "plan",
                    "text": f"plan proposed at version {version}",
                    "plan_version": version,
                }
            )
        elif event.event_type is TaskEventType.PLAN_ACCEPTED:
            version = int(payload["version"])
            entries.append(
                {
                    **base,
                    "kind": "plan",
                    "text": f"plan accepted at version {version}",
                    "plan_version": version,
                }
            )
        elif event.event_type is TaskEventType.GATE_REQUESTED:
            gate_id = str(payload["gate_id"])
            decided_by = (
                str(payload["decided_by"])
                if "decided_by" in payload
                else ("task" if gate_id.startswith(TASK_GATES) else "work")
            )
            entry = {
                **base,
                "kind": "gate",
                "text": str(payload.get("question") or gate_id),
                "gate_id": gate_id,
                "decided_by": decided_by,
                "work_id": str(payload["work_id"]) if decided_by == "work" else None,
            }
            gates.setdefault(gate_id, []).append(entry)
            pending_gate = gate_id
            entries.append(entry)
        elif event.event_type is TaskEventType.GATE_DECIDED:
            gate_id = str(payload["gate_id"])
            gate = next(
                (
                    entry
                    for entry in reversed(gates.get(gate_id, ()))
                    if "decision" not in entry and not entry.get("closed", False)
                ),
                None,
            )
            if gate is None:
                gate = {
                    **base,
                    "kind": "gate",
                    "text": gate_id,
                    "gate_id": gate_id,
                    "decided_by": "task" if gate_id.startswith(TASK_GATES) else "work",
                    "work_id": payload.get("work_id"),
                }
                gates.setdefault(gate_id, []).append(gate)
                entries.append(gate)
            gate["decision"] = str(payload["decision"])
            if pending_gate == gate_id:
                pending_gate = None
        elif event.event_type is TaskEventType.ACTION_RESULT_RECORDED:
            external_ref = payload["external_ref"]
            entries.append(
                {
                    **base,
                    "kind": "output",
                    "text": f"{payload['action_id']} {payload['status']}",
                    "refs": tuple(str(ref) for ref in payload["evidence_refs"]),
                }
            )
            if external_ref is not None:
                entries[-1]["refs"] = (str(external_ref), *entries[-1]["refs"])
        elif event.event_type is TaskEventType.BUDGET_UPDATED:
            entries.append({**base, "kind": "message", "author": "human", "text": "budget updated"})
        elif event.event_type is TaskEventType.TASK_STATUS_CHANGED:
            status = TaskStatus(str(payload["status"]))
            entries.append({**base, "kind": "status", "text": status.value})
            if status in TERMINAL_STATUSES:
                pending_gate = None
                for question in questions.values():
                    if "answered_by" not in question:
                        question["closed"] = True
                for gate_entries in gates.values():
                    for gate in gate_entries:
                        if "decision" not in gate:
                            gate["closed"] = True
    return ThreadView(
        task_id=ordered[0].task_id,
        project_id=ordered[0].project_id,
        brief_ref=brief_ref,
        entries=tuple(ThreadEntry.model_validate(entry) for entry in entries),
        open_question_ids=tuple(
            question_id
            for question_id, entry in questions.items()
            if "answered_by" not in entry and not entry.get("closed", False)
        ),
        pending_gate=pending_gate,
    )


__all__ = ["ThreadEntry", "ThreadKind", "ThreadView", "thread_from_events"]
