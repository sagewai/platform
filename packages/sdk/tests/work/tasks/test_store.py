# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Dual-dialect tests for the Task store."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from sagewai.artifacts.models import ArtifactRef
from sagewai.work.tasks.events import TaskEvent, TaskEventType, fold_record
from sagewai.work.tasks.feed import FeedBus, FeedEntry
from sagewai.work.tasks.models import (
    Authority,
    ExecutionRoute,
    SoftwareTarget,
    Task,
    TaskDefaults,
    TaskKind,
    TaskOrigin,
    TaskRecord,
    TaskStatus,
)
from sagewai.work.tasks.store import SpendReservation, StaleTaskError, TaskStore
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store(dialect_engine) -> TaskStore:  # noqa: F811
    result = TaskStore(engine=dialect_engine, feed_bus=FeedBus())
    await result.init()
    return result


def _task(task_id: str = "task-1", project_id: str = "project-a") -> Task:
    return Task(
        id=task_id,
        project_id=project_id,
        kind=TaskKind.BATCH,
        origin=TaskOrigin.HUMAN,
        title="Build the thing",
        brief_ref=ArtifactRef(
            project_id=project_id,
            digest="sha256:" + "a" * 64,
            media_type="text/markdown",
            size_bytes=12,
            storage_ref="artifact://sha256:" + "a" * 64,
            created_at=NOW,
            created_by="test",
        ),
        brief_summary="Build the thing",
        template_id="software_delivery",
        template_version="1",
        profile="software",
        target=SoftwareTarget(
            repository_path="/tmp/repo", owner="o", repo="r", verification_image="sha256:" + "b" * 64
        ),
        authority=Authority.for_kind(TaskKind.BATCH),
        execution=ExecutionRoute(route="local"),
        created_by="arda",
        created_at=NOW,
    )


def _record(task: Task) -> TaskRecord:
    return TaskRecord(
        task_id=task.id,
        project_id=task.project_id,
        kind=task.kind,
        origin=task.origin,
        title=task.title,
        profile=task.profile,
        status=TaskStatus.PLANNING,
        last_event_sequence=0,
        created_at=NOW,
        updated_at=NOW,
    )


def _event(task: Task, sequence: int, event_type: TaskEventType, payload: dict | None = None) -> TaskEvent:
    return TaskEvent(
        id=f"{task.id}-event-{sequence}",
        project_id=task.project_id,
        task_id=task.id,
        sequence=sequence,
        event_type=event_type,
        actor_type="system",
        actor_ref="test",
        payload_json=payload or {},
        created_at=NOW,
    )


async def _create(store: TaskStore, task: Task) -> TaskRecord:
    record = _record(task)
    events = (_event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),)
    return await store.create(task, events=events, record=record)


@pytest.mark.asyncio
async def test_create_persists_definition_projection_events_and_feed(store: TaskStore) -> None:
    task = _task()
    record = await _create(store, task)
    assert record.revision == 1
    assert record.last_event_sequence == 1
    loaded = await store.load(task.id, project_id=task.project_id)
    assert loaded is not None
    assert loaded[0] == task
    assert loaded[1].status is TaskStatus.PLANNING
    assert loaded[1].last_event_sequence == 1
    events = await store.read_events(task.id, project_id=task.project_id)
    assert [event.sequence for event in events] == [1]
    feed = await store.read_feed(task.id, project_id=task.project_id)
    assert [(entry.feed_sequence, entry.source, entry.event_type) for entry in feed] == [
        (1, "task_event", "TASK_CREATED")
    ]
    assert await store.load(task.id, project_id="project-b") is None


@pytest.mark.asyncio
async def test_create_rejects_duplicate_task(store: TaskStore) -> None:
    task = _task()
    await _create(store, task)
    with pytest.raises(ValueError):
        await _create(store, task)


@pytest.mark.asyncio
async def test_append_requires_expected_sequence_and_updates_projection(store: TaskStore) -> None:
    task = _task()
    record = await _create(store, task)
    events = (_event(task, 2, TaskEventType.TASK_STATUS_CHANGED, {"status": "CLARIFYING"}),)
    folded = fold_record(record, events)
    appended = await store.append(
        task_id=task.id, project_id=task.project_id, events=events, expected_sequence=2, record=folded
    )
    assert appended.revision == 2
    assert appended.last_event_sequence == 2
    assert appended.status is TaskStatus.CLARIFYING
    with pytest.raises(StaleTaskError):
        await store.append(
            task_id=task.id, project_id=task.project_id, events=events, expected_sequence=2, record=folded
        )
    stale = (_event(task, 5, TaskEventType.TASK_STATUS_CHANGED, {"status": "PLANNING"}),)
    with pytest.raises(StaleTaskError):
        await store.append(
            task_id=task.id,
            project_id=task.project_id,
            events=stale,
            expected_sequence=5,
            record=fold_record(folded, stale),
        )
    feed = await store.read_feed(task.id, project_id=task.project_id, after=1)
    assert [entry.feed_sequence for entry in feed] == [2]


@pytest.mark.asyncio
async def test_append_is_fenced_by_lease_epoch(store: TaskStore) -> None:
    task = _task()
    record = await _create(store, task)
    epoch = await store.claim(task.id, project_id=task.project_id, owner="runner-1", ttl_seconds=60)
    assert epoch == 1
    events = (_event(task, 2, TaskEventType.TASK_MESSAGE, {"author": "system", "text": "hi"}),)
    folded = fold_record(record, events)
    with pytest.raises(StaleTaskError):
        await store.append(
            task_id=task.id, project_id=task.project_id, events=events, expected_sequence=2,
            record=folded, lease_epoch=0,
        )
    await store.append(
        task_id=task.id, project_id=task.project_id, events=events, expected_sequence=2,
        record=folded, lease_epoch=1,
    )
    assert [event.sequence for event in await store.read_events(task.id, project_id=task.project_id)] == [1, 2]


@pytest.mark.asyncio
async def test_list_records_filters_by_status_and_scope(store: TaskStore) -> None:
    await _create(store, _task("task-1"))
    await _create(store, _task("task-2", project_id="project-b"))
    records = await store.list_records(project_id="project-a")
    assert [record.task_id for record in records] == ["task-1"]
    assert records[0].last_event_sequence == 1
    assert await store.list_records(project_id="project-a", statuses=(TaskStatus.COMPLETE,)) == []


@pytest.mark.asyncio
async def test_feed_publishes_live_entries(dialect_engine) -> None:  # noqa: F811
    bus = FeedBus()
    store = TaskStore(engine=dialect_engine, feed_bus=bus)
    await store.init()
    task = _task()
    queue = bus.subscribe(task.project_id, task.id)
    await _create(store, task)
    entry = queue.get_nowait()
    assert entry.feed_sequence == 1 and entry.event_type == "TASK_CREATED"


@pytest.mark.asyncio
async def test_claim_renew_release_with_epochs(store: TaskStore) -> None:
    task = _task()
    await _create(store, task)
    first = await store.claim(task.id, project_id=task.project_id, owner="runner-1", ttl_seconds=60)
    assert first == 1
    assert await store.claim(task.id, project_id=task.project_id, owner="runner-2", ttl_seconds=60) is None
    assert await store.renew(task.id, project_id=task.project_id, owner="runner-1", lease_epoch=1, ttl_seconds=60)
    assert not await store.renew(task.id, project_id=task.project_id, owner="runner-2", lease_epoch=1, ttl_seconds=60)
    assert not await store.renew(task.id, project_id=task.project_id, owner="runner-1", lease_epoch=0, ttl_seconds=60)
    record = await store.load_record(task.id, project_id=task.project_id)
    assert record is not None and record.lease_owner == "runner-1" and record.lease_epoch == 1
    assert await store.release(task.id, project_id=task.project_id, owner="runner-1", lease_epoch=1)
    second = await store.claim(task.id, project_id=task.project_id, owner="runner-2", ttl_seconds=60)
    assert second == 2


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed_with_new_epoch(store: TaskStore) -> None:
    task = _task()
    await _create(store, task)
    assert await store.claim(task.id, project_id=task.project_id, owner="runner-1", ttl_seconds=0) == 1
    assert await store.claim(task.id, project_id=task.project_id, owner="runner-2", ttl_seconds=60) == 2
    assert not await store.renew(task.id, project_id=task.project_id, owner="runner-1", lease_epoch=1, ttl_seconds=60)


@pytest.mark.asyncio
async def test_repository_lease_is_exclusive_renewable_and_releasable(store: TaskStore) -> None:
    key = "project-a:o/r:main"
    assert await store.acquire_repository_lease(key, project_id="project-a", task_id="task-1", work_id="w1", ttl_seconds=60)
    assert await store.acquire_repository_lease(key, project_id="project-a", task_id="task-1", work_id="w1", ttl_seconds=60)
    assert not await store.acquire_repository_lease(key, project_id="project-a", task_id="task-2", work_id="w2", ttl_seconds=60)
    assert await store.repository_lease_holder(key, project_id="project-a") == ("task-1", "w1")
    assert await store.renew_repository_lease(key, project_id="project-a", task_id="task-1", ttl_seconds=60)
    assert not await store.renew_repository_lease(key, project_id="project-a", task_id="task-2", ttl_seconds=60)
    assert await store.release_repository_lease(key, project_id="project-a", task_id="task-1")
    assert await store.repository_lease_holder(key, project_id="project-a") is None
    assert await store.acquire_repository_lease(key, project_id="project-a", task_id="task-2", work_id="w2", ttl_seconds=60)


@pytest.mark.asyncio
async def test_expired_repository_lease_can_be_taken_over(store: TaskStore) -> None:
    key = "project-a:o/r:main"
    assert await store.acquire_repository_lease(key, project_id="project-a", task_id="task-1", work_id="w1", ttl_seconds=0)
    assert await store.acquire_repository_lease(key, project_id="project-a", task_id="task-2", work_id="w2", ttl_seconds=60)
    assert await store.repository_lease_holder(key, project_id="project-a") == ("task-2", "w2")
    assert not await store.renew_repository_lease(key, project_id="project-a", task_id="task-1", ttl_seconds=60)


@pytest.mark.asyncio
async def test_terminal_task_cannot_be_claimed(store: TaskStore) -> None:
    task = _task()
    record = await _create(store, task)
    events = (_event(task, 2, TaskEventType.TASK_STATUS_CHANGED, {"status": "CANCELLED"}),)
    await store.append(
        task_id=task.id, project_id=task.project_id, events=events, expected_sequence=2,
        record=fold_record(record, events),
    )
    assert await store.claim(task.id, project_id=task.project_id, owner="runner-1", ttl_seconds=60) is None


@pytest.mark.asyncio
async def test_command_receipt_is_recorded_once(store: TaskStore) -> None:
    task = _task()
    await _create(store, task)
    assert await store.record_command(
        task_id=task.id, project_id=task.project_id, command_id="create-issue:1:step-1", payload={"step": "step-1"}
    )
    assert not await store.record_command(
        task_id=task.id, project_id=task.project_id, command_id="create-issue:1:step-1", payload={"step": "step-1"}
    )


@pytest.mark.asyncio
async def test_spend_ledger_reserves_settles_and_totals(store: TaskStore) -> None:
    task = _task()
    await _create(store, task)
    await store.reserve_spend(SpendReservation(
        reservation_id="r1", project_id=task.project_id, task_id=task.id, cycle=1,
        role="planner", runtime="harness:medium", usd_reserved=Decimal("0.40"),
    ))
    await store.reserve_spend(SpendReservation(
        reservation_id="r2", project_id=task.project_id, task_id=task.id, cycle=1,
        role="implementer", runtime="codex", usd_reserved=Decimal("0"),
    ))
    totals = await store.spend_totals(task_id=task.id, project_id=task.project_id, cycle=1)
    assert totals.usd_reserved == Decimal("0.40") and totals.usd_actual == Decimal("0")
    await store.settle_spend("r1", project_id=task.project_id, usd_actual=Decimal("0.25"))
    await store.settle_spend("r2", project_id=task.project_id, usd_actual=None)
    totals = await store.spend_totals(task_id=task.id, project_id=task.project_id, cycle=1)
    assert totals.usd_reserved == Decimal("0")
    assert totals.usd_actual == Decimal("0.25")
    assert totals.unknown_settlements == 1 and totals.reservations == 2
    with pytest.raises(ValueError):
        await store.reserve_spend(SpendReservation(
            reservation_id="r1", project_id=task.project_id, task_id=task.id, cycle=1,
            role="planner", runtime="harness:medium", usd_reserved=Decimal("1"),
        ))
    with pytest.raises(KeyError):
        await store.settle_spend("missing", project_id=task.project_id, usd_actual=Decimal("1"))


@pytest.mark.asyncio
async def test_defaults_are_per_project_with_revision_cas(store: TaskStore) -> None:
    defaults = await store.get_defaults(project_id="project-a")
    assert defaults.project_id == "project-a" and defaults.revision == 0
    saved = await store.put_defaults(
        TaskDefaults(project_id="project-a", timezone="Europe/Berlin"), expected_revision=0
    )
    assert saved.revision == 1 and saved.timezone == "Europe/Berlin"
    with pytest.raises(StaleTaskError):
        await store.put_defaults(
            TaskDefaults(project_id="project-a", timezone="UTC"), expected_revision=0
        )
    assert (await store.put_defaults(TaskDefaults(project_id="project-a", timezone="UTC"), expected_revision=1)).revision == 2
    assert (await store.get_defaults(project_id="project-b")).timezone == "UTC"


@pytest.mark.asyncio
async def test_append_never_rewrites_lease_and_detects_projection_change(store: TaskStore) -> None:
    task = _task()
    record = await _create(store, task)
    epoch = await store.claim(task.id, project_id=task.project_id, owner="runner-1", ttl_seconds=60)
    events = (_event(task, 2, TaskEventType.TASK_MESSAGE, {"author": "system", "text": "hi"}),)
    folded = fold_record(record, events)
    await store.append(
        task_id=task.id, project_id=task.project_id, events=events, expected_sequence=2,
        record=folded, lease_epoch=epoch,
    )
    after = await store.load_record(task.id, project_id=task.project_id)
    assert after is not None and after.lease_owner == "runner-1" and after.lease_epoch == 1
    assert after.lease_expires_at is not None
    more = (_event(task, 3, TaskEventType.TASK_MESSAGE, {"author": "system", "text": "again"}),)
    with pytest.raises(StaleTaskError):
        await store.append(
            task_id=task.id, project_id=task.project_id, events=more, expected_sequence=3,
            record=fold_record(folded, more), lease_epoch=epoch, expected_revision=7,
        )


@pytest.mark.asyncio
async def test_append_rejects_projection_sequence_mismatch(store: TaskStore) -> None:
    task = _task()
    record = await _create(store, task)
    events = (_event(task, 2, TaskEventType.TASK_STATUS_CHANGED, {"status": "CLARIFYING"}),)
    folded = fold_record(record, events)
    stale_projection = folded.model_copy(update={"last_event_sequence": 1})
    with pytest.raises(ValueError, match="projection last_event_sequence"):
        await store.append(
            task_id=task.id,
            project_id=task.project_id,
            events=events,
            expected_sequence=2,
            record=stale_projection,
        )
    assert [event.sequence for event in await store.read_events(task.id, project_id=task.project_id)] == [1]


@pytest.mark.asyncio
async def test_append_feed_sequences_after_task_events_and_requires_task(store: TaskStore) -> None:
    task = _task()
    await _create(store, task)
    entry = FeedEntry(
        project_id=task.project_id, task_id=task.id, feed_sequence=1, source="work_event",
        source_id="work-event-1", event_type="STAGE_STARTED", payload_json={"stage": "plan"}, created_at=NOW,
    )
    stored = await store.append_feed((entry,))
    assert [item.feed_sequence for item in stored] == [2]
    feed = await store.read_feed(task.id, project_id=task.project_id)
    assert [(item.feed_sequence, item.source) for item in feed] == [(1, "task_event"), (2, "work_event")]
    with pytest.raises(KeyError):
        await store.append_feed((entry.model_copy(update={"task_id": "missing"}),))
