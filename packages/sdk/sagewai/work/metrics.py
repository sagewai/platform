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

    Unsupported-claim rate is accepted/repair/blocked reviews reporting at least
    one unsupported claim divided by reviews carrying that required semantic
    answer. It is unavailable for a runtime slice because ``ReviewResult`` does
    not identify the operator attempt whose claims were reviewed. Changed-file
    and diff means use the implementation or repair attempt immediately before
    an accepted review.

    Mean restoration time is calculated per restored control precondition. A
    blind window is the Work-wide union from no active control degradation to
    all active preconditions restored. Active windows are excluded from both
    means rather than being timed against the query clock.

    Knowledge-item and artifact fields sum complete ``STAGE_STARTED`` capsule
    measurements in the selected slice. TaskCapsule tokens, retrieval relevance,
    missing-context repair cause, risk/permission accuracy, false-positive
    blocking, and verbosity are not adjudicated by the current canonical event
    vocabulary, so those metrics remain ``None`` instead of using a proxy.

    ``profile`` filters WorkItems using their canonical ``WORK_CREATED`` payload.
    ``runtime`` filters only events attributable through a canonical run ID;
    rollback rate is unavailable for a runtime slice because delivery receipts
    do not identify an operator runtime.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    work_id: str | None
    profile: str | None = None
    runtime: str | None = None
    control_degradation_rate: float | None
    mean_time_to_control_restored_seconds: float | None
    scope_violation_rate: float | None
    repair_rate: float | None
    rollback_rate: float | None
    knowledge_items_considered: int | None = None
    knowledge_items_selected: int | None = None
    artifact_bytes_referenced: int | None = None
    task_capsule_tokens: int | None = None
    retrieval_hit_rate: float | None = None
    missing_context_repair_rate: float | None = None
    unsupported_claim_rate: float | None = None
    risk_classification_accuracy: float | None = None
    permission_escalation_accuracy: float | None = None
    mean_changed_files_per_accepted_work_item: float | None = None
    mean_diff_lines_per_accepted_change: float | None = None
    false_positive_blocked_rate: float | None = None
    verbosity_output_token_ratio: float | None = None
    mean_blind_window_seconds: float | None = None


def derive_work_metrics(
    events: Iterable[WorkEvent],
    *,
    project_id: str | None,
    work_id: str | None = None,
    profile: str | None = None,
    runtime: str | None = None,
) -> WorkMetrics:
    """Project scoped metrics from canonical events without mutating state."""

    selected = [
        event
        for event in events
        if event.project_id == project_id and (work_id is None or event.work_id == work_id)
    ]
    if profile is not None:
        profile_work_ids = {
            event.work_id
            for event in selected
            if event.event_type is WorkEventType.WORK_CREATED
            and event.payload_json.get("profile") == profile
        }
        selected = [event for event in selected if event.work_id in profile_work_ids]

    by_work: dict[str, list[WorkEvent]] = defaultdict(list)
    for event in selected:
        by_work[event.work_id].append(event)

    run_runtimes: dict[tuple[str, str], str] = {}
    for event in selected:
        if event.event_type is not WorkEventType.STAGE_STARTED:
            continue
        run_id = event.payload_json.get("run_id")
        event_runtime = event.payload_json.get("runtime")
        if isinstance(run_id, str) and isinstance(event_runtime, str):
            run_runtimes[(event.work_id, run_id)] = event_runtime

    runtime_works = {
        selected_work_id
        for (selected_work_id, _), event_runtime in run_runtimes.items()
        if event_runtime == runtime
    }
    observed_works = len(runtime_works) if runtime is not None else len(by_work)

    degraded_works: set[str] = set()
    implemented_works: set[str] = set()
    repaired_works: set[str] = set()
    discipline_reports = 0
    scope_violation_reports = 0
    review_checks = 0
    unsupported_claim_reviews = 0
    deployments = 0
    rollbacks = 0
    restoration_seconds: list[float] = []
    blind_window_seconds: list[float] = []
    accepted_reports: list[dict | None] = []
    considered_values: list[int | None] = []
    selected_values: list[int | None] = []
    artifact_byte_values: list[int | None] = []

    for selected_work_id, work_events in by_work.items():
        ordered = sorted(work_events, key=lambda item: item.sequence)
        active_degradations: dict[str, datetime] = {}
        blind_window_started_at: datetime | None = None
        reports_by_run: dict[str, dict] = {}
        latest_change_run_id: str | None = None
        accepted_change_run_id: str | None = None

        for event in ordered:
            payload = event.payload_json
            event_run_id = payload.get("run_id")
            event_runtime = (
                run_runtimes.get((selected_work_id, event_run_id))
                if isinstance(event_run_id, str)
                else None
            )
            runtime_matches = runtime is None or event_runtime == runtime

            if event.event_type is WorkEventType.CONTROL_DEGRADED and runtime_matches:
                failed_ids = tuple(str(item) for item in payload.get("failed_preconditions", ()))
                if failed_ids:
                    degraded_works.add(selected_work_id)
                    if not active_degradations:
                        blind_window_started_at = event.created_at
                    for precondition_id in failed_ids:
                        active_degradations[precondition_id] = event.created_at
            elif event.event_type is WorkEventType.CONTROL_RESTORED:
                for precondition_id in payload.get("precondition_ids", ()):
                    degraded_at = active_degradations.pop(str(precondition_id), None)
                    if degraded_at is not None:
                        restoration_seconds.append(
                            (event.created_at - degraded_at).total_seconds()
                        )
                if not active_degradations and blind_window_started_at is not None:
                    blind_window_seconds.append(
                        (event.created_at - blind_window_started_at).total_seconds()
                    )
                    blind_window_started_at = None
            elif event.event_type is WorkEventType.OPERATOR_DISCIPLINE_RECORDED:
                if not runtime_matches:
                    continue
                discipline_reports += 1
                if payload.get("scope_violations"):
                    scope_violation_reports += 1
                if isinstance(event_run_id, str):
                    reports_by_run[event_run_id] = payload
            elif event.event_type is WorkEventType.STAGE_STARTED and runtime_matches:
                stage = payload.get("stage")
                if stage == "implement":
                    implemented_works.add(selected_work_id)
                elif stage == "repair":
                    repaired_works.add(selected_work_id)
                considered_values.append(_nonnegative_int(payload.get("knowledge_items_considered")))
                selected_values.append(_nonnegative_int(payload.get("knowledge_items_selected")))
                artifact_byte_values.append(
                    _nonnegative_int(payload.get("artifact_bytes_referenced"))
                )
            elif (
                runtime is None
                and event.event_type is WorkEventType.DEPLOYMENT_RECORDED
                and payload.get("action") != "promote_rollout"
            ):
                deployments += 1
            elif runtime is None and event.event_type is WorkEventType.ROLLBACK_RECORDED:
                rollbacks += 1

            if event.event_type is WorkEventType.REVIEW_RECORDED:
                if runtime is None and "unsupported_claims" in payload:
                    review_checks += 1
                    if payload.get("unsupported_claims"):
                        unsupported_claim_reviews += 1
                if payload.get("verdict") == "accept" and latest_change_run_id is not None:
                    accepted_change_run_id = latest_change_run_id
            elif (
                event.event_type is WorkEventType.STAGE_COMPLETED
                and payload.get("stage") in {"implement", "repair"}
                and isinstance(event_run_id, str)
            ):
                latest_change_run_id = event_run_id

        if accepted_change_run_id is not None and (
            runtime is None
            or run_runtimes.get((selected_work_id, accepted_change_run_id)) == runtime
        ):
            accepted_reports.append(reports_by_run.get(accepted_change_run_id))

    repair_works = len(implemented_works & repaired_works)
    control_rate = _optional_rate(len(degraded_works), observed_works)
    repair_rate = _optional_rate(repair_works, len(implemented_works))
    return WorkMetrics(
        project_id=project_id,
        work_id=work_id,
        profile=profile,
        runtime=runtime,
        control_degradation_rate=control_rate,
        mean_time_to_control_restored_seconds=_mean(restoration_seconds),
        scope_violation_rate=_optional_rate(
            scope_violation_reports,
            discipline_reports,
        ),
        repair_rate=repair_rate,
        rollback_rate=None if runtime is not None else _optional_rate(rollbacks, deployments),
        knowledge_items_considered=_complete_int_sum(considered_values),
        knowledge_items_selected=_complete_int_sum(selected_values),
        artifact_bytes_referenced=_complete_int_sum(artifact_byte_values),
        unsupported_claim_rate=_optional_rate(
            unsupported_claim_reviews,
            review_checks,
        ),
        mean_changed_files_per_accepted_work_item=_complete_report_mean(
            accepted_reports,
            "changed_files",
        ),
        mean_diff_lines_per_accepted_change=_complete_report_mean(
            accepted_reports,
            "diff_lines",
        ),
        mean_blind_window_seconds=_mean(blind_window_seconds),
    )


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _complete_int_sum(values: list[int | None]) -> int | None:
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _complete_report_mean(reports: list[dict | None], field: str) -> float | None:
    if not reports:
        return None
    values: list[float] = []
    for report in reports:
        value = report.get(field) if report is not None else None
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        values.append(float(value))
    return _mean(values)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _optional_rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None
