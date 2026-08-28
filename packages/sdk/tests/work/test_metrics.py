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
