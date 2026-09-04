# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Dual-dialect tests for the append-only Work store."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from sagewai.work import WorkEvent, WorkEventType, WorkRecord, WorkStore
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store(dialect_engine) -> WorkStore:  # noqa: F811
    result = WorkStore(engine=dialect_engine)
    await result.init()
    return result


def _event(
    sequence: int,
    *,
    event_id: str | None = None,
    project_id: str | None = "project-a",
    payload: dict | None = None,
) -> WorkEvent:
    return WorkEvent(
        id=event_id or f"event-{sequence}",
        project_id=project_id,
        work_id="work-1",
        sequence=sequence,
        event_type=WorkEventType.STAGE_STARTED,
        actor_type="system",
        actor_ref=None,
        payload_json=payload or {"stage": f"stage-{sequence}"},
        created_at=NOW,
    )


def _record(**updates) -> WorkRecord:
    values = {
        "work_id": "work-1",
        "project_id": "project-a",
        "source_ref": "local://pr-1",
        "profile": "software",
        "status": "received",
        "contract_version": None,
        "active_run_id": None,
        "pending_gate": None,
        "profile_context": {"opaque": {"key": ["value"]}},
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return WorkRecord.model_validate(values)


@pytest.mark.asyncio
async def test_events_are_read_in_sequence_order(store: WorkStore) -> None:
    await store.append_event(_event(2))
    await store.append_event(_event(1))

    events = await store.read_events("work-1", project_id="project-a")

    assert [event.sequence for event in events] == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("project_id", ["project-a", None])
async def test_duplicate_work_sequence_is_rejected_within_scope(
    store: WorkStore,
    project_id: str | None,
) -> None:
    await store.append_event(_event(1, event_id="event-a", project_id=project_id))

    with pytest.raises(IntegrityError):
        await store.append_event(_event(1, event_id="event-b", project_id=project_id))


@pytest.mark.asyncio
async def test_global_degradation_receipt_remains_durable(
    store: WorkStore,
) -> None:
    event = _event(
        1,
        project_id=None,
        payload={"failed_preconditions": ["authority"]},
    ).model_copy(update={"event_type": WorkEventType.CONTROL_DEGRADED})

    await store.append_event(event)

    assert await store.read_events("work-1", project_id=None) == [event]


@pytest.mark.asyncio
async def test_persisted_events_are_immutable(store: WorkStore) -> None:
    event = _event(1, payload={"state": {"value": "original"}})
    await store.append_event(event)

    event.payload_json["state"]["value"] = "mutated"
    stored = (await store.read_events("work-1", project_id="project-a"))[0]

    assert stored.payload_json == {"state": {"value": "original"}}
    with pytest.raises(ValidationError):
        stored.sequence = 2  # type: ignore[misc]
    assert not hasattr(store, "update_event")
    assert not hasattr(store, "delete_event")


@pytest.mark.asyncio
async def test_event_reads_are_project_scoped(store: WorkStore) -> None:
    await store.append_event(_event(1))

    assert await store.read_events("work-1", project_id="project-b") == []


@pytest.mark.asyncio
async def test_event_identity_and_stream_are_project_scoped(store: WorkStore) -> None:
    events = (
        _event(
            1,
            event_id="shared-event",
            project_id="project-a",
            payload={"stage": "project-a"},
        ),
        _event(
            1,
            event_id="shared-event",
            project_id="project-b",
            payload={"stage": "project-b"},
        ),
        _event(
            1,
            event_id="shared-event",
            project_id=None,
            payload={"stage": "global"},
        ),
    )
    for event in events:
        await store.append_event(event)

    assert await store.read_events("work-1", project_id="project-a") == [events[0]]
    assert await store.read_events("work-1", project_id="project-b") == [events[1]]
    assert await store.read_events("work-1", project_id=None) == [events[2]]
    assert await store.read_events("work-1", project_id="project-c") == []


@pytest.mark.asyncio
async def test_projection_save_load_and_profile_context_round_trip(store: WorkStore) -> None:
    await store.save_work(_record())
    updated = _record(
        status="contract_ready",
        contract_version=2,
        active_run_id="run-1",
        profile_context={"opaque": {"key": ["new", "values"]}},
    )
    await store.save_work(updated)

    loaded = await store.load_work("work-1", project_id="project-a")

    assert loaded == updated
    assert loaded.profile_context == {"opaque": {"key": ["new", "values"]}}


@pytest.mark.asyncio
async def test_projection_identity_and_reads_are_project_scoped(store: WorkStore) -> None:
    records = (
        _record(project_id="project-a", profile_context={"scope": "project-a"}),
        _record(project_id="project-b", profile_context={"scope": "project-b"}),
        _record(project_id=None, profile_context={"scope": "global"}),
    )
    for record in records:
        await store.save_work(record)

    assert await store.load_work("work-1", project_id="project-a") == records[0]
    assert await store.load_work("work-1", project_id="project-b") == records[1]
    assert await store.load_work("work-1", project_id=None) == records[2]
    assert await store.load_work("work-1", project_id="project-c") is None

    updated = records[0].model_copy(update={"status": "contract_ready", "updated_at": NOW})
    await store.save_work(updated)

    assert await store.load_work("work-1", project_id="project-a") == updated
    assert await store.load_work("work-1", project_id="project-b") == records[1]
    assert await store.load_work("work-1", project_id=None) == records[2]


@pytest.mark.asyncio
async def test_source_ref_lookup_is_project_scoped(store: WorkStore) -> None:
    source_ref = "https://github.com/octocat/hello-world/issues/42"
    await store.save_work(_record(source_ref=source_ref))

    assert await store.find_work_by_source_ref(
        source_ref,
        project_id="project-a",
    ) == _record(source_ref=source_ref)
    assert (
        await store.find_work_by_source_ref(
            source_ref,
            project_id="project-b",
        )
        is None
    )


@pytest.mark.asyncio
async def test_pending_attention_skips_task_plan_works(store: WorkStore) -> None:
    """A planning Work's attention belongs to the Task, not to the Work inbox."""
    now = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)
    for work_id, profile in (("w-plan", "task_plan"), ("w-code", "software")):
        await store.save_work(
            WorkRecord(
                work_id=work_id,
                project_id="project-a",
                source_ref="task-1",
                profile=profile,
                status="WORK_BLOCKED",
                contract_version=1,
                active_run_id=None,
                pending_gate=None,
                profile_context={},
                created_at=now,
                updated_at=now,
            )
        )
        await store.append_event(
            WorkEvent(
                id=f"{work_id}-blocked",
                project_id="project-a",
                work_id=work_id,
                sequence=1,
                event_type=WorkEventType.WORK_BLOCKED,
                actor_type="system",
                actor_ref="test",
                payload_json={"reason": "needs a decision", "decision_request": "choose"},
                created_at=now,
            )
        )
    pending = await store.pending_attention(project_id="project-a")
    assert [item.work_id for item in pending] == ["w-code"]


@pytest.mark.asyncio
async def test_append_next_numbers_from_the_stream(store: WorkStore) -> None:
    first = await store.append_next(
        work_id="w1",
        project_id="p",
        event_type=WorkEventType.WORK_CREATED,
        payload={"work_id": "w1"},
        actor_type="cli",
        actor_ref="test",
    )
    second = await store.append_next(
        work_id="w1",
        project_id="p",
        event_type=WorkEventType.GATE_REQUESTED,
        payload={"gate_id": "merge:w1:3", "question": "Approve."},
        actor_type="human",
        actor_ref="arda",
    )

    assert (first.sequence, second.sequence) == (1, 2)
    assert second.actor_type == "human"
    stored = await store.read_events("w1", project_id="p")
    assert [event.id for event in stored] == [first.id, second.id]
    assert (stored[1].event_type, stored[1].payload_json) == (
        WorkEventType.GATE_REQUESTED,
        {"gate_id": "merge:w1:3", "question": "Approve."},
    )


@pytest.mark.asyncio
async def test_append_next_is_project_scoped(store: WorkStore) -> None:
    await store.append_next(
        work_id="w1",
        project_id="p",
        event_type=WorkEventType.WORK_CREATED,
        payload={"work_id": "w1"},
        actor_type="cli",
        actor_ref="test",
    )

    other = await store.append_next(
        work_id="w1",
        project_id="q",
        event_type=WorkEventType.WORK_CREATED,
        payload={"work_id": "w1"},
        actor_type="cli",
        actor_ref="test",
    )

    assert other.sequence == 1
