# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""One project's Task attention merged with its Work attention, soonest due first."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sagewai.work import (
    ActionRequest,
    Reversibility,
    WorkEvent,
    WorkEventType,
    WorkRecord,
    WorkStore,
)
from sagewai.work.tasks.events import TaskEvent, TaskEventType, fold_record
from sagewai.work.tasks.inbox import decision_inbox
from sagewai.work.tasks.models import TaskStatus
from sagewai.work.tasks.store import TaskStore
from sagewai.work.tasks.writer import TaskWriter, status_entry
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _record, _task

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def stores(dialect_engine) -> tuple[TaskStore, WorkStore]:  # noqa: F811
    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    return task_store, work_store


def _task_event(task, sequence: int, event_type: TaskEventType, payload: dict) -> TaskEvent:
    return TaskEvent(
        id=f"{task.id}-{sequence}",
        project_id=task.project_id,
        task_id=task.id,
        sequence=sequence,
        event_type=event_type,
        actor_type="system",
        actor_ref="coordinator",
        payload_json=payload,
        created_at=NOW,
    )


async def _create(store: TaskStore, task_id: str, extra: tuple = ()) -> None:
    task = _task(task_id, project_id="p")
    events = (
        _task_event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),
        *(
            _task_event(task, index, kind, payload)
            for index, (kind, payload) in enumerate(extra, start=2)
        ),
    )
    await store.create(task, events=events, record=fold_record(_record(task), events))


def _question(
    question_id: str,
    text: str,
    *,
    defaultable: bool,
    attention_version: int,
    default: str | None = None,
) -> dict:
    return {
        "id": question_id,
        "text": text,
        "kind": "text",
        "options": [],
        "default": default,
        "defaultable": defaultable,
        "rationale": "",
        "attention_version": attention_version,
    }


async def _seed_presented_task(
    store: TaskStore, task_id: str, *, urgency: str, due_at: datetime
) -> None:
    """A Task holding an open gate the coordinator has already presented."""
    await _create(
        store,
        task_id,
        extra=(
            (
                TaskEventType.GATE_REQUESTED,
                {"gate_id": f"plan:{task_id}:1", "question": "Approve the plan"},
            ),
            (
                TaskEventType.NOTIFICATION_PRESENTED,
                {
                    "channel": "console",
                    "ref": f"console:{task_id}",
                    "attention_id": f"plan:{task_id}:1",
                    "urgency": urgency,
                    "due_at": due_at.isoformat(),
                    "summary": "Approve the plan",
                    "evidence_refs": [],
                },
            ),
        ),
    )


async def _seed_clarifying_task(store: TaskStore, task_id: str) -> None:
    await _seed_clarifying_task_with_questions(
        store,
        task_id,
        (_question("q1", "Which repository?", defaultable=False, attention_version=2),),
    )


async def _seed_clarifying_task_with_questions(
    store: TaskStore, task_id: str, questions: tuple[dict, ...]
) -> None:
    await _create(
        store,
        task_id,
        extra=(
            (
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": list(questions),
                    "deadline_at": (NOW + timedelta(hours=2)).isoformat(),
                },
            ),
            (
                TaskEventType.TASK_STATUS_CHANGED,
                {"status": TaskStatus.CLARIFYING.value},
            ),
        ),
    )


async def _seed_blocked_task(store: TaskStore, task_id: str) -> None:
    """A Task waiting on a human that no channel ever carried."""
    await _create(store, task_id)
    record = await store.load_record(task_id, project_id="p")
    await TaskWriter(store).append(record, [status_entry(record, TaskStatus.BLOCKED)], now=NOW)


async def _seed_blocked_task_with_question(store: TaskStore, task_id: str) -> None:
    await _create(
        store,
        task_id,
        extra=(
            (
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": [
                        _question(
                            "blocked-q",
                            "Which issue is blocked?",
                            defaultable=False,
                            attention_version=3,
                        )
                    ],
                    "deadline_at": (NOW + timedelta(hours=2)).isoformat(),
                },
            ),
            (
                TaskEventType.TASK_STATUS_CHANGED,
                {"status": TaskStatus.BLOCKED.value},
            ),
        ),
    )


async def _seed_task_with_gate_and_question(store: TaskStore, task_id: str) -> None:
    await _create(
        store,
        task_id,
        extra=(
            (
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": [
                        _question(
                            "gate-q",
                            "Which branch?",
                            defaultable=False,
                            attention_version=4,
                        )
                    ],
                    "deadline_at": (NOW + timedelta(hours=2)).isoformat(),
                },
            ),
            (
                TaskEventType.GATE_REQUESTED,
                {"gate_id": f"plan:{task_id}:1", "question": "Approve the plan"},
            ),
            (
                TaskEventType.NOTIFICATION_PRESENTED,
                {
                    "channel": "console",
                    "ref": f"console:{task_id}",
                    "attention_id": f"plan:{task_id}:1",
                    "urgency": "today",
                    "due_at": (NOW + timedelta(hours=4)).isoformat(),
                    "summary": "Approve the plan",
                    "evidence_refs": [],
                },
            ),
        ),
    )


def _work_action(project_id: str, work_id: str) -> dict:
    return ActionRequest(
        project_id=project_id,
        action="merge",
        work_id=work_id,
        risk="medium",
        reversibility=Reversibility.COMPENSATABLE,
        scope="https://github.com/o/r/pull/3",
        evidence_refs=("pr://3",),
        rollback="revert_pull_request",
        post_check="merged_sha_read_back",
    ).model_dump(mode="json")


async def _seed_task_with_mirrored_work_gate(store: TaskStore, task_id: str, work_id: str) -> None:
    gate_id = f"merge:{work_id}:3"
    await _create(
        store,
        task_id,
        extra=(
            (
                TaskEventType.GATE_REQUESTED,
                {
                    "gate_id": gate_id,
                    "question": "Approve merge of PR #3.",
                    "action": _work_action("p", work_id),
                    "work_id": work_id,
                    "attention_id": gate_id,
                    "decided_by": "work",
                },
            ),
            (
                TaskEventType.NOTIFICATION_PRESENTED,
                {
                    "channel": "console",
                    "ref": f"console:{task_id}",
                    "attention_id": gate_id,
                    "urgency": "today",
                    "due_at": (NOW + timedelta(hours=3)).isoformat(),
                    "summary": "Approve merge of PR #3.",
                    "evidence_refs": ["pr://3", work_id],
                },
            ),
        ),
    )


async def _seed_running_task(store: TaskStore, task_id: str) -> None:
    await _create(store, task_id)
    record = await store.load_record(task_id, project_id="p")
    await TaskWriter(store).append(record, [status_entry(record, TaskStatus.EXECUTING)], now=NOW)


async def _seed_work_gate(store: WorkStore, work_id: str, *, project_id: str = "p") -> None:
    await store.save_work(
        WorkRecord(
            work_id=work_id,
            project_id=project_id,
            source_ref="https://github.com/o/r/issues/1",
            profile="software",
            status="READY_TO_MERGE",
            active_run_id=None,
            pending_gate=f"merge:{work_id}:3",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    for sequence, (event_type, payload) in enumerate(
        (
            (WorkEventType.WORK_CREATED, {"work_id": work_id}),
            (
                WorkEventType.GATE_REQUESTED,
                {"gate_id": f"merge:{work_id}:3", "question": "Approve merge of PR #3."},
            ),
        ),
        start=1,
    ):
        await store.append_event(
            WorkEvent(
                id=f"{work_id}-{sequence}",
                project_id=project_id,
                work_id=work_id,
                sequence=sequence,
                event_type=event_type,
                actor_type="github_lifecycle",
                actor_ref="policy",
                payload_json=payload,
                created_at=NOW,
            )
        )


@pytest.mark.asyncio
async def test_a_presented_task_item_carries_its_urgency_and_due_time(stores) -> None:
    task_store, work_store = stores
    await _seed_presented_task(task_store, "t-1", urgency="today", due_at=NOW + timedelta(hours=4))

    items = await decision_inbox(
        task_store=task_store, work_store=work_store, project_id="p", now=NOW
    )

    assert [item.kind for item in items] == ["task"]
    assert items[0].task_id == "t-1"
    assert items[0].attention_id == "plan:t-1:1"
    assert items[0].urgency == "today"
    assert items[0].due_at == NOW + timedelta(hours=4)
    assert items[0].summary == "Approve the plan"
    assert items[0].gate_id == "plan:t-1:1"
    assert items[0].decided_by == "task"


@pytest.mark.asyncio
async def test_a_task_that_needs_you_but_was_never_presented_still_appears(stores) -> None:
    task_store, work_store = stores
    await _seed_blocked_task(task_store, "t-2")

    items = await decision_inbox(
        task_store=task_store, work_store=work_store, project_id="p", now=NOW
    )

    assert [item.attention_id for item in items] == ["blocked"]
    assert items[0].urgency == "now"
    assert items[0].due_at == NOW


@pytest.mark.asyncio
async def test_status_attention_is_not_replaced_by_an_open_question(stores) -> None:
    task_store, work_store = stores
    await _seed_blocked_task_with_question(task_store, "t-blocked-question")

    items = await decision_inbox(
        task_store=task_store, work_store=work_store, project_id="p", now=NOW
    )

    assert [item.attention_id for item in items] == ["blocked"]
    assert items[0].summary == "Build the thing"


@pytest.mark.asyncio
async def test_work_attention_joins_the_same_inbox_sorted_by_due_time(stores) -> None:
    task_store, work_store = stores
    await _seed_presented_task(
        task_store, "t-1", urgency="this_week", due_at=NOW + timedelta(days=3)
    )
    await _seed_work_gate(work_store, "w1")

    items = await decision_inbox(
        task_store=task_store, work_store=work_store, project_id="p", now=NOW
    )

    assert [item.kind for item in items] == ["work", "task"]
    assert items[0].work_id == "w1"
    assert items[0].urgency == "today"
    assert items[0].due_at == NOW + timedelta(hours=24)
    assert items[0].gate_id == "merge:w1:3"
    assert items[0].decided_by == "work"


@pytest.mark.asyncio
async def test_a_gate_and_an_open_question_yield_the_gate_once(stores) -> None:
    task_store, work_store = stores
    await _seed_task_with_gate_and_question(task_store, "t-gate-question")

    items = await decision_inbox(
        task_store=task_store, work_store=work_store, project_id="p", now=NOW
    )

    assert [item.attention_id for item in items] == ["plan:t-gate-question:1"]
    assert items[0].gate_id == "plan:t-gate-question:1"
    assert items[0].attention_version is None


@pytest.mark.asyncio
async def test_open_clarification_carries_attention_version(stores) -> None:
    task_store, work_store = stores
    await _seed_clarifying_task(task_store, "t-clarify")

    items = await decision_inbox(
        task_store=task_store, work_store=work_store, project_id="p", now=NOW
    )

    assert [(item.attention_id, item.attention_version) for item in items] == [("q1", 2)]
    assert items[0].summary == "Which repository?"
    assert items[0].due_at == NOW + timedelta(hours=2)
    assert items[0].decided_by is None


@pytest.mark.asyncio
async def test_open_material_questions_fan_out_one_item_per_question(stores) -> None:
    task_store, work_store = stores
    await _seed_clarifying_task_with_questions(
        task_store,
        "t-two-questions",
        (
            _question("q1", "Which repository?", defaultable=False, attention_version=2),
            _question("q2", "Which branch?", defaultable=False, attention_version=5),
        ),
    )

    items = await decision_inbox(
        task_store=task_store, work_store=work_store, project_id="p", now=NOW
    )

    assert [(item.attention_id, item.attention_version) for item in items] == [
        ("q1", 2),
        ("q2", 5),
    ]
    assert [item.summary for item in items] == ["Which repository?", "Which branch?"]


@pytest.mark.asyncio
async def test_defaultable_questions_do_not_create_decision_items(stores) -> None:
    task_store, work_store = stores
    await _seed_clarifying_task_with_questions(
        task_store,
        "t-defaultable",
        (
            _question(
                "q-default", "Use main?", defaultable=True, attention_version=2, default="main"
            ),
        ),
    )

    assert (
        await decision_inbox(task_store=task_store, work_store=work_store, project_id="p", now=NOW)
        == ()
    )


@pytest.mark.asyncio
async def test_only_the_material_question_is_listed_beside_a_defaultable_one(stores) -> None:
    task_store, work_store = stores
    await _seed_clarifying_task_with_questions(
        task_store,
        "t-mixed",
        (
            _question("q-mat", "Which repository?", defaultable=False, attention_version=2),
            _question(
                "q-default", "Use main?", defaultable=True, attention_version=2, default="main"
            ),
        ),
    )

    items = await decision_inbox(
        task_store=task_store, work_store=work_store, project_id="p", now=NOW
    )

    assert [item.attention_id for item in items] == ["q-mat"]


@pytest.mark.asyncio
async def test_a_mirrored_work_gate_appears_once_as_the_task_item(stores) -> None:
    task_store, work_store = stores
    await _seed_task_with_mirrored_work_gate(task_store, "t-mirror", "w1")
    await _seed_work_gate(work_store, "w1")

    items = await decision_inbox(
        task_store=task_store, work_store=work_store, project_id="p", now=NOW
    )

    assert [item.kind for item in items] == ["task"]
    assert items[0].task_id == "t-mirror"
    assert items[0].work_id == "w1"
    assert items[0].gate_id == "merge:w1:3"
    assert items[0].decided_by == "work"


@pytest.mark.asyncio
async def test_a_task_with_no_attention_is_not_in_the_inbox(stores) -> None:
    task_store, work_store = stores
    await _seed_running_task(task_store, "t-3")

    assert (
        await decision_inbox(task_store=task_store, work_store=work_store, project_id="p", now=NOW)
        == ()
    )


@pytest.mark.asyncio
async def test_the_inbox_is_project_scoped(stores) -> None:
    task_store, work_store = stores
    await _seed_blocked_task(task_store, "t-2")

    assert (
        await decision_inbox(task_store=task_store, work_store=work_store, project_id="q", now=NOW)
        == ()
    )


@pytest.mark.asyncio
async def test_work_attention_is_project_scoped(stores) -> None:
    task_store, work_store = stores
    await _seed_work_gate(work_store, "w-other", project_id="q")

    assert (
        await decision_inbox(task_store=task_store, work_store=work_store, project_id="p", now=NOW)
        == ()
    )
