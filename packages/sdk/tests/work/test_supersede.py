# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Superseding a Work is terminal and removes it from active and attention views."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.work import (
    PendingAttentionKind,
    WorkEvent,
    WorkEventType,
    WorkRecord,
    WorkStore,
)
from sagewai.work.supersede import supersede_work
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
async def store(dialect_engine) -> WorkStore:  # noqa: F811
    result = WorkStore(engine=dialect_engine)
    await result.init()
    return result


async def _blocked_work(store: WorkStore, work_id: str) -> None:
    await store.save_work(
        WorkRecord(
            work_id=work_id,
            project_id="p",
            source_ref=None,
            profile="software",
            status="WORK_BLOCKED",
            contract_version=1,
            active_run_id=None,
            pending_gate=None,
            profile_context={},
            created_at=NOW,
            updated_at=NOW,
        )
    )
    await store.append_event(
        WorkEvent(
            id=f"{work_id}-1",
            project_id="p",
            work_id=work_id,
            sequence=1,
            event_type=WorkEventType.WORK_BLOCKED,
            actor_type="system",
            actor_ref=None,
            payload_json={
                "reason": "implement_failed",
                "decision_request": "?",
            },
            created_at=NOW,
        )
    )
    await store.append_event(
        WorkEvent(
            id=f"{work_id}-2",
            project_id="p",
            work_id=work_id,
            sequence=2,
            event_type=WorkEventType.GATE_REQUESTED,
            actor_type="system",
            actor_ref=None,
            payload_json={"gate_id": f"merge:{work_id}", "question": "Approve?"},
            created_at=NOW,
        )
    )
    await store.append_event(
        WorkEvent(
            id=f"{work_id}-3",
            project_id="p",
            work_id=work_id,
            sequence=3,
            event_type=WorkEventType.CONTROL_DEGRADED,
            actor_type="system",
            actor_ref=None,
            payload_json={"failed_preconditions": ["observability"]},
            created_at=NOW,
        )
    )


@pytest.mark.asyncio
async def test_supersede_is_terminal_idempotent_and_hidden(
    store: WorkStore,
) -> None:
    await _blocked_work(store, "w1")
    assert {item.kind for item in await store.pending_attention(project_id="p")} == {
        PendingAttentionKind.WORK_BLOCKED,
        PendingAttentionKind.GATE_REQUESTED,
        PendingAttentionKind.CONTROL_DEGRADED,
    }
    record = await supersede_work(
        store,
        work_id="w1",
        project_id="p",
        superseded_by="w2",
        reason="base_moved",
        actor_ref="coordinator",
    )
    assert (
        record.status == "SUPERSEDED"
        and record.pending_gate is None
        and record.active_run_id is None
    )
    again = await supersede_work(
        store,
        work_id="w1",
        project_id="p",
        superseded_by="w2",
        reason="base_moved",
        actor_ref="coordinator",
    )
    assert again.status == "SUPERSEDED"
    events = await store.read_events("w1", project_id="p")
    superseded = [
        event
        for event in events
        if event.event_type is WorkEventType.WORK_SUPERSEDED
    ]
    assert len(superseded) == 1
    assert superseded[0].payload_json == {
        "superseded_by": "w2",
        "reason": "base_moved",
    }
    active = await store.list_work(project_id="p", active_only=True)
    assert [item.work_id for item in active] == []
    assert await store.pending_attention(project_id="p") == ()


@pytest.mark.asyncio
async def test_supersede_unknown_work_raises(store: WorkStore) -> None:
    with pytest.raises(KeyError):
        await supersede_work(
            store,
            work_id="missing",
            project_id="p",
            superseded_by="w2",
            reason="x",
            actor_ref="c",
        )


@pytest.mark.asyncio
async def test_list_work_active_only_includes_blocked_and_ready_to_merge(
    store: WorkStore,
) -> None:
    for index, status in enumerate(
        ("WORK_BLOCKED", "READY_TO_MERGE", "COMPLETE", "SUPERSEDED"),
        start=1,
    ):
        await store.save_work(
            WorkRecord(
                work_id=f"w{index}",
                project_id="p",
                source_ref=None,
                profile="software",
                status=status,
                contract_version=1,
                active_run_id=None,
                pending_gate=None,
                profile_context={},
                created_at=NOW,
                updated_at=NOW,
            )
        )

    active = await store.list_work(project_id="p", active_only=True)

    assert [(item.work_id, item.status) for item in active] == [
        ("w1", "WORK_BLOCKED"),
        ("w2", "READY_TO_MERGE"),
    ]
