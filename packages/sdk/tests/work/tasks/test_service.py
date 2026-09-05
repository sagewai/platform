# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task creation runs intake, records the first events, and lands the human decisions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, get_args, get_origin, get_type_hints

import pytest

from sagewai.work.tasks.events import TaskEventType
from sagewai.work.tasks.models import (
    AttentionOwner,
    Authority,
    BoardColumn,
    Budget,
    ExecutionRoute,
    GateMode,
    ReportTarget,
    RoleAlias,
    RoutingPolicy,
    RuntimeRef,
    SoftwareTarget,
    TaskDefaults,
    TaskKind,
    TaskOrigin,
    TaskStatus,
)
from sagewai.work.tasks.plan import plan_from_events
from sagewai.work.tasks.service import (
    ActionNotFoundError,
    ClarificationDeadlines,
    TaskCreationError,
    TaskDecisionError,
    TaskNotFoundError,
    TaskService,
)
from sagewai.work.tasks.store import StaleTaskError, TaskStore
from sagewai.work.tasks.transitions import IllegalTransitionError
from sagewai.work.tasks.writer import TaskWriter, status_entry
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)
SOFTWARE_BRIEF = (
    "Implement the retry queue in the payments service repository, add the failing test first, "
    "and open a pull request when the deterministic verification command passes."
)
TARGET = SoftwareTarget(
    repository_path="/tmp/repo", owner="o", repo="r", verification_image="sha256:" + "b" * 64
)


@pytest.fixture
async def store(dialect_engine) -> TaskStore:  # noqa: F811
    result = TaskStore(engine=dialect_engine)
    await result.init()
    await result.put_defaults(
        TaskDefaults(project_id="project-a", target=TARGET, routing=RoutingPolicy(prefer_free_implementation=True)),
        expected_revision=0,
    )
    return result


@pytest.fixture
def service(store: TaskStore, tmp_path) -> TaskService:
    from sagewai.artifacts.object_store import LocalArtifactStore

    return TaskService(store=store, artifact_store=LocalArtifactStore(root=tmp_path / "objects"))


@pytest.fixture
async def service_and_record(service: TaskService, store: TaskStore):
    """A Task past intake, with no open question — the plain write target."""
    _task, record = await service.create(
        SOFTWARE_BRIEF, project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    return service, record


@pytest.fixture
async def clarifying(service: TaskService, store: TaskStore):
    """A Task intake could not route, so it asked; returns the first open question.

    ``"tidy up"`` tokenizes to fewer than four scoring tokens, so ``intake.route`` lands in the
    ``synthesis`` band and emits ``CLARIFICATION_REQUESTED`` as event 4. Every question the
    template mints carries ``attention_version`` 1.
    """
    task, record = await service.create(
        "tidy up", project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    events = await store.read_events(task.id, project_id="project-a")
    questions = events[3].payload_json["questions"]
    assert questions and questions[0]["attention_version"] == 1
    return service, record, str(questions[0]["id"])


@pytest.mark.asyncio
async def test_create_records_the_first_three_events_and_the_template_binding(
    service: TaskService, store: TaskStore
) -> None:
    task, record = await service.create(
        SOFTWARE_BRIEF, project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    assert task.template_id == "software_delivery"
    assert task.profile == "software" and task.target == TARGET
    assert task.kind is TaskKind.BATCH and task.schedule is None
    assert task.authority == Authority(plan=GateMode.REQUIRE)
    assert task.routing.roles[RoleAlias.IMPLEMENTER] == (RuntimeRef.CODEX,)
    assert task.routing.prefer_free_implementation is True
    assert task.execution == ExecutionRoute(route="local")
    assert task.brief_summary.startswith("Implement the retry queue")
    assert record.status is TaskStatus.PLANNING and record.last_event_sequence == 3
    events = await store.read_events(task.id, project_id="project-a")
    assert [event.event_type for event in events] == [
        TaskEventType.TASK_CREATED,
        TaskEventType.BRIEF_RECORDED,
        TaskEventType.INTAKE_RECORDED,
    ]
    assert events[1].payload_json["brief_ref"] == task.brief_ref.storage_ref
    assert events[2].payload_json["template_id"] == "software_delivery"


@pytest.mark.asyncio
async def test_create_that_asks_first_starts_clarifying(service: TaskService, store: TaskStore) -> None:
    task, record = await service.create(
        "tidy up", project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    assert record.status is TaskStatus.CLARIFYING
    assert record.pending_questions >= 1
    events = await store.read_events(task.id, project_id="project-a")
    assert events[3].event_type is TaskEventType.CLARIFICATION_REQUESTED
    assert events[3].payload_json["deadline_at"] == "2026-09-03T13:00:00+00:00"
    assert events[4].payload_json == {"status": "CLARIFYING"}


@pytest.mark.asyncio
async def test_non_human_origin_never_prefers_the_free_implementation(service: TaskService) -> None:
    task, _record = await service.create(
        SOFTWARE_BRIEF,
        project_id="project-a",
        origin=TaskOrigin.TRIGGER,
        created_by="trigger:github_label",
        authority_floor=Authority(merge=GateMode.REQUIRE),
        now=NOW,
    )
    assert task.origin is TaskOrigin.TRIGGER
    assert task.routing.prefer_free_implementation is False
    assert task.authority.merge is GateMode.REQUIRE


@pytest.mark.asyncio
async def test_a_non_human_origin_cannot_merge_automatically_without_a_floor(
    service: TaskService,
) -> None:
    """Section 19: the default merge gate resolves to ALLOW, so the service must tighten it."""
    task, _record = await service.create(
        SOFTWARE_BRIEF,
        project_id="project-a",
        origin=TaskOrigin.TRIGGER,
        created_by="trigger:github_label",
        now=NOW,
    )
    assert task.authority.merge is GateMode.REQUIRE
    assert task.authority.plan is GateMode.REQUIRE
    assert task.authority.deliver is GateMode.REQUIRE
    human, _record = await service.create(
        SOFTWARE_BRIEF, project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    assert human.authority.merge is GateMode.BY_REVERSIBILITY


@pytest.mark.asyncio
async def test_create_without_a_matching_target_is_refused(store: TaskStore, tmp_path) -> None:
    from sagewai.artifacts.object_store import LocalArtifactStore

    await store.put_defaults(TaskDefaults(project_id="project-b"), expected_revision=0)
    service = TaskService(store=store, artifact_store=LocalArtifactStore(root=tmp_path / "objects"))
    with pytest.raises(TaskCreationError):
        await service.create(
            SOFTWARE_BRIEF, project_id="project-b", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
        )


@pytest.mark.asyncio
async def test_create_with_an_explicit_target_that_mismatches_the_template_is_refused(
    service: TaskService,
) -> None:
    with pytest.raises(TaskCreationError):
        await service.create(
            SOFTWARE_BRIEF,
            project_id="project-a",
            origin=TaskOrigin.HUMAN,
            created_by="arda",
            target=ReportTarget(required_sections=("Summary",)),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_create_stores_execution_origin_ref_and_source_ref(service: TaskService, store: TaskStore) -> None:
    execution = ExecutionRoute(route="fleet", fleet_org_id="org-1")
    task, _record = await service.create(
        SOFTWARE_BRIEF,
        project_id="project-a",
        origin=TaskOrigin.HUMAN,
        created_by="arda",
        execution=execution,
        origin_ref="chat://thread-1",
        source_ref="github://issue/1",
        now=NOW,
    )
    assert task.execution == execution
    assert task.origin_ref == "chat://thread-1"
    assert task.source_ref == "github://issue/1"
    loaded = await store.load(task.id, project_id="project-a")
    assert loaded is not None
    assert loaded[0].execution == execution
    assert loaded[0].origin_ref == "chat://thread-1"
    assert loaded[0].source_ref == "github://issue/1"


@pytest.mark.asyncio
async def test_scheduled_brief_without_a_cadence_inherits_the_template_default_cron(
    service: TaskService,
) -> None:
    from sagewai.work.tasks.templates import get_template

    template = get_template("scheduled_research_report")
    task, _record = await service.create(
        "Research stuff about my competitors",
        project_id="project-a",
        origin=TaskOrigin.HUMAN,
        created_by="arda",
        target=ReportTarget(required_sections=("Summary",)),
        now=NOW,
    )
    assert task.schedule is not None
    assert task.schedule.cron == template.default_cron


@pytest.mark.asyncio
async def test_answering_the_last_question_returns_to_planning(service: TaskService, store: TaskStore) -> None:
    task, record = await service.create(
        "tidy up", project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    events = await store.read_events(task.id, project_id="project-a")
    questions = events[3].payload_json["questions"]
    assert len(questions) == record.pending_questions
    for question in questions:
        record = await service.answer_clarification(
            task.id,
            project_id="project-a",
            question_id=question["id"],
            attention_version=1,
            answer="the payments service",
            actor_ref="arda",
            now=NOW,
        )
    assert record.status is TaskStatus.PLANNING
    assert record.pending_questions == 0


@pytest.mark.asyncio
async def test_answering_questions_uses_the_open_set_and_materiality(
    service: TaskService, store: TaskStore
) -> None:
    task, record = await service.create(
        "tidy up", project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    events = await store.read_events(task.id, project_id="project-a")
    defaultable_id = events[3].payload_json["questions"][0]["id"]
    record = await TaskWriter(store).append(
        record,
        [
            (
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": [
                        {
                            "id": "hard",
                            "text": "Which repository?",
                            "kind": "text",
                            "options": [],
                            "default": None,
                            "defaultable": False,
                            "rationale": "",
                            "attention_version": 1,
                        }
                    ],
                    "deadline_at": NOW.isoformat(),
                },
            )
        ],
        now=NOW,
    )
    record = await service.answer_clarification(
        task.id,
        project_id="project-a",
        question_id=defaultable_id,
        attention_version=1,
        answer="ship the retry queue",
        actor_ref="arda",
        now=NOW,
    )
    assert record.status is TaskStatus.CLARIFYING
    assert record.attention_owner is AttentionOwner.USER
    with pytest.raises(TaskDecisionError):
        await service.answer_clarification(
            task.id,
            project_id="project-a",
            question_id=defaultable_id,
            attention_version=1,
            answer="again",
            actor_ref="arda",
            now=NOW,
        )
    loaded = await store.load_record(task.id, project_id="project-a")
    assert loaded is not None
    assert loaded.status is TaskStatus.CLARIFYING
    record = await service.answer_clarification(
        task.id,
        project_id="project-a",
        question_id="hard",
        attention_version=1,
        answer="/tmp/repo",
        actor_ref="arda",
        now=NOW,
    )
    assert record.status is TaskStatus.PLANNING
    assert record.pending_questions == 0


@pytest.mark.asyncio
async def test_an_expired_defaultable_question_defaults_and_returns_to_planning(
    service: TaskService, store: TaskStore
) -> None:
    task, record = await service.create(
        "tidy up", project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    assert record.status is TaskStatus.CLARIFYING
    early = await service.default_expired_clarifications(
        task.id, project_id="project-a", now=NOW + timedelta(hours=1)
    )
    assert early.status is TaskStatus.CLARIFYING
    late = await service.default_expired_clarifications(
        task.id, project_id="project-a", now=NOW + timedelta(hours=5)
    )
    assert late.status is TaskStatus.PLANNING
    assert late.pending_questions == 0
    types = [event.event_type for event in await store.read_events(task.id, project_id="project-a")]
    assert types.count(TaskEventType.CLARIFICATION_DEFAULTED) == record.pending_questions


@pytest.mark.asyncio
async def test_deadline_sweep_defaults_only_expired_questions(
    service: TaskService, store: TaskStore
) -> None:
    expired, expired_record = await service.create(
        "tidy up", project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    future, _future_record = await service.create(
        "tidy up",
        project_id="project-a",
        origin=TaskOrigin.HUMAN,
        created_by="arda",
        now=NOW + timedelta(hours=2),
    )
    defaulted = await ClarificationDeadlines(store=store, service=service).run(
        project_id="project-a", now=NOW + timedelta(hours=5)
    )
    assert defaulted == expired_record.pending_questions
    expired_loaded = await store.load_record(expired.id, project_id="project-a")
    future_loaded = await store.load_record(future.id, project_id="project-a")
    assert expired_loaded is not None and expired_loaded.status is TaskStatus.PLANNING
    assert future_loaded is not None and future_loaded.status is TaskStatus.CLARIFYING
    future_events = await store.read_events(future.id, project_id="project-a")
    assert TaskEventType.CLARIFICATION_DEFAULTED not in [event.event_type for event in future_events]


@pytest.mark.asyncio
async def test_deadline_sweep_continues_after_a_stale_task(
    service: TaskService, store: TaskStore, monkeypatch
) -> None:
    import sagewai.work.tasks.service as service_module

    stale, _stale_record = await service.create(
        "tidy up", project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    good, good_record = await service.create(
        "tidy up", project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    original_append = service_module.TaskWriter.append

    async def stale_once(self, record, entries, **kwargs):
        if record.task_id == stale.id:
            raise StaleTaskError("lost the append race")
        return await original_append(self, record, entries, **kwargs)

    monkeypatch.setattr(service_module.TaskWriter, "append", stale_once)
    defaulted = await ClarificationDeadlines(store=store, service=service).run(
        project_id="project-a", now=NOW + timedelta(hours=5)
    )
    assert defaulted == good_record.pending_questions
    stale_loaded = await store.load_record(stale.id, project_id="project-a")
    good_loaded = await store.load_record(good.id, project_id="project-a")
    assert stale_loaded is not None and stale_loaded.status is TaskStatus.CLARIFYING
    assert good_loaded is not None and good_loaded.status is TaskStatus.PLANNING


@pytest.mark.asyncio
async def test_a_non_defaultable_question_is_never_defaulted(
    service: TaskService, store: TaskStore
) -> None:
    task, record = await service.create(
        "tidy up", project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    record = await TaskWriter(store).append(
        record,
        [
            (
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": [
                        {
                            "id": "hard",
                            "text": "Which repository?",
                            "kind": "text",
                            "options": [],
                            "default": None,
                            "defaultable": False,
                            "rationale": "",
                            "attention_version": 1,
                        }
                    ],
                    "deadline_at": NOW.isoformat(),
                },
            )
        ],
        now=NOW,
    )
    after = await service.default_expired_clarifications(
        task.id, project_id="project-a", now=NOW + timedelta(hours=9)
    )
    assert after.status is TaskStatus.CLARIFYING
    assert after.pending_material_questions == 1
    assert after.attention_owner is AttentionOwner.USER


@pytest.mark.asyncio
async def test_a_question_attached_to_a_plan_defaults_without_returning_to_planning(
    service: TaskService, store: TaskStore
) -> None:
    task, record = await service.create(
        "tidy up", project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    record = await service.default_expired_clarifications(
        task.id, project_id="project-a", now=NOW + timedelta(hours=5)
    )
    assert record.status is TaskStatus.PLANNING and record.pending_questions == 0
    record = await TaskWriter(store).append(
        record,
        [
            (
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": [
                        {
                            "id": "difficulty",
                            "text": "Which difficulty axis?",
                            "kind": "text",
                            "options": [],
                            "default": "the plan above",
                            "defaultable": True,
                            "rationale": "",
                            "attention_version": 1,
                        }
                    ],
                    "deadline_at": (NOW + timedelta(hours=9)).isoformat(),
                },
            ),
            status_entry(record, TaskStatus.PLAN_PROPOSED),
        ],
        now=NOW + timedelta(hours=5),
    )
    assert record.status is TaskStatus.PLAN_PROPOSED and record.pending_questions == 1

    defaulted = await ClarificationDeadlines(store=store, service=service).run(
        project_id="project-a", now=NOW + timedelta(hours=10)
    )

    assert defaulted == 1
    after = await store.load_record(task.id, project_id="project-a")
    assert after is not None
    assert after.status is TaskStatus.PLAN_PROPOSED and after.pending_questions == 0
    defaults = [
        event.payload_json
        for event in await store.read_events(task.id, project_id="project-a")
        if event.event_type is TaskEventType.CLARIFICATION_DEFAULTED
        and event.payload_json["question_id"] == "difficulty"
    ]
    assert defaults == [{"question_id": "difficulty", "answer": "the plan above"}]


@pytest.mark.asyncio
async def test_add_message_appends_one_human_message(service_and_record) -> None:
    service, record = service_and_record

    updated = await service.add_message(
        record.task_id, project_id=record.project_id, text="use the redis queue", actor_ref="arda"
    )

    assert updated.last_event_sequence == record.last_event_sequence + 1
    events = await service._store.read_events(record.task_id, project_id=record.project_id)
    assert events[-1].event_type is TaskEventType.TASK_MESSAGE
    assert events[-1].payload_json == {"author": "human", "text": "use the redis queue", "refs": []}
    assert events[-1].actor_ref == "arda"


@pytest.mark.asyncio
async def test_a_keyed_message_is_written_once(service_and_record) -> None:
    service, record = service_and_record

    first = await service.add_message(
        record.task_id,
        project_id=record.project_id,
        text="use the redis queue",
        actor_ref="arda",
        idempotency_key="k1",
    )
    again = await service.add_message(
        record.task_id,
        project_id=record.project_id,
        text="use the redis queue",
        actor_ref="arda",
        idempotency_key="k1",
    )

    assert again.revision == first.revision
    events = await service._store.read_events(record.task_id, project_id=record.project_id)
    assert sum(1 for event in events if event.event_type is TaskEventType.TASK_MESSAGE) == 1


@pytest.mark.asyncio
async def test_a_keyed_message_that_loses_the_append_keeps_its_key_usable_after_any_failure(
    service_and_record, monkeypatch
) -> None:
    """A spent receipt would turn the client's retry into a silent no-op."""
    service, record = service_and_record
    real_append = service._store.append

    async def _fail(*_args, **_kwargs):
        raise RuntimeError("append crashed after recording the receipt")

    monkeypatch.setattr(service._store, "append", _fail)
    with pytest.raises(RuntimeError, match="append crashed"):
        await service.add_message(
            record.task_id,
            project_id=record.project_id,
            text="use the redis queue",
            actor_ref="arda",
            idempotency_key="k1",
        )
    monkeypatch.setattr(service._store, "append", real_append)

    retried = await service.add_message(
        record.task_id,
        project_id=record.project_id,
        text="use the redis queue",
        actor_ref="arda",
        idempotency_key="k1",
    )

    assert retried.last_event_sequence == record.last_event_sequence + 1
    events = await service._store.read_events(record.task_id, project_id=record.project_id)
    assert sum(1 for event in events if event.event_type is TaskEventType.TASK_MESSAGE) == 1


@pytest.mark.asyncio
async def test_an_unkeyed_message_repeats(service_and_record) -> None:
    service, record = service_and_record

    await service.add_message(
        record.task_id, project_id=record.project_id, text="again", actor_ref="arda"
    )
    await service.add_message(
        record.task_id, project_id=record.project_id, text="again", actor_ref="arda"
    )

    events = await service._store.read_events(record.task_id, project_id=record.project_id)
    assert sum(1 for event in events if event.event_type is TaskEventType.TASK_MESSAGE) == 2


@pytest.mark.asyncio
async def test_answering_with_a_stale_attention_version_is_refused(clarifying) -> None:
    service, record, question_id = clarifying

    with pytest.raises(TaskDecisionError, match="attention version"):
        await service.answer_clarification(
            record.task_id,
            project_id=record.project_id,
            question_id=question_id,
            attention_version=2,
            answer="redis",
            actor_ref="arda",
        )
    unchanged = await service._store.load_record(record.task_id, project_id=record.project_id)
    assert unchanged.pending_questions == record.pending_questions


@pytest.mark.asyncio
async def test_answering_with_the_current_attention_version_records_the_answer(clarifying) -> None:
    service, record, question_id = clarifying

    updated = await service.answer_clarification(
        record.task_id,
        project_id=record.project_id,
        question_id=question_id,
        attention_version=1,
        answer="redis",
        actor_ref="arda",
    )

    assert updated.pending_questions == record.pending_questions - 1


@pytest.mark.asyncio
async def test_defaulting_with_the_current_attention_version_records_the_default(
    clarifying,
) -> None:
    service, record, question_id = clarifying

    updated = await service.answer_clarification(
        record.task_id,
        project_id=record.project_id,
        question_id=question_id,
        attention_version=1,
        answer=None,
        actor_ref="arda",
    )

    assert updated.pending_questions == record.pending_questions - 1
    events = await service._store.read_events(record.task_id, project_id=record.project_id)
    assert events[-2].event_type is TaskEventType.CLARIFICATION_DEFAULTED


@pytest.mark.asyncio
async def test_defaulting_a_non_defaultable_question_is_refused(
    service: TaskService, store: TaskStore
) -> None:
    task, record = await service.create(
        "tidy up", project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    record = await TaskWriter(store).append(
        record,
        [
            (
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": [
                        {
                            "id": "hard",
                            "text": "Which repository?",
                            "kind": "text",
                            "options": [],
                            "default": "no",
                            "defaultable": False,
                            "rationale": "",
                            "attention_version": 1,
                        }
                    ],
                    "deadline_at": NOW.isoformat(),
                },
            )
        ],
        now=NOW,
    )

    with pytest.raises(TaskDecisionError, match="not defaultable"):
        await service.answer_clarification(
            task.id,
            project_id="project-a",
            question_id="hard",
            attention_version=1,
            answer=None,
            actor_ref="arda",
        )
    unchanged = await store.load_record(task.id, project_id="project-a")
    assert unchanged == record


@pytest.mark.asyncio
async def test_defaulting_a_question_without_a_default_is_refused(
    service: TaskService, store: TaskStore
) -> None:
    task, record = await service.create(
        "tidy up", project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    record = await TaskWriter(store).append(
        record,
        [
            (
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": [
                        {
                            "id": "hard",
                            "text": "Which repository?",
                            "kind": "text",
                            "options": [],
                            "default": None,
                            "defaultable": True,
                            "rationale": "",
                            "attention_version": 1,
                        }
                    ],
                    "deadline_at": NOW.isoformat(),
                },
            )
        ],
        now=NOW,
    )

    with pytest.raises(TaskDecisionError, match="no default"):
        await service.answer_clarification(
            task.id,
            project_id="project-a",
            question_id="hard",
            attention_version=1,
            answer=None,
            actor_ref="arda",
        )
    unchanged = await store.load_record(task.id, project_id="project-a")
    assert unchanged == record


STEP = {
    "id": "s1",
    "title": "Add the retry queue",
    "goal": "Add the retry queue",
    "allowed_scope": ["src"],
    "acceptance_criteria": [{"statement": "the suite passes", "verification_kind": "deterministic"}],
    "constraints": [],
    "non_goals": [],
    "risk": "low",
    "design_required": False,
    "depends_on": [],
    "domain": "backend",
    "size": "s",
}
MATRIX = [
    {"id": "m1", "statement": "just smoke passes", "verification_kind": "deterministic", "command": "just smoke"}
]


async def _proposed(service: TaskService, store: TaskStore, gate_id: str | None = None):
    """A Task with a proposed plan and, unless told otherwise, its plan gate open."""
    task, record = await service.create(
        SOFTWARE_BRIEF, project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    record = await TaskWriter(store).append(
        record,
        [
            (TaskEventType.PLAN_PROPOSED, {"version": 1, "steps": [STEP], "acceptance_matrix": MATRIX}),
            (
                TaskEventType.GATE_REQUESTED,
                {"gate_id": gate_id or f"plan:{task.id}:1", "question": "Approve the plan."},
            ),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.PLAN_PROPOSED.value}),
        ],
        now=NOW,
    )
    return task, record


@pytest.mark.asyncio
async def test_accept_plan_decides_the_gate_and_starts_executing(
    service: TaskService, store: TaskStore
) -> None:
    task, record = await _proposed(service, store)
    assert record.pending_gate == f"plan:{task.id}:1"
    record = await service.accept_plan(task.id, project_id="project-a", version=1, actor_ref="arda", now=NOW)
    assert record.status is TaskStatus.EXECUTING
    assert record.plan_version == 1
    assert record.pending_gate is None


@pytest.mark.asyncio
async def test_accept_plan_refuses_mirrored_work_gates_and_writes_nothing(
    service: TaskService, store: TaskStore
) -> None:
    task, _record = await _proposed(service, store, gate_id="merge:w1:7")
    before = await store.read_events(task.id, project_id="project-a")
    with pytest.raises(TaskDecisionError, match="sagewai work approve"):
        await service.accept_plan(task.id, project_id="project-a", version=1, actor_ref="arda", now=NOW)
    after = await store.read_events(task.id, project_id="project-a")
    assert [event.event_type for event in after] == [event.event_type for event in before]


@pytest.mark.asyncio
async def test_accept_plan_refuses_a_rollback_gate_and_writes_nothing(
    service: TaskService, store: TaskStore
) -> None:
    task, record = await _proposed(service, store, gate_id="rollback:w-s1-1")
    before = await store.read_events(task.id, project_id="project-a")
    with pytest.raises(TaskDecisionError, match="not the plan gate"):
        await service.accept_plan(task.id, project_id="project-a", version=1, actor_ref="arda", now=NOW)
    after = await store.read_events(task.id, project_id="project-a")
    assert [event.event_type for event in after] == [event.event_type for event in before]
    assert record.pending_gate == "rollback:w-s1-1"


@pytest.mark.asyncio
async def test_accept_plan_refuses_a_never_proposed_version_and_writes_nothing(
    service: TaskService, store: TaskStore
) -> None:
    task, _record = await _proposed(service, store)
    before = await store.read_events(task.id, project_id="project-a")
    with pytest.raises(TaskDecisionError):
        await service.accept_plan(task.id, project_id="project-a", version=2, actor_ref="arda", now=NOW)
    after = await store.read_events(task.id, project_id="project-a")
    assert [event.event_type for event in after] == [event.event_type for event in before]


@pytest.mark.asyncio
async def test_accept_plan_is_idempotent_for_an_already_accepted_version(
    service: TaskService, store: TaskStore
) -> None:
    task, _record = await _proposed(service, store)
    first = await service.accept_plan(task.id, project_id="project-a", version=1, actor_ref="arda", now=NOW)
    second = await service.accept_plan(task.id, project_id="project-a", version=1, actor_ref="arda", now=NOW)
    assert second == first
    events = await store.read_events(task.id, project_id="project-a")
    assert [event.event_type for event in events].count(TaskEventType.PLAN_ACCEPTED) == 1


@pytest.mark.asyncio
async def test_denying_the_plan_gate_blocks_the_task(service: TaskService, store: TaskStore) -> None:
    task, record = await _proposed(service, store)
    record = await service.decide_gate(
        task.id,
        project_id="project-a",
        gate_id=f"plan:{task.id}:1",
        decision="deny",
        actor_ref="arda",
        now=NOW,
    )
    assert record.status is TaskStatus.BLOCKED
    assert record.pending_gate is None
    assert record.attention_owner is AttentionOwner.USER


@pytest.mark.asyncio
async def test_budget_raise_revives_an_exhausted_executing_task(
    service_and_record,
) -> None:
    service, record = service_and_record
    running = await TaskWriter(service._store).append(
        record, [status_entry(record, TaskStatus.EXECUTING)], now=NOW
    )
    exhausted = await TaskWriter(service._store).append(
        running, [status_entry(running, TaskStatus.BUDGET_EXHAUSTED)], now=NOW
    )

    _task, revived = await service.update_budget(
        exhausted.task_id,
        project_id=exhausted.project_id,
        budget=Budget(max_cycle_usd="25.00"),
        expected_revision=exhausted.revision,
        actor_ref="arda",
        now=NOW,
    )

    assert revived.status is TaskStatus.EXECUTING
    assert revived.attention_owner is AttentionOwner.SYSTEM
    assert revived.waiting_reason == "working"
    assert revived.board_column is BoardColumn.IN_PROGRESS


@pytest.mark.asyncio
async def test_budget_raise_revives_a_paused_exhausted_task_to_its_budgeted_status(
    service_and_record,
) -> None:
    service, record = service_and_record
    running = await TaskWriter(service._store).append(
        record, [status_entry(record, TaskStatus.EXECUTING)], now=NOW
    )
    exhausted = await TaskWriter(service._store).append(
        running, [status_entry(running, TaskStatus.BUDGET_EXHAUSTED)], now=NOW
    )
    paused = await service.pause(
        exhausted.task_id,
        project_id=exhausted.project_id,
        actor_ref="arda",
        now=NOW,
    )
    resumed = await service.resume(
        paused.task_id,
        project_id=paused.project_id,
        actor_ref="arda",
        now=NOW,
    )

    _task, revived = await service.update_budget(
        resumed.task_id,
        project_id=resumed.project_id,
        budget=Budget(max_cycle_usd="25.00"),
        expected_revision=resumed.revision,
        actor_ref="arda",
        now=NOW,
    )

    assert resumed.status is TaskStatus.BUDGET_EXHAUSTED
    assert revived.status is TaskStatus.EXECUTING
    assert revived.attention_owner is AttentionOwner.SYSTEM
    assert revived.waiting_reason == "working"
    assert revived.board_column is BoardColumn.IN_PROGRESS


@pytest.mark.asyncio
async def test_budget_raise_revives_an_exhausted_assessing_task_as_executing(
    service_and_record,
) -> None:
    service, record = service_and_record
    running = await TaskWriter(service._store).append(
        record, [status_entry(record, TaskStatus.EXECUTING)], now=NOW
    )
    assessing = await TaskWriter(service._store).append(
        running, [status_entry(running, TaskStatus.ASSESSING)], now=NOW
    )
    exhausted = await TaskWriter(service._store).append(
        assessing, [status_entry(assessing, TaskStatus.BUDGET_EXHAUSTED)], now=NOW
    )

    _task, revived = await service.update_budget(
        exhausted.task_id,
        project_id=exhausted.project_id,
        budget=Budget(max_cycle_usd="25.00"),
        expected_revision=exhausted.revision,
        actor_ref="arda",
        now=NOW,
    )

    assert revived.status is TaskStatus.EXECUTING
    assert revived.attention_owner is AttentionOwner.SYSTEM
    assert revived.waiting_reason == "working"
    assert revived.board_column is BoardColumn.IN_PROGRESS


@pytest.mark.asyncio
async def test_budget_raise_uses_the_last_budgeted_status_before_the_final_exhaustion(
    service_and_record,
) -> None:
    service, record = service_and_record
    running = await TaskWriter(service._store).append(
        record, [status_entry(record, TaskStatus.EXECUTING)], now=NOW
    )
    exhausted = await TaskWriter(service._store).append(
        running, [status_entry(running, TaskStatus.BUDGET_EXHAUSTED)], now=NOW
    )
    _task, revived = await service.update_budget(
        exhausted.task_id,
        project_id=exhausted.project_id,
        budget=Budget(max_cycle_usd="25.00"),
        expected_revision=exhausted.revision,
        actor_ref="arda",
        now=NOW,
    )
    assessing = await TaskWriter(service._store).append(
        revived, [status_entry(revived, TaskStatus.ASSESSING)], now=NOW
    )
    exhausted_again = await TaskWriter(service._store).append(
        assessing, [status_entry(assessing, TaskStatus.BUDGET_EXHAUSTED)], now=NOW
    )

    _task, revived_again = await service.update_budget(
        exhausted_again.task_id,
        project_id=exhausted_again.project_id,
        budget=Budget(max_cycle_usd="35.00"),
        expected_revision=exhausted_again.revision,
        actor_ref="arda",
        now=NOW,
    )

    assert revived_again.status is TaskStatus.EXECUTING
    assert revived_again.attention_owner is AttentionOwner.SYSTEM
    assert revived_again.waiting_reason == "working"
    assert revived_again.board_column is BoardColumn.IN_PROGRESS


@pytest.mark.asyncio
async def test_budget_raise_revives_an_exhausted_planning_task(
    service_and_record,
) -> None:
    service, record = service_and_record
    exhausted = await TaskWriter(service._store).append(
        record, [status_entry(record, TaskStatus.BUDGET_EXHAUSTED)], now=NOW
    )

    _task, revived = await service.update_budget(
        exhausted.task_id,
        project_id=exhausted.project_id,
        budget=Budget(max_cycle_usd="25.00"),
        expected_revision=exhausted.revision,
        actor_ref="arda",
        now=NOW,
    )

    assert revived.status is TaskStatus.PLANNING
    assert revived.attention_owner is AttentionOwner.SYSTEM
    assert revived.waiting_reason == "working"
    assert revived.board_column is BoardColumn.INBOX


@pytest.mark.asyncio
async def test_budget_update_keeps_a_non_exhausted_status(service_and_record) -> None:
    service, record = service_and_record

    _task, updated = await service.update_budget(
        record.task_id,
        project_id=record.project_id,
        budget=Budget(max_cycle_usd="25.00"),
        expected_revision=record.revision,
        actor_ref="arda",
        now=NOW,
    )

    assert updated.status is TaskStatus.PLANNING


async def _replan_gate(service: TaskService, store: TaskStore):
    task, record = await _proposed(service, store)
    record = await service.accept_plan(
        task.id, project_id="project-a", version=1, actor_ref="arda", now=NOW
    )
    gate_id = f"replan:{task.id}:2"
    record = await TaskWriter(store).append(
        record,
        [
            (TaskEventType.CYCLE_STARTED, {"cycle": 1, "scheduled_for": None}),
            status_entry(record, TaskStatus.ASSESSING),
            (
                TaskEventType.ASSESSMENT_RECORDED,
                {
                    "cycle": 1,
                    "attempt_id": "assess",
                    "matrix_results": [],
                    "gaps": [
                        {
                            "statement": "deterministic check failed",
                            "severity": "high",
                            "suggested_step": "repair-step",
                        }
                    ],
                    "verdict": "replan",
                },
            ),
            (
                TaskEventType.REPLAN_PROPOSED,
                {"version": 2, "reason": "assessment requested a re-plan"},
            ),
            (
                TaskEventType.GATE_REQUESTED,
                {"gate_id": gate_id, "question": "Approve the re-plan.", "action": {}},
            ),
        ],
        now=NOW,
    )
    return task, record, gate_id


@pytest.mark.asyncio
async def test_allowing_a_replan_gate_returns_the_task_to_planning(
    service: TaskService, store: TaskStore
) -> None:
    task, record, gate_id = await _replan_gate(service, store)
    assert record.status is TaskStatus.ASSESSING
    record = await service.decide_gate(
        task.id,
        project_id="project-a",
        gate_id=gate_id,
        decision="allow",
        actor_ref="arda",
        now=NOW,
    )
    assert record.status is TaskStatus.PLANNING
    assert record.pending_gate is None


@pytest.mark.asyncio
async def test_denying_a_replan_gate_blocks_the_task_for_the_user(
    service: TaskService, store: TaskStore
) -> None:
    task, _record, gate_id = await _replan_gate(service, store)
    record = await service.decide_gate(
        task.id,
        project_id="project-a",
        gate_id=gate_id,
        decision="deny",
        actor_ref="arda",
        now=NOW,
    )
    assert record.status is TaskStatus.BLOCKED
    assert record.pending_gate is None
    assert record.attention_owner is AttentionOwner.USER


@pytest.mark.asyncio
async def test_a_work_gate_mirrored_onto_the_task_is_not_decidable_here(
    service: TaskService, store: TaskStore
) -> None:
    """The real merge gate lives on the Work; clearing only the Task's copy would hide it."""
    task, _record = await _proposed(service, store, gate_id="merge:w1:7")
    with pytest.raises(TaskDecisionError):
        await service.decide_gate(
            task.id,
            project_id="project-a",
            gate_id="merge:w1:7",
            decision="allow",
            actor_ref="arda",
            now=NOW,
        )


def test_action_not_found_is_a_task_not_found_error() -> None:
    assert issubclass(ActionNotFoundError, TaskNotFoundError)


@pytest.mark.asyncio
async def test_request_rollback_raises_action_not_found_for_missing_action(
    service: TaskService, store: TaskStore
) -> None:
    task, _record = await service.create(
        SOFTWARE_BRIEF, project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )

    with pytest.raises(ActionNotFoundError, match="no recorded action deliver:w1:2"):
        await service.request_rollback(
            task.id,
            project_id="project-a",
            action_id="deliver:w1:2",
            actor_ref="arda",
            now=NOW,
        )


@pytest.mark.asyncio
async def test_deciding_a_gate_that_is_not_open_is_refused(
    service: TaskService, store: TaskStore
) -> None:
    task, _record = await _proposed(service, store)
    with pytest.raises(TaskDecisionError):
        await service.decide_gate(
            task.id,
            project_id="project-a",
            gate_id=f"plan:{task.id}:2",
            decision="allow",
            actor_ref="arda",
            now=NOW,
        )


@pytest.mark.asyncio
async def test_two_stale_writers_are_fenced_by_the_task_stream(
    service: TaskService, store: TaskStore
) -> None:
    task, record = await service.create(
        SOFTWARE_BRIEF, project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    writer_a = TaskWriter(store)
    writer_b = TaskWriter(store)
    won = await writer_a.append(
        record,
        [(TaskEventType.TASK_MESSAGE, {"author": "system", "text": "winner", "refs": []})],
        now=NOW,
    )
    assert won.last_event_sequence == record.last_event_sequence + 1
    with pytest.raises(StaleTaskError):
        await writer_b.append(
            record,
            [(TaskEventType.TASK_MESSAGE, {"author": "system", "text": "loser", "refs": []})],
            now=NOW,
        )
    messages = [
        event.payload_json["text"]
        for event in await store.read_events(task.id, project_id="project-a")
        if event.event_type is TaskEventType.TASK_MESSAGE
    ]
    assert messages == ["winner"]


@pytest.mark.asyncio
async def test_build_events_refuses_raw_illegal_status_entries(
    service: TaskService, store: TaskStore
) -> None:
    task, record = await service.create(
        SOFTWARE_BRIEF, project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    before = await store.read_events(task.id, project_id="project-a")
    with pytest.raises(IllegalTransitionError):
        await TaskWriter(store).append(
            record,
            [(TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.COMPLETE.value})],
            now=NOW,
        )
    after = await store.read_events(task.id, project_id="project-a")
    assert [event.event_type for event in after] == [event.event_type for event in before]
    legal = await TaskWriter(store).append(record, [status_entry(record, TaskStatus.PLAN_PROPOSED)], now=NOW)
    assert legal.status is TaskStatus.PLAN_PROPOSED


@pytest.mark.asyncio
async def test_plan_from_events_reads_the_latest_matching_version(
    service: TaskService, store: TaskStore
) -> None:
    task, record = await service.create(
        SOFTWARE_BRIEF, project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )
    record = await TaskWriter(store).append(
        record,
        [
            (
                TaskEventType.PLAN_PROPOSED,
                {
                    "version": 1,
                    "steps": [dict(STEP, title="First proposal")],
                    "acceptance_matrix": MATRIX,
                },
            )
        ],
        now=NOW,
    )
    await TaskWriter(store).append(
        record,
        [
            (
                TaskEventType.PLAN_PROPOSED,
                {
                    "version": 1,
                    "steps": [dict(STEP, title="Latest proposal")],
                    "acceptance_matrix": MATRIX,
                },
            ),
            (
                TaskEventType.PLAN_PROPOSED,
                {
                    "version": 2,
                    "steps": [dict(STEP, id="s2", title="Second version")],
                    "acceptance_matrix": MATRIX,
                },
            ),
        ],
        now=NOW,
    )
    events = await store.read_events(task.id, project_id="project-a")
    assert plan_from_events(events, version=1).steps[0].title == "Latest proposal"
    assert plan_from_events(events, version=2).steps[0].id == "s2"
    assert plan_from_events(events, version=3) is None


def test_decide_gate_accepts_only_allow_or_deny_by_type() -> None:
    hint = get_type_hints(TaskService.decide_gate)["decision"]
    assert get_origin(hint) is Literal
    assert set(get_args(hint)) == {"allow", "deny"}


@pytest.mark.asyncio
async def test_create_lets_the_project_override_the_planner_ladder(
    service: TaskService, store: TaskStore
) -> None:
    defaults = await store.get_defaults(project_id="project-a")
    await store.put_defaults(
        defaults.model_copy(
            update={
                "routing": RoutingPolicy(
                    roles={RoleAlias.PLANNER: (RuntimeRef.CODEX,)},
                    prefer_free_implementation=True,
                )
            }
        ),
        expected_revision=defaults.revision,
    )

    task, _record = await service.create(
        SOFTWARE_BRIEF, project_id="project-a", origin=TaskOrigin.HUMAN, created_by="arda", now=NOW
    )

    assert task.routing.roles[RoleAlias.PLANNER] == (RuntimeRef.CODEX,)
    assert task.routing.roles[RoleAlias.REVIEWER] == (RuntimeRef.CLAUDE_REVIEW,)
