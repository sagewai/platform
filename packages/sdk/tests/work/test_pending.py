# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Canonical project-scoped pending-attention query tests."""

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
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
ISSUE_URL = "https://github.com/octocat/hello-world/issues/42"


@pytest.fixture
async def store(dialect_engine) -> WorkStore:  # noqa: F811
    result = WorkStore(engine=dialect_engine)
    await result.init()
    return result


def _record(
    *,
    work_id: str = "work-1",
    project_id: str = "project-a",
    status: str = "WORK_BLOCKED",
    pending_gate: str | None = "merge:work-1:7",
) -> WorkRecord:
    return WorkRecord(
        work_id=work_id,
        project_id=project_id,
        source_ref=ISSUE_URL,
        profile="software",
        status=status,
        contract_version=1,
        active_run_id=None,
        pending_gate=pending_gate,
        profile_context={"base_sha": "a" * 40},
        created_at=NOW,
        updated_at=NOW,
    )


def _event(
    sequence: int,
    event_type: WorkEventType,
    payload: dict,
    *,
    work_id: str = "work-1",
    project_id: str = "project-a",
) -> WorkEvent:
    return WorkEvent(
        id=f"{work_id}-event-{sequence}",
        project_id=project_id,
        work_id=work_id,
        sequence=sequence,
        event_type=event_type,
        actor_type="test",
        actor_ref=None,
        payload_json=payload,
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_pending_attention_is_canonical_resolved_and_project_scoped(
    store: WorkStore,
) -> None:
    await store.save_work(_record())
    await store.append_event(
        _event(
            1,
            WorkEventType.GATE_REQUESTED,
            {
                "gate_id": "merge:work-1:7",
                "question": "Approve merge of PR #7.",
                "evidence_refs": ["review://accepted"],
            },
        )
    )
    await store.append_event(
        _event(
            2,
            WorkEventType.WORK_BLOCKED,
            {
                "reason": "operator_input_required",
                "decision_request": "Choose the target branch.",
            },
        )
    )
    await store.append_event(
        _event(
            3,
            WorkEventType.CONTROL_DEGRADED,
            {
                "failed_preconditions": ["observability", "workspace"],
                "evidence_refs": ["check://stale"],
            },
        )
    )
    await store.append_event(
        _event(
            4,
            WorkEventType.CONTROL_RESTORED,
            {"precondition_ids": ["workspace"], "evidence_refs": ["check://workspace"]},
        )
    )

    await store.save_work(
        _record(
            work_id="work-other",
            project_id="project-b",
            status="READY_TO_MERGE",
            pending_gate="merge:work-other:8",
        )
    )
    await store.append_event(
        _event(
            1,
            WorkEventType.GATE_REQUESTED,
            {"gate_id": "merge:work-other:8", "question": "Other project gate."},
            work_id="work-other",
            project_id="project-b",
        )
    )

    pending = await store.pending_attention(project_id="project-a")

    assert {(item.kind, item.attention_id) for item in pending} == {
        (PendingAttentionKind.GATE_REQUESTED, "merge:work-1:7"),
        (PendingAttentionKind.WORK_BLOCKED, "work-1-event-2"),
        (PendingAttentionKind.CONTROL_DEGRADED, "observability"),
    }
    gate = next(item for item in pending if item.kind is PendingAttentionKind.GATE_REQUESTED)
    assert gate.source_ref == ISSUE_URL
    assert gate.summary == "Approve merge of PR #7."
    control = next(item for item in pending if item.kind is PendingAttentionKind.CONTROL_DEGRADED)
    assert control.evidence_refs == ("check://stale",)

    await store.append_event(
        _event(
            5,
            WorkEventType.GATE_DECIDED,
            {"gate_id": "merge:work-1:7", "decision": "allow"},
        )
    )
    await store.append_event(
        _event(
            6,
            WorkEventType.CONTROL_RESTORED,
            {
                "precondition_ids": ["observability"],
                "evidence_refs": ["check://fresh"],
            },
        )
    )
    await store.save_work(_record(status="READY_TO_DELIVER", pending_gate=None))

    assert await store.pending_attention(project_id="project-a") == ()
