# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Deterministic Work-event metrics projection tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.metrics import derive_work_metrics
from sagewai.work.store import WorkStore
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _event(
    work_id: str,
    sequence: int,
    event_type: WorkEventType,
    *,
    project_id: str | None = "project-a",
    seconds: int = 0,
    payload: dict | None = None,
) -> WorkEvent:
    return WorkEvent(
        id=f"{project_id}:{work_id}:{sequence}",
        project_id=project_id,
        work_id=work_id,
        sequence=sequence,
        event_type=event_type,
        actor_type="system",
        actor_ref=None,
        payload_json=payload or {},
        created_at=NOW + timedelta(seconds=seconds),
    )


@pytest.mark.asyncio
async def test_store_queries_project_metrics_from_synthetic_event_stream(
    dialect_engine,  # noqa: F811
) -> None:
    events = (
        _event("work-1", 1, WorkEventType.WORK_CREATED),
        _event(
            "work-1",
            2,
            WorkEventType.STAGE_STARTED,
            payload={"stage": "implement"},
        ),
        _event(
            "work-1",
            3,
            WorkEventType.OPERATOR_DISCIPLINE_RECORDED,
            payload={"scope_violations": []},
        ),
        _event(
            "work-1",
            4,
            WorkEventType.DEPLOYMENT_RECORDED,
            payload={"action": "deploy_production"},
        ),
        _event(
            "work-1",
            5,
            WorkEventType.DEPLOYMENT_RECORDED,
            payload={"action": "promote_rollout"},
        ),
        _event(
            "work-1",
            6,
            WorkEventType.CONTROL_DEGRADED,
            seconds=10,
            payload={"failed_preconditions": ["authority", "observability"]},
        ),
        _event(
            "work-1",
            7,
            WorkEventType.CONTROL_RESTORED,
            seconds=40,
            payload={"precondition_ids": ["authority"]},
        ),
        _event(
            "work-1",
            8,
            WorkEventType.CONTROL_RESTORED,
            seconds=70,
            payload={"precondition_ids": ["observability"]},
        ),
        _event(
            "work-1",
            9,
            WorkEventType.STAGE_STARTED,
            payload={"stage": "repair"},
        ),
        _event("work-1", 10, WorkEventType.ROLLBACK_RECORDED),
        _event("work-2", 1, WorkEventType.WORK_CREATED),
        _event(
            "work-2",
            2,
            WorkEventType.STAGE_STARTED,
            payload={"stage": "implement"},
        ),
        _event(
            "work-2",
            3,
            WorkEventType.OPERATOR_DISCIPLINE_RECORDED,
            payload={"scope_violations": ["outside.txt"]},
        ),
        _event(
            "work-2",
            4,
            WorkEventType.DEPLOYMENT_RECORDED,
            payload={"action": "deploy_staging"},
        ),
    )

    store = WorkStore(engine=dialect_engine)
    await store.init()
    for event in events:
        await store.append_event(event)

    metrics = await store.metrics(project_id="project-a")

    assert metrics.control_degradation_rate == 0.5
    assert metrics.mean_time_to_control_restored_seconds == 45.0
    assert metrics.scope_violation_rate == 0.5
    assert metrics.repair_rate == 0.5
    assert metrics.rollback_rate == 0.5


@pytest.mark.asyncio
async def test_store_exposes_profile_and_runtime_dimensions(
    dialect_engine,  # noqa: F811
) -> None:
    events = (
        _event(
            "work-1",
            1,
            WorkEventType.WORK_CREATED,
            payload={"profile": "software"},
        ),
        _event(
            "work-1",
            2,
            WorkEventType.STAGE_STARTED,
            payload={
                "stage": "implement",
                "run_id": "implement-1",
                "runtime": "codex",
                "knowledge_items_considered": 2,
                "knowledge_items_selected": 1,
                "artifact_bytes_referenced": 10,
            },
        ),
        _event(
            "work-2",
            1,
            WorkEventType.WORK_CREATED,
            payload={"profile": "research"},
        ),
    )
    store = WorkStore(engine=dialect_engine)
    await store.init()
    for event in events:
        await store.append_event(event)

    metrics = await store.metrics(
        project_id="project-a",
        profile="software",
        runtime="codex",
    )

    assert metrics.profile == "software"
    assert metrics.runtime == "codex"
    assert metrics.knowledge_items_considered == 2
    assert metrics.knowledge_items_selected == 1
    assert metrics.artifact_bytes_referenced == 10


def test_filters_by_exact_project_and_optional_work() -> None:
    events = (
        _event("work-1", 1, WorkEventType.WORK_CREATED),
        _event(
            "work-1",
            2,
            WorkEventType.CONTROL_DEGRADED,
            payload={"failed_preconditions": ["authority"]},
        ),
        _event("work-2", 1, WorkEventType.WORK_CREATED),
        _event(
            "work-b",
            1,
            WorkEventType.WORK_CREATED,
            project_id="project-b",
        ),
        _event(
            "work-b",
            2,
            WorkEventType.CONTROL_DEGRADED,
            project_id="project-b",
            payload={"failed_preconditions": ["authority"]},
        ),
    )

    project_metrics = derive_work_metrics(events, project_id="project-a")
    work_metrics = derive_work_metrics(
        events,
        project_id="project-a",
        work_id="work-2",
    )

    assert project_metrics.control_degradation_rate == 0.5
    assert work_metrics.control_degradation_rate == 0.0


def test_excludes_unrestored_control_incidents_and_is_immutable() -> None:
    events = (
        _event("work-1", 1, WorkEventType.WORK_CREATED),
        _event(
            "work-1",
            2,
            WorkEventType.CONTROL_DEGRADED,
            seconds=10,
            payload={"failed_preconditions": ["authority", "observability"]},
        ),
        _event(
            "work-1",
            3,
            WorkEventType.CONTROL_RESTORED,
            seconds=25,
            payload={"precondition_ids": ["authority"]},
        ),
    )

    metrics = derive_work_metrics(reversed(events), project_id="project-a")

    assert metrics.mean_time_to_control_restored_seconds == 15.0
    with pytest.raises(ValidationError):
        metrics.rollback_rate = 1.0


def test_derives_named_operator_metrics_only_from_canonical_outcomes() -> None:
    events = (
        _event(
            "work-1",
            1,
            WorkEventType.WORK_CREATED,
            payload={"profile": "software"},
        ),
        _event(
            "work-1",
            2,
            WorkEventType.STAGE_STARTED,
            payload={
                "stage": "implement",
                "run_id": "implement-1",
                "runtime": "codex",
                "knowledge_items_considered": 4,
                "knowledge_items_selected": 2,
                "artifact_bytes_referenced": 100,
            },
        ),
        _event(
            "work-1",
            3,
            WorkEventType.OPERATOR_DISCIPLINE_RECORDED,
            payload={
                "run_id": "implement-1",
                "unsupported_claims": [],
                "scope_violations": [],
                "permission_violations": [],
                "risk_mismatches": [],
                "changed_files": 2,
                "diff_lines": 20,
            },
        ),
        _event(
            "work-1",
            4,
            WorkEventType.STAGE_COMPLETED,
            payload={"stage": "implement", "run_id": "implement-1"},
        ),
        _event(
            "work-1",
            5,
            WorkEventType.REVIEW_RECORDED,
            payload={
                "attempt_id": "review-1",
                "verdict": "accept",
                "unsupported_claims": [],
            },
        ),
        _event(
            "work-1",
            6,
            WorkEventType.CONTROL_DEGRADED,
            seconds=10,
            payload={
                "run_id": "implement-1",
                "failed_preconditions": ["observability"],
            },
        ),
        _event(
            "work-1",
            7,
            WorkEventType.CONTROL_DEGRADED,
            seconds=20,
            payload={
                "run_id": "implement-1",
                "failed_preconditions": ["authority"],
            },
        ),
        _event(
            "work-1",
            8,
            WorkEventType.CONTROL_RESTORED,
            seconds=30,
            payload={"precondition_ids": ["observability"]},
        ),
        _event(
            "work-1",
            9,
            WorkEventType.CONTROL_RESTORED,
            seconds=40,
            payload={"precondition_ids": ["authority"]},
        ),
        _event(
            "work-2",
            1,
            WorkEventType.WORK_CREATED,
            payload={"profile": "software"},
        ),
        _event(
            "work-2",
            2,
            WorkEventType.STAGE_STARTED,
            payload={
                "stage": "implement",
                "run_id": "implement-2",
                "runtime": "claude",
                "knowledge_items_considered": 3,
                "knowledge_items_selected": 0,
                "artifact_bytes_referenced": 50,
            },
        ),
        _event(
            "work-2",
            3,
            WorkEventType.OPERATOR_DISCIPLINE_RECORDED,
            payload={
                "run_id": "implement-2",
                "unsupported_claims": [],
                "scope_violations": [],
                "permission_violations": ["permission"],
                "risk_mismatches": ["risk"],
                "changed_files": None,
                "diff_lines": None,
            },
        ),
        _event(
            "work-2",
            4,
            WorkEventType.REVIEW_RECORDED,
            payload={
                "attempt_id": "review-2",
                "verdict": "repair",
                "unsupported_claims": ["unsupported"],
            },
        ),
        _event(
            "other-profile",
            1,
            WorkEventType.WORK_CREATED,
            payload={"profile": "research"},
        ),
        _event(
            "other-profile",
            2,
            WorkEventType.STAGE_STARTED,
            payload={
                "stage": "execute",
                "run_id": "execute-1",
                "runtime": "codex",
                "knowledge_items_considered": 99,
                "knowledge_items_selected": 99,
                "artifact_bytes_referenced": 99,
            },
        ),
        _event(
            "other-profile",
            3,
            WorkEventType.REVIEW_RECORDED,
            payload={
                "attempt_id": "review-3",
                "verdict": "repair",
                "unsupported_claims": ["other-profile-claim"],
            },
        ),
    )

    metrics = derive_work_metrics(
        events,
        project_id="project-a",
        profile="software",
    )
    codex_metrics = derive_work_metrics(
        events,
        project_id="project-a",
        profile="software",
        runtime="codex",
    )
    work_metrics = derive_work_metrics(
        events,
        project_id="project-a",
        work_id="work-1",
        profile="software",
    )

    assert metrics.profile == "software"
    assert metrics.runtime is None
    assert metrics.knowledge_items_considered == 7
    assert metrics.knowledge_items_selected == 2
    assert metrics.artifact_bytes_referenced == 150
    assert metrics.task_capsule_tokens is None
    assert metrics.retrieval_hit_rate is None
    assert metrics.unsupported_claim_rate == 0.5
    assert metrics.risk_classification_accuracy is None
    assert metrics.permission_escalation_accuracy is None
    assert metrics.mean_changed_files_per_accepted_work_item == 2.0
    assert metrics.mean_diff_lines_per_accepted_change == 20.0
    assert metrics.mean_time_to_control_restored_seconds == 20.0
    assert metrics.mean_blind_window_seconds == 30.0
    assert metrics.missing_context_repair_rate is None
    assert metrics.false_positive_blocked_rate is None
    assert metrics.verbosity_output_token_ratio is None

    assert codex_metrics.runtime == "codex"
    assert codex_metrics.knowledge_items_considered == 4
    assert codex_metrics.knowledge_items_selected == 2
    assert codex_metrics.artifact_bytes_referenced == 100
    assert codex_metrics.task_capsule_tokens is None
    assert codex_metrics.retrieval_hit_rate is None
    assert codex_metrics.unsupported_claim_rate is None
    assert codex_metrics.risk_classification_accuracy is None
    assert codex_metrics.permission_escalation_accuracy is None
    assert codex_metrics.mean_changed_files_per_accepted_work_item == 2.0
    assert codex_metrics.mean_diff_lines_per_accepted_change == 20.0
    assert codex_metrics.mean_blind_window_seconds == 30.0

    assert work_metrics.work_id == "work-1"
    assert work_metrics.knowledge_items_considered == 4
    assert work_metrics.unsupported_claim_rate == 0.0


def test_acceptance_means_distinguish_work_items_from_changes() -> None:
    events = (
        _event("work-1", 1, WorkEventType.WORK_CREATED, payload={"profile": "software"}),
        _event(
            "work-1",
            2,
            WorkEventType.STAGE_STARTED,
            payload={
                "stage": "implement",
                "run_id": "implement-1",
                "runtime": "codex",
                "knowledge_items_considered": 0,
                "knowledge_items_selected": 0,
                "artifact_bytes_referenced": 0,
            },
        ),
        _event(
            "work-1",
            3,
            WorkEventType.OPERATOR_DISCIPLINE_RECORDED,
            payload={"run_id": "implement-1", "changed_files": 2, "diff_lines": 10},
        ),
        _event(
            "work-1",
            4,
            WorkEventType.STAGE_COMPLETED,
            payload={"stage": "implement", "run_id": "implement-1"},
        ),
        _event(
            "work-1",
            5,
            WorkEventType.REVIEW_RECORDED,
            payload={"verdict": "accept", "unsupported_claims": []},
        ),
        _event(
            "work-1",
            6,
            WorkEventType.STAGE_STARTED,
            payload={
                "stage": "repair",
                "run_id": "repair-1",
                "runtime": "claude",
                "knowledge_items_considered": 0,
                "knowledge_items_selected": 0,
                "artifact_bytes_referenced": 0,
            },
        ),
        _event(
            "work-1",
            7,
            WorkEventType.OPERATOR_DISCIPLINE_RECORDED,
            payload={"run_id": "repair-1", "changed_files": 3, "diff_lines": 30},
        ),
        _event(
            "work-1",
            8,
            WorkEventType.STAGE_COMPLETED,
            payload={"stage": "repair", "run_id": "repair-1"},
        ),
        _event(
            "work-1",
            9,
            WorkEventType.REVIEW_RECORDED,
            payload={"verdict": "accept", "unsupported_claims": []},
        ),
    )

    metrics = derive_work_metrics(events, project_id="project-a")

    assert metrics.mean_changed_files_per_accepted_work_item == 3.0
    assert metrics.mean_diff_lines_per_accepted_change == 20.0


def test_repair_rate_is_attributed_to_the_implementation_runtime() -> None:
    events = (
        _event("work-1", 1, WorkEventType.WORK_CREATED, payload={"profile": "software"}),
        _event(
            "work-1",
            2,
            WorkEventType.STAGE_STARTED,
            payload={"stage": "implement", "run_id": "implement-1", "runtime": "codex"},
        ),
        _event(
            "work-1",
            3,
            WorkEventType.STAGE_STARTED,
            payload={"stage": "repair", "run_id": "repair-1", "runtime": "claude"},
        ),
    )

    codex_metrics = derive_work_metrics(events, project_id="project-a", runtime="codex")
    claude_metrics = derive_work_metrics(events, project_id="project-a", runtime="claude")

    assert codex_metrics.repair_rate == 1.0
    assert claude_metrics.repair_rate is None


def test_unknown_metric_denominators_remain_unavailable() -> None:
    metrics = derive_work_metrics(
        (_event("work-1", 1, WorkEventType.WORK_CREATED),),
        project_id="project-a",
    )

    assert metrics.knowledge_items_considered is None
    assert metrics.knowledge_items_selected is None
    assert metrics.artifact_bytes_referenced is None
    assert metrics.task_capsule_tokens is None
    assert metrics.retrieval_hit_rate is None
    assert metrics.unsupported_claim_rate is None
    assert metrics.risk_classification_accuracy is None
    assert metrics.permission_escalation_accuracy is None
    assert metrics.mean_changed_files_per_accepted_work_item is None
    assert metrics.mean_diff_lines_per_accepted_change is None
    assert metrics.missing_context_repair_rate is None
    assert metrics.false_positive_blocked_rate is None
    assert metrics.verbosity_output_token_ratio is None
    assert metrics.mean_blind_window_seconds is None
    assert metrics.scope_violation_rate is None
    assert metrics.repair_rate is None
    assert metrics.rollback_rate is None
