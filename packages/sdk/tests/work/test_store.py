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
async def test_duplicate_work_sequence_is_rejected(store: WorkStore) -> None:
    await store.append_event(_event(1, event_id="event-a"))

    with pytest.raises(IntegrityError):
        await store.append_event(_event(1, event_id="event-b"))


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
async def test_event_append_rejects_projection_project_mismatch(
    store: WorkStore,
) -> None:
    await store.save_work(_record(project_id=None))

    with pytest.raises(ValueError, match="different project"):
        await store.append_event(_event(1, project_id="project-b"))

    assert await store.read_events("work-1", project_id="project-b") == []


@pytest.mark.asyncio
async def test_event_stream_cannot_fork_projects_before_projection(
    store: WorkStore,
) -> None:
    await store.append_event(_event(1, project_id=None))

    with pytest.raises(ValueError, match="different project"):
        await store.append_event(_event(2, project_id="project-b"))

    events = await store.read_events("work-1", project_id=None)
    assert [event.sequence for event in events] == [1]


@pytest.mark.asyncio
async def test_projection_cannot_fork_from_existing_event_stream(
    store: WorkStore,
) -> None:
    await store.append_event(_event(1, project_id=None))

    with pytest.raises(ValueError, match="different project"):
        await store.save_work(_record(project_id="project-a"))

    assert await store.load_work("work-1", project_id="project-a") is None
    events = await store.read_events("work-1", project_id=None)
    assert [event.sequence for event in events] == [1]


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
async def test_projection_reads_and_identity_are_project_scoped(store: WorkStore) -> None:
    await store.save_work(_record())

    assert await store.load_work("work-1", project_id="project-b") is None
    with pytest.raises(ValueError, match="different project"):
        await store.save_work(_record(project_id="project-b"))


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
async def test_org_global_projection_cannot_change_project(store: WorkStore) -> None:
    await store.save_work(_record(project_id=None))

    with pytest.raises(ValueError, match="different project"):
        await store.save_work(_record(project_id="project-a"))
