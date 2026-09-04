# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pause holds a Task where it stands; resume returns it there; cancel is terminal."""

from __future__ import annotations

import pytest

from sagewai.work.tasks.events import TaskEventType
from sagewai.work.tasks.models import TaskStatus
from sagewai.work.tasks.service import TaskDecisionError, TaskService
from sagewai.work.tasks.store import TaskStore
from sagewai.work.tasks.transitions import IllegalTransitionError
from sagewai.work.tasks.writer import TaskWriter, status_entry
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _create, _task


@pytest.fixture
async def service_and_record(dialect_engine, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))
    store = TaskStore(engine=dialect_engine)
    await store.init()
    task = _task("t-1")
    record = await _create(store, task)
    return TaskService(store=store), record


@pytest.mark.asyncio
async def test_pause_writes_one_status_entry(service_and_record) -> None:
    service, record = service_and_record

    paused = await service.pause(record.task_id, project_id=record.project_id, actor_ref="arda")

    assert paused.status is TaskStatus.PAUSED
    assert paused.board_column.value == "planned"
    events = await service._store.read_events(record.task_id, project_id=record.project_id)
    assert events[-1].event_type is TaskEventType.TASK_STATUS_CHANGED
    assert events[-1].payload_json == {"status": "PAUSED"}


@pytest.mark.asyncio
async def test_resume_returns_to_the_status_pause_left(service_and_record) -> None:
    service, record = service_and_record
    paused = await service.pause(record.task_id, project_id=record.project_id, actor_ref="arda")

    resumed = await service.resume(paused.task_id, project_id=paused.project_id, actor_ref="arda")

    assert resumed.status is TaskStatus.PLANNING


@pytest.mark.asyncio
async def test_pausing_a_paused_task_is_idempotent(service_and_record) -> None:
    service, record = service_and_record
    paused = await service.pause(record.task_id, project_id=record.project_id, actor_ref="arda")

    again = await service.pause(record.task_id, project_id=record.project_id, actor_ref="arda")

    assert again.revision == paused.revision


@pytest.mark.asyncio
async def test_resuming_a_running_task_is_refused(service_and_record) -> None:
    service, record = service_and_record

    with pytest.raises(TaskDecisionError, match="not PAUSED"):
        await service.resume(record.task_id, project_id=record.project_id, actor_ref="arda")


@pytest.mark.asyncio
async def test_cancel_is_terminal_and_carries_its_note(service_and_record) -> None:
    service, record = service_and_record

    cancelled = await service.cancel(
        record.task_id, project_id=record.project_id, actor_ref="arda", note="superseded by hand"
    )

    assert cancelled.status is TaskStatus.CANCELLED
    assert cancelled.board_column.value == "done"
    events = await service._store.read_events(record.task_id, project_id=record.project_id)
    assert events[-2].event_type is TaskEventType.TASK_MESSAGE
    assert events[-2].payload_json["text"] == "superseded by hand"


@pytest.mark.asyncio
async def test_pausing_a_cancelled_task_is_an_illegal_transition(service_and_record) -> None:
    service, record = service_and_record
    await service.cancel(record.task_id, project_id=record.project_id, actor_ref="arda")

    with pytest.raises(IllegalTransitionError):
        await service.pause(record.task_id, project_id=record.project_id, actor_ref="arda")


@pytest.mark.asyncio
async def test_cancelling_a_cancelled_task_is_idempotent(service_and_record) -> None:
    service, record = service_and_record
    first = await service.cancel(record.task_id, project_id=record.project_id, actor_ref="arda")

    again = await service.cancel(record.task_id, project_id=record.project_id, actor_ref="arda")

    assert again.revision == first.revision


@pytest.mark.asyncio
async def test_a_paused_scheduled_task_resumes_to_scheduled(service_and_record) -> None:
    service, record = service_and_record
    running = await TaskWriter(service._store).append(
        record, [status_entry(record, TaskStatus.EXECUTING)]
    )
    assessing = await TaskWriter(service._store).append(
        running, [status_entry(running, TaskStatus.ASSESSING)]
    )
    scheduled = await TaskWriter(service._store).append(
        assessing, [status_entry(assessing, TaskStatus.SCHEDULED)]
    )
    await service.pause(scheduled.task_id, project_id=scheduled.project_id, actor_ref="arda")

    resumed = await service.resume(
        scheduled.task_id, project_id=scheduled.project_id, actor_ref="arda"
    )

    assert resumed.status is TaskStatus.SCHEDULED


@pytest.mark.asyncio
async def test_a_task_the_health_sweeper_paused_resumes_too(service_and_record) -> None:
    """The coordinator's health pause writes a plain status entry; resume must still work."""
    service, record = service_and_record
    running = await TaskWriter(service._store).append(
        record, [status_entry(record, TaskStatus.EXECUTING)]
    )
    assessing = await TaskWriter(service._store).append(
        running, [status_entry(running, TaskStatus.ASSESSING)]
    )
    scheduled = await TaskWriter(service._store).append(
        assessing, [status_entry(assessing, TaskStatus.SCHEDULED)]
    )
    paused = await TaskWriter(service._store).append(
        scheduled, [(TaskEventType.TASK_STATUS_CHANGED, {"status": "PAUSED"})]
    )

    resumed = await service.resume(paused.task_id, project_id=paused.project_id, actor_ref="arda")

    assert resumed.status is TaskStatus.SCHEDULED
