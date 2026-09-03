# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task event vocabulary and pure projection folds."""

from __future__ import annotations

from datetime import datetime, timezone

from sagewai.work.tasks.events import (
    TaskEvent,
    TaskEventType,
    board_column,
    derive_attention,
    fold_record,
)
from sagewai.work.tasks.models import (
    AttentionOwner,
    BoardColumn,
    TaskKind,
    TaskOrigin,
    TaskRecord,
    TaskStatus,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _record(**updates) -> TaskRecord:
    values = dict(
        task_id="task-1",
        project_id="project-a",
        kind=TaskKind.BATCH,
        origin=TaskOrigin.HUMAN,
        title="Build the thing",
        profile="software",
        status=TaskStatus.PLANNING,
        last_event_sequence=0,
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(updates)
    return TaskRecord.model_validate(values)


def _event(sequence: int, event_type: TaskEventType, payload: dict) -> TaskEvent:
    return TaskEvent(
        id=f"event-{sequence}",
        project_id="project-a",
        task_id="task-1",
        sequence=sequence,
        event_type=event_type,
        actor_type="system",
        actor_ref="test",
        payload_json=payload,
        created_at=NOW,
    )


def test_board_column_follows_status_and_attention_owner() -> None:
    assert board_column(TaskStatus.PLANNING, None) is BoardColumn.INBOX
    assert board_column(TaskStatus.EXECUTING, AttentionOwner.USER) is BoardColumn.NEEDS_YOU
    assert (
        board_column(TaskStatus.EXECUTING, AttentionOwner.EXTERNAL)
        is BoardColumn.IN_PROGRESS
    )
    assert (
        board_column(TaskStatus.CLARIFYING, AttentionOwner.SYSTEM)
        is BoardColumn.IN_PROGRESS
    )
    assert board_column(TaskStatus.SCHEDULED, None) is BoardColumn.PLANNED
    assert board_column(TaskStatus.PAUSED, None) is BoardColumn.PLANNED
    assert board_column(TaskStatus.COMPLETE, None) is BoardColumn.DONE
    assert board_column(TaskStatus.CANCELLED, None) is BoardColumn.DONE


def test_derive_attention_user_wins_over_explicit_owner() -> None:
    assert derive_attention(
        status=TaskStatus.BLOCKED,
        pending_gate=None,
        pending_material_questions=0,
        explicit=None,
    ) == (AttentionOwner.USER, "blocked")
    assert derive_attention(
        status=TaskStatus.EXECUTING,
        pending_gate="merge:1",
        pending_material_questions=0,
        explicit=None,
    ) == (AttentionOwner.USER, "gate:merge:1")
    assert derive_attention(
        status=TaskStatus.CLARIFYING,
        pending_gate=None,
        pending_material_questions=2,
        explicit=None,
    ) == (AttentionOwner.USER, "questions:2")
    assert derive_attention(
        status=TaskStatus.CLARIFYING,
        pending_gate=None,
        pending_material_questions=0,
        explicit=None,
    ) == (AttentionOwner.SYSTEM, "awaiting defaults")
    assert derive_attention(
        status=TaskStatus.EXECUTING,
        pending_gate=None,
        pending_material_questions=0,
        explicit=(AttentionOwner.EXTERNAL, "waiting for fleet worker"),
    ) == (AttentionOwner.EXTERNAL, "waiting for fleet worker")
    assert derive_attention(
        status=TaskStatus.COMPLETE,
        pending_gate=None,
        pending_material_questions=0,
        explicit=None,
    ) == (None, None)


def test_fold_record_applies_status_gates_questions_cycles_and_budget() -> None:
    events = [
        _event(1, TaskEventType.TASK_CREATED, {}),
        _event(
            2,
            TaskEventType.CLARIFICATION_REQUESTED,
            {
                "questions": [
                    {"id": "q1", "defaultable": True},
                    {"id": "q2", "defaultable": False},
                ]
            },
        ),
        _event(3, TaskEventType.TASK_STATUS_CHANGED, {"status": "CLARIFYING"}),
    ]
    folded = fold_record(_record(), events)
    assert folded.status is TaskStatus.CLARIFYING
    assert folded.last_event_sequence == 3
    assert folded.pending_questions == 2
    assert folded.pending_material_questions == 1
    assert folded.attention_owner is AttentionOwner.USER
    assert folded.board_column is BoardColumn.NEEDS_YOU

    events += [
        _event(4, TaskEventType.CLARIFICATION_ANSWERED, {"question_id": "q2", "material": True}),
        _event(5, TaskEventType.CLARIFICATION_DEFAULTED, {"question_id": "q1"}),
        _event(6, TaskEventType.TASK_STATUS_CHANGED, {"status": "PLANNING"}),
        _event(7, TaskEventType.PLAN_ACCEPTED, {"version": 1}),
        _event(8, TaskEventType.GATE_REQUESTED, {"gate_id": "plan:1:1"}),
        _event(9, TaskEventType.TASK_STATUS_CHANGED, {"status": "PLAN_PROPOSED"}),
    ]
    folded = fold_record(_record(), events)
    assert folded.last_event_sequence == 9
    assert folded.pending_questions == 0 and folded.pending_material_questions == 0
    assert folded.plan_version == 1
    assert folded.pending_gate == "plan:1:1"
    assert folded.board_column is BoardColumn.NEEDS_YOU

    events += [
        _event(
            10,
            TaskEventType.GATE_DECIDED,
            {"gate_id": "plan:1:1", "decision": "allow"},
        ),
        _event(11, TaskEventType.CYCLE_STARTED, {"cycle": 1}),
        _event(12, TaskEventType.TASK_STATUS_CHANGED, {"status": "EXECUTING"}),
        _event(
            13,
            TaskEventType.ATTENTION_CHANGED,
            {"owner": "external", "reason": "waiting for fleet worker"},
        ),
        _event(
            14,
            TaskEventType.BUDGET_RECORDED,
            {"budget_used": {"works": 1, "attempts": 2, "usd_actual": "0.50"}},
        ),
    ]
    folded = fold_record(_record(), events)
    assert folded.last_event_sequence == 14
    assert folded.pending_gate is None
    assert folded.current_cycle == 1
    assert folded.attention_owner is AttentionOwner.EXTERNAL
    assert folded.waiting_reason == "waiting for fleet worker"
    assert folded.board_column is BoardColumn.IN_PROGRESS
    assert folded.budget_used.works == 1 and str(folded.budget_used.usd_actual) == "0.50"

    events += [
        _event(
            15,
            TaskEventType.CYCLE_COMPLETED,
            {"cycle": 1, "next_run_at": "2026-09-03T06:00:00+00:00"},
        ),
        _event(16, TaskEventType.TASK_STATUS_CHANGED, {"status": "SCHEDULED"}),
    ]
    folded = fold_record(_record(), events)
    assert folded.last_event_sequence == 16
    assert folded.next_run_at == datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)
    assert folded.attention_owner is None
    assert folded.board_column is BoardColumn.PLANNED
    assert folded.updated_at == NOW


def test_fold_record_ignores_events_of_other_tasks() -> None:
    foreign = _event(1, TaskEventType.TASK_STATUS_CHANGED, {"status": "COMPLETE"}).model_copy(
        update={"task_id": "task-2"}
    )
    assert fold_record(_record(), [foreign]).status is TaskStatus.PLANNING


def test_terminal_status_clears_questions_and_attention() -> None:
    events = [
        _event(1, TaskEventType.CLARIFICATION_REQUESTED, {"questions": [{"id": "q1", "defaultable": False}]}),
        _event(2, TaskEventType.TASK_STATUS_CHANGED, {"status": "CLARIFYING"}),
        _event(3, TaskEventType.TASK_STATUS_CHANGED, {"status": "CANCELLED"}),
    ]
    folded = fold_record(_record(), events)
    assert folded.pending_questions == 0 and folded.pending_material_questions == 0
    assert folded.attention_owner is None and folded.waiting_reason is None
    assert folded.board_column is BoardColumn.DONE


def test_an_explicit_user_owner_holds_needs_you_while_scheduled() -> None:
    """A health alert on a SCHEDULED Task must keep the Needs-you column."""
    record = _record(status=TaskStatus.SCHEDULED)
    folded = fold_record(
        record,
        (
            _event(
                1,
                TaskEventType.ATTENTION_CHANGED,
                {"owner": "user", "reason": "health:cost_spike:5"},
            ),
        ),
    )
    assert folded.attention_owner is AttentionOwner.USER
    assert folded.waiting_reason == "health:cost_spike:5"
    assert folded.board_column is BoardColumn.NEEDS_YOU


def test_a_terminal_status_never_holds_an_explicit_owner() -> None:
    record = _record(status=TaskStatus.COMPLETE)
    folded = fold_record(
        record,
        (_event(1, TaskEventType.ATTENTION_CHANGED, {"owner": "user", "reason": "x"}),),
    )
    assert folded.attention_owner is None
    assert folded.board_column is BoardColumn.DONE


def test_status_change_drops_explicit_wait_reason() -> None:
    waiting = _record(status=TaskStatus.EXECUTING, attention_owner=AttentionOwner.EXTERNAL, waiting_reason="waiting for fleet worker")
    folded = fold_record(waiting, [_event(1, TaskEventType.TASK_STATUS_CHANGED, {"status": "ASSESSING"})])
    assert folded.attention_owner is AttentionOwner.SYSTEM
    assert folded.waiting_reason == "working"


def test_fold_applies_only_unapplied_events() -> None:
    events = [_event(1, TaskEventType.CLARIFICATION_REQUESTED, {"questions": [{"id": "q1", "defaultable": True}]})]
    once = fold_record(_record(), events)
    assert once.pending_questions == 1
    assert once.last_event_sequence == 1
    assert fold_record(once, []) == once
    assert fold_record(once, events) == once
