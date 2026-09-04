# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pure read-side folds over one Task's event stream."""

from __future__ import annotations

from datetime import datetime, timezone

from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import TaskStatus
from sagewai.work.tasks.views import thread_from_events

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _event(
    sequence: int,
    event_type: TaskEventType,
    payload: dict,
    *,
    author: str = "system",
) -> TaskEvent:
    return TaskEvent(
        id=f"e{sequence}",
        project_id="p",
        task_id="t1",
        sequence=sequence,
        event_type=event_type,
        actor_type=author,
        actor_ref="test",
        payload_json=payload,
        created_at=NOW,
    )


def _stream() -> tuple[TaskEvent, ...]:
    return (
        _event(1, TaskEventType.TASK_CREATED, {"title": "Retry queue"}),
        _event(
            2,
            TaskEventType.BRIEF_RECORDED,
            {"brief_ref": "artifact://sha256:" + "a" * 64, "summary": "Retry queue"},
        ),
        _event(
            3,
            TaskEventType.CLARIFICATION_REQUESTED,
            {
                "questions": [
                    {
                        "id": "q1",
                        "text": "Which branch?",
                        "defaultable": True,
                        "default": "main",
                        "attention_version": 1,
                    },
                    {
                        "id": "q2",
                        "text": "Which queue?",
                        "defaultable": False,
                        "default": None,
                        "attention_version": 1,
                    },
                ],
                "deadline_at": NOW.isoformat(),
            },
        ),
        _event(
            4,
            TaskEventType.CLARIFICATION_ANSWERED,
            {"question_id": "q2", "answer": "redis", "material": True},
        ),
        _event(
            5,
            TaskEventType.CLARIFICATION_DEFAULTED,
            {"question_id": "q1", "answer": "main"},
        ),
        _event(
            6,
            TaskEventType.TASK_MESSAGE,
            {"author": "coordinator", "text": "planning", "refs": []},
        ),
        _event(
            7,
            TaskEventType.PLAN_PROPOSED,
            {"version": 1, "steps": [], "acceptance_matrix": []},
        ),
        _event(
            8,
            TaskEventType.GATE_REQUESTED,
            {"gate_id": "plan:t1:1", "question": "Approve the plan."},
        ),
        _event(
            9,
            TaskEventType.GATE_DECIDED,
            {"gate_id": "plan:t1:1", "decision": "allow"},
        ),
        _event(10, TaskEventType.PLAN_ACCEPTED, {"version": 1}),
        _event(11, TaskEventType.TASK_STATUS_CHANGED, {"status": "EXECUTING"}),
    )


def test_the_thread_carries_the_brief_first() -> None:
    view = thread_from_events(_stream())

    assert view.task_id == "t1"
    assert view.project_id == "p"
    assert view.brief_ref == "artifact://sha256:" + "a" * 64
    assert view.entries[0].kind == "brief"
    assert view.entries[0].text == "Retry queue"


def test_each_question_carries_its_own_answer_and_who_gave_it() -> None:
    entries = {
        entry.attention_id: entry
        for entry in thread_from_events(_stream()).entries
        if entry.kind == "question"
    }

    assert entries["q1"].id == "3:q1"
    assert "question_id" not in entries["q1"].model_dump()
    assert entries["q1"].answer == "main"
    assert entries["q1"].answered_by == "default"
    assert entries["q1"].defaultable is True
    assert entries["q1"].deadline_at == NOW
    assert entries["q1"].attention_id == "q1"
    assert entries["q1"].attention_version == 1
    assert entries["q2"].id == "3:q2"
    assert entries["q2"].answer == "redis"
    assert entries["q2"].answered_by == "human"
    assert entries["q2"].defaultable is False
    assert entries["q2"].attention_id == "q2"
    assert entries["q2"].attention_version == 1


def test_a_gate_carries_its_decision_and_the_plan_its_version() -> None:
    view = thread_from_events(_stream())
    gate = next(entry for entry in view.entries if entry.kind == "gate")
    plans = [entry for entry in view.entries if entry.kind == "plan"]

    assert gate.gate_id == "plan:t1:1"
    assert gate.decision == "allow"
    assert gate.decided_by == "task"
    assert gate.work_id is None
    assert [entry.plan_version for entry in plans] == [1, 1]
    assert [entry.text for entry in plans] == [
        "plan proposed at version 1",
        "plan accepted at version 1",
    ]
    assert view.pending_gate is None
    assert view.open_question_ids == ()


def test_an_undecided_gate_and_an_unanswered_question_stay_open() -> None:
    view = thread_from_events(_stream()[:4])

    assert view.pending_gate is None
    assert view.open_question_ids == ("q1",)


def test_the_open_gate_is_the_undecided_one() -> None:
    view = thread_from_events(_stream()[:8])

    assert view.pending_gate == "plan:t1:1"
    assert next(entry for entry in view.entries if entry.kind == "gate").decision is None


def test_terminal_tasks_close_unanswered_questions_and_undecided_gates() -> None:
    view = thread_from_events(
        (
            _event(1, TaskEventType.TASK_CREATED, {"title": "Retry queue"}),
            _event(
                2,
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": [
                        {
                            "id": "q1",
                            "text": "Which branch?",
                            "defaultable": True,
                            "default": "main",
                            "attention_version": 2,
                        },
                        {
                            "id": "q2",
                            "text": "Which queue?",
                            "defaultable": False,
                            "default": None,
                            "attention_version": 2,
                        },
                    ],
                    "deadline_at": NOW.isoformat(),
                },
            ),
            _event(
                3,
                TaskEventType.GATE_REQUESTED,
                {"gate_id": "plan:t1:2", "question": "Approve the plan."},
            ),
            _event(
                4,
                TaskEventType.TASK_STATUS_CHANGED,
                {"status": TaskStatus.CANCELLED.value},
            ),
        )
    )

    questions = [entry for entry in view.entries if entry.kind == "question"]
    gate = next(entry for entry in view.entries if entry.kind == "gate")
    assert view.pending_gate is None
    assert view.open_question_ids == ()
    assert [entry.closed for entry in questions] == [True, True]
    assert gate.closed is True


def test_a_mirrored_work_gate_points_to_the_work_gate_route() -> None:
    view = thread_from_events(
        (
            _event(1, TaskEventType.TASK_CREATED, {"title": "Retry queue"}),
            _event(
                2,
                TaskEventType.GATE_REQUESTED,
                {
                    "gate_id": "merge:w1:1",
                    "question": "Approve merge.",
                    "work_id": "w1",
                },
            ),
        )
    )

    gate = view.entries[0]
    assert gate.gate_id == "merge:w1:1"
    assert gate.decided_by == "work"
    assert gate.work_id == "w1"


def test_a_mirrored_work_gate_prefers_the_payload_owner_over_the_gate_prefix() -> None:
    view = thread_from_events(
        (
            _event(1, TaskEventType.TASK_CREATED, {"title": "Retry queue"}),
            _event(
                2,
                TaskEventType.GATE_REQUESTED,
                {
                    "gate_id": "rollback:w1",
                    "question": "Approve rollback.",
                    "decided_by": "work",
                    "work_id": "w1",
                },
            ),
        )
    )

    gate = view.entries[0]
    assert gate.gate_id == "rollback:w1"
    assert gate.decided_by == "work"
    assert gate.work_id == "w1"


def test_a_repeated_gate_id_mints_a_new_entry_and_decides_the_latest_open_one() -> None:
    view = thread_from_events(
        (
            _event(
                1,
                TaskEventType.GATE_REQUESTED,
                {"gate_id": "rollback:w1", "question": "First rollback."},
            ),
            _event(
                2,
                TaskEventType.GATE_REQUESTED,
                {"gate_id": "rollback:w1", "question": "Second rollback."},
            ),
            _event(
                3,
                TaskEventType.GATE_DECIDED,
                {"gate_id": "rollback:w1", "decision": "allow"},
            ),
            _event(4, TaskEventType.GATE_DECIDED, {"gate_id": "rollback:w1", "decision": "deny"}),
        )
    )

    gates = [entry for entry in view.entries if entry.kind == "gate"]
    assert [entry.id for entry in gates] == ["1", "2"]
    assert [entry.decision for entry in gates] == ["deny", "allow"]


def test_a_task_gate_that_carries_a_work_id_still_points_to_the_task_gate_route() -> None:
    view = thread_from_events(
        (
            _event(
                1,
                TaskEventType.GATE_REQUESTED,
                {
                    "gate_id": "rollback:w1",
                    "question": "Approve rollback.",
                    "work_id": "w1",
                },
            ),
        )
    )

    gate = view.entries[0]
    assert gate.decided_by == "task"
    assert gate.work_id is None


def test_a_gate_decided_without_a_request_still_appears_decided() -> None:
    view = thread_from_events(
        (
            _event(
                1,
                TaskEventType.GATE_DECIDED,
                {"gate_id": "deliver:w1:1", "decision": "allow"},
            ),
        )
    )

    (gate,) = view.entries
    assert (gate.kind, gate.gate_id, gate.decision) == ("gate", "deliver:w1:1", "allow")
    assert gate.decided_by == "task"
    assert gate.work_id is None
    assert view.pending_gate is None


def test_output_and_budget_events_are_rendered_in_sequence_order() -> None:
    view = thread_from_events(
        (
            _event(
                3,
                TaskEventType.BUDGET_UPDATED,
                {"budget": {"max_usd": "12.00"}, "revision": 2},
            ),
            _event(
                2,
                TaskEventType.ACTION_RESULT_RECORDED,
                {
                    "work_id": "w1",
                    "project_id": "p",
                    "action_id": "deliver:w1:2",
                    "status": "succeeded",
                    "external_ref": "https://github.com/o/r/issues/1#issuecomment-9",
                    "evidence_refs": ["artifact://sha256:" + "b" * 64],
                    "started_at": NOW.isoformat(),
                    "completed_at": NOW.isoformat(),
                },
            ),
            _event(1, TaskEventType.TASK_STATUS_CHANGED, {"status": "EXECUTING"}),
        )
    )

    assert [entry.sequence for entry in view.entries] == sorted(
        entry.sequence for entry in view.entries
    )
    assert [(entry.kind, entry.text) for entry in view.entries] == [
        ("status", "EXECUTING"),
        ("output", "deliver:w1:2 succeeded"),
        ("message", "budget updated"),
    ]
    assert view.entries[1].refs == (
        "https://github.com/o/r/issues/1#issuecomment-9",
        "artifact://sha256:" + "b" * 64,
    )
