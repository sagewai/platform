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

from datetime import datetime, timedelta, timezone

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
    created_at: datetime = NOW,
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
        created_at=created_at,
    )


def _deployment(
    deployment_id: str,
    *,
    environment: str,
    status: str = "active",
) -> dict:
    return {
        "id": deployment_id,
        "project_id": "project-a",
        "work_id": "work-1",
        "release_candidate_id": "candidate-1",
        "environment": environment,
        "exposure": {"dimension": "traffic", "value": "100%"},
        "provider_ref": f"provider://{deployment_id}",
        "status": status,
    }


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
    assert control.summary == "observability"

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


def test_pending_attention_taxonomy_has_exactly_four_kinds() -> None:
    assert [kind.value for kind in PendingAttentionKind] == [
        "GATE_REQUESTED",
        "WORK_BLOCKED",
        "CONTROL_DEGRADED",
        "PRODUCTION_INCIDENT",
    ]


@pytest.mark.asyncio
async def test_production_fail_and_rollback_are_one_stable_incident(
    store: WorkStore,
) -> None:
    await store.save_work(_record(status="PRODUCTION_ROLLOUT", pending_gate=None))
    await store.append_event(
        _event(
            1,
            WorkEventType.DEPLOYMENT_RECORDED,
            {"deployment": _deployment("production-1", environment="production")},
        )
    )
    await store.append_event(
        _event(
            2,
            WorkEventType.OBSERVATION_RECORDED,
            {
                "observation": {
                    "project_id": "project-a",
                    "work_id": "work-1",
                    "deployment_id": "production-1",
                    "verdict": "fail",
                    "gate_results": [],
                    "evidence_refs": ["metrics://production-fail"],
                }
            },
        )
    )
    await store.append_event(
        _event(
            3,
            WorkEventType.ROLLBACK_RECORDED,
            {
                "source_deployment_id": "production-1",
                "deployment": _deployment(
                    "rollback-1",
                    environment="production",
                    status="rolled_back",
                ),
                "evidence_refs": ["provider://rollback-1"],
            },
            created_at=NOW + timedelta(seconds=1),
        )
    )

    incidents = [
        item
        for item in await store.pending_attention(project_id="project-a")
        if item.kind is PendingAttentionKind.PRODUCTION_INCIDENT
    ]

    assert len(incidents) == 1
    assert incidents[0].attention_id == "work-1-event-2"
    assert incidents[0].created_at == NOW
    assert incidents[0].severity == "high"
    assert incidents[0].summary == "HIGH: production incident for deployment production-1"
    assert incidents[0].evidence_refs == (
        "metrics://production-fail",
        "provider://rollback-1",
    )

    await store.save_work(_record(status="TRIAGING", pending_gate=None))
    assert any(
        item.kind is PendingAttentionKind.PRODUCTION_INCIDENT
        for item in await store.pending_attention(project_id="project-a")
    )
    await store.save_work(_record(status="COMPLETE", pending_gate=None))
    assert all(
        item.kind is not PendingAttentionKind.PRODUCTION_INCIDENT
        for item in await store.pending_attention(project_id="project-a")
    )


@pytest.mark.asyncio
async def test_staging_fail_and_rollback_do_not_interrupt(
    store: WorkStore,
) -> None:
    await store.save_work(_record(status="STAGING", pending_gate=None))
    await store.append_event(
        _event(
            1,
            WorkEventType.DEPLOYMENT_RECORDED,
            {"deployment": _deployment("staging-1", environment="staging")},
        )
    )
    await store.append_event(
        _event(
            2,
            WorkEventType.OBSERVATION_RECORDED,
            {
                "observation": {
                    "project_id": "project-a",
                    "work_id": "work-1",
                    "deployment_id": "staging-1",
                    "verdict": "fail",
                    "gate_results": [],
                    "evidence_refs": ["metrics://staging-fail"],
                }
            },
        )
    )
    await store.append_event(
        _event(
            3,
            WorkEventType.ROLLBACK_RECORDED,
            {
                "source_deployment_id": "staging-1",
                "deployment": _deployment(
                    "staging-rollback-1",
                    environment="staging",
                    status="rolled_back",
                ),
            },
        )
    )

    assert await store.pending_attention(project_id="project-a") == ()


@pytest.mark.asyncio
async def test_critical_rollback_control_loss_upserts_incident_and_suppresses_only_it(
    store: WorkStore,
) -> None:
    await store.save_work(_record(status="CONTROL_DEGRADED", pending_gate=None))
    await store.append_event(
        _event(
            1,
            WorkEventType.DEPLOYMENT_RECORDED,
            {"deployment": _deployment("production-1", environment="production")},
        )
    )
    await store.append_event(
        _event(
            2,
            WorkEventType.CONTROL_DEGRADED,
            {
                "severity": "critical",
                "action": "rollback",
                "deployment_id": "production-1",
                "failed_preconditions": ["rollback-authority"],
                "details": "rollback credential expired",
                "evidence_refs": ["check://rollback-authority"],
                "frozen_action_ids": ["rollback"],
            },
        )
    )
    await store.append_event(
        _event(
            3,
            WorkEventType.CONTROL_DEGRADED,
            {
                "failed_preconditions": ["observability"],
                "details": "metrics stale",
                "evidence_refs": ["check://observability"],
                "frozen_action_ids": ["promote"],
            },
            created_at=NOW + timedelta(seconds=1),
        )
    )
    await store.append_event(
        _event(
            4,
            WorkEventType.ROLLBACK_RECORDED,
            {
                "source_deployment_id": "production-1",
                "deployment": _deployment(
                    "rollback-1",
                    environment="production",
                    status="rolled_back",
                ),
            },
            created_at=NOW + timedelta(seconds=2),
        )
    )

    pending = await store.pending_attention(project_id="project-a")

    assert [(item.kind, item.attention_id) for item in pending] == [
        (PendingAttentionKind.PRODUCTION_INCIDENT, "work-1-event-2"),
        (PendingAttentionKind.CONTROL_DEGRADED, "observability"),
    ]
    incident = pending[0]
    assert incident.severity == "critical"
    assert incident.summary == (
        "CRITICAL: production incident for deployment production-1; "
        "failed preconditions: rollback-authority; "
        "details: rollback credential expired"
    )
    assert incident.evidence_refs == ("check://rollback-authority",)

    await store.append_event(
        _event(
            5,
            WorkEventType.CONTROL_RESTORED,
            {
                "precondition_ids": ["rollback-authority"],
                "evidence_refs": ["check://rollback-authority-restored"],
            },
            created_at=NOW + timedelta(seconds=3),
        )
    )
    after_restore = await store.pending_attention(project_id="project-a")
    restored_incident = next(
        item
        for item in after_restore
        if item.kind is PendingAttentionKind.PRODUCTION_INCIDENT
    )
    assert restored_incident.attention_id == incident.attention_id
    assert restored_incident.created_at == incident.created_at
    assert restored_incident.severity == "high"
    assert restored_incident.evidence_refs == incident.evidence_refs
    assert restored_incident.summary.startswith("HIGH: production incident")

    await store.append_event(
        _event(
            6,
            WorkEventType.CONTROL_DEGRADED,
            {
                "severity": "high",
                "action": "deploy",
                "failed_preconditions": ["rollback-authority"],
                "details": "unrelated staging authority failure",
                "evidence_refs": ["check://staging-authority"],
                "frozen_action_ids": ["deploy"],
            },
            created_at=NOW + timedelta(seconds=4),
        )
    )
    with_unrelated_degradation = await store.pending_attention(project_id="project-a")
    unrelated_incident = next(
        item
        for item in with_unrelated_degradation
        if item.kind is PendingAttentionKind.PRODUCTION_INCIDENT
    )
    assert unrelated_incident.severity == "high"
    assert any(
        item.kind is PendingAttentionKind.CONTROL_DEGRADED
        and item.attention_id == "rollback-authority"
        for item in with_unrelated_degradation
    )
