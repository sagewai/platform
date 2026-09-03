# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Concurrency invariants of the Task store, on SQLite and on PostgreSQL."""

from __future__ import annotations

import asyncio

import pytest

from sagewai.work.tasks.events import TaskEventType
from sagewai.work.tasks.store import StaleTaskError, TaskStore
from sagewai.work.tasks.writer import TaskWriter
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import NOW, _create, _task


@pytest.fixture
async def store(dialect_engine) -> TaskStore:  # noqa: F811
    result = TaskStore(engine=dialect_engine)
    await result.init()
    return result


def _message(text: str):
    return (TaskEventType.TASK_MESSAGE, {"author": "coordinator", "text": text, "refs": []})


@pytest.mark.asyncio
async def test_only_one_claimer_wins_the_lease(store: TaskStore) -> None:
    task = _task()
    await _create(store, task)
    epochs = await asyncio.gather(
        *(
            store.claim(
                task.id, project_id=task.project_id, owner=f"runner-{index}", ttl_seconds=90
            )
            for index in range(5)
        )
    )
    assert sorted(epoch is None for epoch in epochs) == [False, True, True, True, True]
    assert max(epoch for epoch in epochs if epoch is not None) == 1


@pytest.mark.asyncio
async def test_two_appends_at_the_same_sequence_cannot_both_land(store: TaskStore) -> None:
    task = _task()
    record = await _create(store, task)
    writer = TaskWriter(store)
    results = await asyncio.gather(
        writer.append(record, [_message("a")], now=NOW),
        writer.append(record, [_message("b")], now=NOW),
        return_exceptions=True,
    )
    landed = [result for result in results if not isinstance(result, BaseException)]
    failed = [result for result in results if isinstance(result, StaleTaskError)]
    assert len(landed) == 1 and len(failed) == 1
    assert len(await store.read_events(task.id, project_id=task.project_id)) == 2


@pytest.mark.asyncio
async def test_an_append_with_a_stale_lease_epoch_is_rejected(store: TaskStore) -> None:
    task = _task()
    record = await _create(store, task)
    epoch = await store.claim(task.id, project_id=task.project_id, owner="runner-1", ttl_seconds=90)
    with pytest.raises(StaleTaskError):
        await TaskWriter(store).append(
            record, [_message("late")], lease_epoch=epoch - 1, now=NOW
        )


@pytest.mark.asyncio
async def test_only_one_task_holds_a_repository_lease(store: TaskStore) -> None:
    first, second = _task("task-1"), _task("task-2")
    await _create(store, first)
    await _create(store, second)
    key = first.repository_lease_key
    held = await asyncio.gather(
        store.acquire_repository_lease(
            key, project_id=first.project_id, task_id=first.id, work_id=None, ttl_seconds=60
        ),
        store.acquire_repository_lease(
            key, project_id=second.project_id, task_id=second.id, work_id=None, ttl_seconds=60
        ),
    )
    assert sorted(held) == [False, True]
    holder, _work_id = await store.repository_lease_holder(key, project_id=first.project_id)
    assert holder in {first.id, second.id}
    assert (
        await store.release_repository_lease(key, project_id=first.project_id, task_id=holder)
        is True
    )


@pytest.mark.asyncio
async def test_one_command_receipt_wins_under_concurrency(store: TaskStore) -> None:
    task = _task()
    await _create(store, task)
    recorded = await asyncio.gather(
        *(
            store.record_command(
                task_id=task.id,
                project_id=task.project_id,
                command_id="start_step:3",
                payload={"n": index},
            )
            for index in range(4)
        )
    )
    assert sorted(recorded) == [False, False, False, True]
