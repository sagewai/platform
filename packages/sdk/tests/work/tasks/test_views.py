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
from sagewai.work.tasks.views import (
    actions_from_events,
    referenced_artifacts,
    task_work_ids,
    thread_from_events,
)

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


def _action_stream() -> tuple[TaskEvent, ...]:
    action = {
        "project_id": "p",
        "action": "report_delivered",
        "work_id": "w1",
        "risk": "medium",
        "reversibility": "compensatable",
        "scope": "https://github.com/o/r/issues/1",
        "evidence_refs": ["artifact://sha256:" + "b" * 64],
        "rollback": "delete_comment",
        "post_check": "comment_read_back",
    }
    shared_ref = "artifact://sha256:" + "d" * 64
    artifact_external_ref = "artifact://sha256:" + "e" * 64
    artifact_action = {
        "project_id": "p",
        "action": "artifact_delivered",
        "work_id": "w2",
        "risk": "low",
        "reversibility": "none",
        "scope": "https://github.com/o/r/issues/2",
        "evidence_refs": [shared_ref],
        "rollback": None,
        "post_check": None,
    }
    return (
        _event(
            1,
            TaskEventType.BRIEF_RECORDED,
            {"brief_ref": "artifact://sha256:" + "a" * 64, "summary": "s"},
        ),
        _event(
            2,
            TaskEventType.ACTION_INTENT_RECORDED,
            {
                "action_id": "deliver:w1:2",
                "work_id": "w1",
                "gate_id": "deliver:w1:2",
                "action": action,
            },
        ),
        _event(
            3,
            TaskEventType.ACTION_RESULT_RECORDED,
            {
                "work_id": "w1",
                "project_id": "p",
                "action_id": "deliver:w1:2",
                "status": "succeeded",
                "external_ref": "https://github.com/o/r/issues/1#issuecomment-9",
                "evidence_refs": ["artifact://sha256:" + "c" * 64],
                "started_at": NOW.isoformat(),
                "completed_at": NOW.isoformat(),
            },
        ),
        _event(
            4,
            TaskEventType.OBSERVATION_RECORDED,
            {
                "work_id": "w1",
                "action_id": "deliver:w1:2",
                "check": "comment_read_back",
                "passed": True,
                "detail": "read back",
                "evidence_refs": [],
            },
        ),
        _event(
            5,
            TaskEventType.ACTION_INTENT_RECORDED,
            {
                "action_id": "deliver:w2:5",
                "work_id": "w2",
                "gate_id": "deliver:w2:5",
                "action": artifact_action,
            },
        ),
        _event(
            6,
            TaskEventType.ACTION_RESULT_RECORDED,
            {
                "work_id": "w2",
                "project_id": "p",
                "action_id": "deliver:w2:5",
                "status": "succeeded",
                "external_ref": artifact_external_ref,
                "evidence_refs": [shared_ref],
                "started_at": NOW.isoformat(),
                "completed_at": NOW.isoformat(),
            },
        ),
        _event(7, TaskEventType.STEP_WORK_STARTED, {"step_id": "s1", "work_id": "w1"}),
        _event(8, TaskEventType.STEP_WORK_STARTED, {"step_id": "s2", "work_id": "w2"}),
        _event(9, TaskEventType.STEP_WORK_STARTED, {"step_id": "s1", "work_id": "w1"}),
    )


def _shuffled_action_stream() -> tuple[TaskEvent, ...]:
    events = _action_stream()
    return (
        events[8],
        events[2],
        events[0],
        events[5],
        events[1],
        events[7],
        events[3],
        events[6],
        events[4],
    )


def test_an_action_folds_intent_result_and_observation_into_one_record() -> None:
    records = actions_from_events(_shuffled_action_stream())

    assert [record.action_id for record in records] == ["deliver:w1:2", "deliver:w2:5"]
    record = records[0]
    assert record.work_id == "w1"
    assert record.action == "report_delivered"
    assert record.reversibility == "compensatable"
    assert record.risk == "medium"
    assert record.scope == "https://github.com/o/r/issues/1"
    assert record.rollback == "delete_comment"
    assert record.post_check == "comment_read_back"
    assert record.gate_id == "deliver:w1:2"
    assert record.requested_at == NOW
    assert record.status == "succeeded"
    assert record.external_ref == "https://github.com/o/r/issues/1#issuecomment-9"
    assert record.completed_at == NOW
    assert record.check == "comment_read_back"
    assert record.passed is True
    assert record.detail == "read back"
    assert record.evidence_refs == (
        "artifact://sha256:" + "b" * 64,
        "artifact://sha256:" + "c" * 64,
    )
    artifact_record = records[1]
    shared_ref = "artifact://sha256:" + "d" * 64
    assert artifact_record.external_ref == "artifact://sha256:" + "e" * 64
    assert artifact_record.evidence_refs == (shared_ref,)
    assert artifact_record.evidence_refs.count(shared_ref) == 1


def test_an_action_with_no_result_yet_reports_no_status() -> None:
    records = actions_from_events(_action_stream()[:2])

    assert records[0].status is None
    assert records[0].external_ref is None
    assert records[0].completed_at is None
    assert records[0].check is None
    assert records[0].passed is None
    assert records[0].detail is None


def test_a_refused_result_without_an_intent_is_skipped() -> None:
    events = (
        *_action_stream(),
        _event(
            10,
            TaskEventType.ACTION_RESULT_RECORDED,
            {
                "work_id": "w3",
                "project_id": "p",
                "action_id": "revert:w3:refused",
                "status": "failed",
                "external_ref": None,
                "evidence_refs": [],
                "started_at": NOW.isoformat(),
                "completed_at": NOW.isoformat(),
            },
        ),
        _event(
            11,
            TaskEventType.OBSERVATION_RECORDED,
            {
                "work_id": "w3",
                "action_id": "revert:w3:refused",
                "check": "rollback_refused",
                "passed": False,
                "detail": "not a GitHub pull request URL",
                "evidence_refs": [],
            },
        ),
    )

    records = actions_from_events(events)

    assert [record.action_id for record in records] == ["deliver:w1:2", "deliver:w2:5"]


def test_referenced_artifacts_are_the_brief_and_every_artifact_evidence_ref() -> None:
    refs = referenced_artifacts(_action_stream())

    assert refs == frozenset(
        {
            "artifact://sha256:" + "a" * 64,
            "artifact://sha256:" + "b" * 64,
            "artifact://sha256:" + "c" * 64,
            "artifact://sha256:" + "d" * 64,
            "artifact://sha256:" + "e" * 64,
        }
    )


def test_task_work_ids_are_unique_and_in_first_start_order() -> None:
    assert task_work_ids(_shuffled_action_stream()) == ("w1", "w2")
