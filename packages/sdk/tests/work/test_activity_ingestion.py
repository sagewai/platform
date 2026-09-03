# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Activity is persisted per run and mirrored into the owning Task's feed."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import pytest

from sagewai.work import (
    ACTIVITY_ROW_CAP,
    OperatorActivity,
    WorkActivityStore,
    WorkRecord,
    WorkStore,
)
from sagewai.work.activity_ingestion import ActivityIngestion, BatchingActivitySink
from sagewai.work.tasks import TaskEventType, TaskStore
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _event, _record, _task

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _activity(sequence: int, **overrides) -> OperatorActivity:
    values = dict(
        project_id="p",
        work_id="w",
        run_id="w:implement:1",
        sequence=sequence,
        at=NOW,
        source="codex",
        kind="message",
        summary=f"line {sequence}",
    )
    values.update(overrides)
    return OperatorActivity(**values)


def _work_record(work_id: str, *, profile_context: dict, project_id: str = "p") -> WorkRecord:
    return WorkRecord(
        work_id=work_id,
        project_id=project_id,
        source_ref=f"issue:{work_id}",
        profile="software",
        status="IMPLEMENTING",
        contract_version=None,
        active_run_id=None,
        pending_gate=None,
        profile_context=profile_context,
        created_at=NOW,
        updated_at=NOW,
    )


def _created_task(task_id: str, *, project_id: str = "p"):
    task = _task(task_id, project_id=project_id)
    record = _record(task)
    events = (_event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),)
    return task, record, events


@pytest.mark.asyncio
async def test_batching_sink_flushes_on_size_and_interval(dialect_engine) -> None:  # noqa: F811
    flushed: list[list[OperatorActivity]] = []

    async def flush(batch: list[OperatorActivity]) -> None:
        flushed.append(list(batch))

    sink = BatchingActivitySink(flush, max_batch=2, interval=0.05)
    sink.emit(_activity(1))
    sink.emit(_activity(2))
    sink.emit(_activity(3))
    await asyncio.sleep(0.2)
    await sink.close()
    assert [len(batch) for batch in flushed] == [2, 1]


@pytest.mark.asyncio
async def test_batching_sink_splits_cjk_batches_by_wire_bytes() -> None:
    flushed: list[list[OperatorActivity]] = []

    async def flush(batch: list[OperatorActivity]) -> None:
        flushed.append(list(batch))

    max_batch_bytes = 512 * 1024
    activities = [
        _activity(
            sequence,
            source="claude",
            kind="tool_result",
            summary="中" * 2000,
            detail="中" * 8192,
        )
        for sequence in range(1, 51)
    ]
    assert all(
        len(json.dumps(activity.model_dump(mode="json"))) <= max_batch_bytes
        for activity in activities
    )

    sink = BatchingActivitySink(
        flush,
        max_batch=50,
        max_batch_bytes=max_batch_bytes,
        interval=60,
    )
    for activity in activities:
        sink.emit(activity)
    await sink.close()

    assert len(flushed) > 1
    assert [item.sequence for batch in flushed for item in batch] == list(range(1, 51))
    for batch in flushed:
        body = {
            "run_id": "w:implement:1",
            "activities": [activity.model_dump(mode="json") for activity in batch],
        }
        assert len(json.dumps(body)) <= 640 * 1024


@pytest.mark.asyncio
async def test_batching_sink_close_flushes_tail_immediately_and_clears_pending() -> None:
    flushed: list[list[OperatorActivity]] = []

    async def flush(batch: list[OperatorActivity]) -> None:
        flushed.append(list(batch))

    sink = BatchingActivitySink(flush, max_batch=50, interval=60)
    sink.emit(_activity(1))
    await sink.close()
    assert [len(batch) for batch in flushed] == [1]
    assert sink._pending == set()


@pytest.mark.asyncio
async def test_batching_sink_serializes_flushes_in_emission_order() -> None:
    first_started = asyncio.Event()
    allow_first = asyncio.Event()
    flushed: list[list[int]] = []

    async def flush(batch: list[OperatorActivity]) -> None:
        if batch[0].sequence == 1:
            first_started.set()
            await allow_first.wait()
        flushed.append([item.sequence for item in batch])

    sink = BatchingActivitySink(flush, max_batch=1, interval=60)
    sink.emit(_activity(1))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    sink.emit(_activity(2))
    await asyncio.sleep(0)

    assert flushed == []

    allow_first.set()
    await sink.close()
    assert flushed == [[1], [2]]


@pytest.mark.asyncio
async def test_batching_sink_logs_flush_errors_without_propagating(caplog) -> None:
    async def flush(batch: list[OperatorActivity]) -> None:
        raise RuntimeError("boom")

    caplog.set_level(logging.ERROR, logger="sagewai.work.activity_ingestion")
    sink = BatchingActivitySink(flush, max_batch=1, interval=60)
    sink.emit(_activity(1))
    await sink.close()
    assert "activity flush failed" in caplog.text


@pytest.mark.asyncio
async def test_ingestion_mirrors_activity_into_the_task_feed_only_for_task_works(dialect_engine) -> None:  # noqa: F811
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    activity_store = WorkActivityStore(engine=dialect_engine)
    await activity_store.init()
    task, record, events = _created_task("t1")
    await task_store.create(task, events=events, record=record)
    await work_store.save_work(_work_record("w1", profile_context={"task_id": "t1"}))
    await work_store.save_work(_work_record("w2", profile_context={}))
    ingestion = ActivityIngestion(
        work_store=work_store,
        task_store=task_store,
        activity_store=activity_store,
    )
    await ingestion.ingest([_activity(1, work_id="w1"), _activity(1, work_id="w2")])
    feed = await task_store.read_feed("t1", project_id="p")
    activity_entries = [entry for entry in feed if entry.source == "activity"]
    assert [entry.source_id for entry in activity_entries] == ["w:implement:1:1"]
    assert activity_entries[0].event_type == "activity.message"
    assert len(await activity_store.read("w1", run_id="w:implement:1", project_id="p")) == 1
    assert len(await activity_store.read("w2", run_id="w:implement:1", project_id="p")) == 1


@pytest.mark.asyncio
async def test_ingestion_mirrors_only_inserted_activity_and_marker(dialect_engine) -> None:  # noqa: F811
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    activity_store = WorkActivityStore(engine=dialect_engine)
    await activity_store.init()
    task, record, events = _created_task("t1")
    await task_store.create(task, events=events, record=record)
    await work_store.save_work(_work_record("w1", profile_context={"task_id": "t1"}))
    ingestion = ActivityIngestion(
        work_store=work_store,
        task_store=task_store,
        activity_store=activity_store,
    )

    first = await ingestion.ingest([_activity(1, work_id="w1"), _activity(2, work_id="w1")])
    replayed = await ingestion.ingest([_activity(1, work_id="w1"), _activity(2, work_id="w1")])
    marker = await ingestion.ingest(
        [_activity(ACTIVITY_ROW_CAP + 5, work_id="w1"), _activity(ACTIVITY_ROW_CAP + 6, work_id="w1")]
    )

    assert [entry.source_id for entry in first] == ["w:implement:1:1", "w:implement:1:2"]
    assert replayed == []
    assert [(entry.source_id, entry.event_type, entry.payload_json["kind"]) for entry in marker] == [
        (f"w:implement:1:{ACTIVITY_ROW_CAP}", "activity.raw", "raw")
    ]
    activity_entries = [entry for entry in await task_store.read_feed("t1", project_id="p") if entry.source == "activity"]
    assert [entry.source_id for entry in activity_entries] == [
        "w:implement:1:1",
        "w:implement:1:2",
        f"w:implement:1:{ACTIVITY_ROW_CAP}",
    ]


@pytest.mark.asyncio
async def test_ingestion_loads_same_work_id_in_each_project(dialect_engine) -> None:  # noqa: F811
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    activity_store = WorkActivityStore(engine=dialect_engine)
    await activity_store.init()
    task_p, record_p, events_p = _created_task("task-p", project_id="p")
    task_other, record_other, events_other = _created_task("task-other", project_id="other")
    await task_store.create(task_p, events=events_p, record=record_p)
    await task_store.create(task_other, events=events_other, record=record_other)
    await work_store.save_work(_work_record("shared", project_id="p", profile_context={"task_id": "task-p"}))
    await work_store.save_work(
        _work_record("shared", project_id="other", profile_context={"task_id": "task-other"})
    )
    ingestion = ActivityIngestion(
        work_store=work_store,
        task_store=task_store,
        activity_store=activity_store,
    )

    stored = await ingestion.ingest(
        [
            _activity(1, project_id="p", work_id="shared", run_id="shared:p:1"),
            _activity(1, project_id="other", work_id="shared", run_id="shared:other:1"),
        ]
    )

    assert [(entry.project_id, entry.task_id, entry.source_id) for entry in stored] == [
        ("other", "task-other", "shared:other:1:1"),
        ("p", "task-p", "shared:p:1:1"),
    ]
