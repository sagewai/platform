# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Read-only discipline and control metrics derived from Work events."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from sagewai.work.events import WorkEvent, WorkEventType


class WorkMetrics(BaseModel):
    """Immutable event projection for one project or WorkItem.

    ``control_degradation_rate`` is Works with at least one degradation divided
    by Works represented in the selected stream. ``scope_violation_rate`` is
    discipline reports containing at least one scope violation divided by all
    discipline reports. ``repair_rate`` is Works that started a repair divided
    by Works that started implementation. ``rollback_rate`` is successful
    rollback records divided by initial delivery deployment records; progressive
    ``promote_rollout`` records are excluded from the denominator.

    Mean restoration time is calculated per restored control precondition.
    Degradations that remain active at the end of the stream are excluded.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    work_id: str | None
    control_degradation_rate: float
    mean_time_to_control_restored_seconds: float | None
    scope_violation_rate: float
    repair_rate: float
    rollback_rate: float


def derive_work_metrics(
    events: Iterable[WorkEvent],
    *,
    project_id: str | None,
    work_id: str | None = None,
) -> WorkMetrics:
    """Project the selected project/WorkItem's metrics without mutating state."""

    selected = [
        event
        for event in events
        if event.project_id == project_id and (work_id is None or event.work_id == work_id)
    ]
    by_work: dict[str, list[WorkEvent]] = defaultdict(list)
    for event in selected:
        by_work[event.work_id].append(event)

    degraded_works: set[str] = set()
    implemented_works: set[str] = set()
    repaired_works: set[str] = set()
    discipline_reports = 0
    scope_violation_reports = 0
    deployments = 0
    rollbacks = 0
    restoration_seconds: list[float] = []

    for selected_work_id, work_events in by_work.items():
        active_degradations: dict[str, datetime] = {}
        for event in sorted(work_events, key=lambda item: item.sequence):
            if event.event_type is WorkEventType.CONTROL_DEGRADED:
                degraded_works.add(selected_work_id)
                for precondition_id in event.payload_json.get("failed_preconditions", ()):
                    active_degradations[str(precondition_id)] = event.created_at
            elif event.event_type is WorkEventType.CONTROL_RESTORED:
                for precondition_id in event.payload_json.get("precondition_ids", ()):
                    degraded_at = active_degradations.pop(str(precondition_id), None)
                    if degraded_at is None:
                        continue
                    duration = (event.created_at - degraded_at).total_seconds()
                    restoration_seconds.append(duration)
            elif event.event_type is WorkEventType.OPERATOR_DISCIPLINE_RECORDED:
                discipline_reports += 1
                if event.payload_json.get("scope_violations"):
                    scope_violation_reports += 1
            elif event.event_type is WorkEventType.STAGE_STARTED:
                stage = event.payload_json.get("stage")
                if stage == "implement":
                    implemented_works.add(selected_work_id)
                elif stage == "repair":
                    repaired_works.add(selected_work_id)
            elif (
                event.event_type is WorkEventType.DEPLOYMENT_RECORDED
                and event.payload_json.get("action") != "promote_rollout"
            ):
                deployments += 1
            elif event.event_type is WorkEventType.ROLLBACK_RECORDED:
                rollbacks += 1

    observed_works = len(by_work)
    repair_works = len(implemented_works & repaired_works)
    mean_restoration = (
        sum(restoration_seconds) / len(restoration_seconds) if restoration_seconds else None
    )
    return WorkMetrics(
        project_id=project_id,
        work_id=work_id,
        control_degradation_rate=_rate(len(degraded_works), observed_works),
        mean_time_to_control_restored_seconds=mean_restoration,
        scope_violation_rate=_rate(scope_violation_reports, discipline_reports),
        repair_rate=_rate(repair_works, len(implemented_works)),
        rollback_rate=_rate(rollbacks, deployments),
    )


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
