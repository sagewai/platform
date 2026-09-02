# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for canonical execution-attempt projections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sagewai.work import (
    WorkEvent,
    WorkEventType,
    execution_attempt_from_events,
    next_stage_run,
    stage_run_ids,
    stage_runtime_failures,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
RUN_ID = "work-1:implement:1"


def _event(
    sequence: int,
    event_type: WorkEventType,
    payload: dict,
    *,
    project_id: str = "project-a",
    work_id: str = "work-1",
) -> WorkEvent:
    return WorkEvent(
        id=f"{project_id}:{work_id}:{sequence}:{event_type.value}",
        project_id=project_id,
        work_id=work_id,
        sequence=sequence,
        event_type=event_type,
        actor_type="test",
        actor_ref=None,
        payload_json=payload,
        created_at=NOW + timedelta(seconds=sequence),
    )


def _started(
    sequence: int = 1,
    *,
    runtime: str = "codex",
    workspace_ref: str = "workspace://first",
    project_id: str = "project-a",
    work_id: str = "work-1",
) -> WorkEvent:
    return _event(
        sequence,
        WorkEventType.STAGE_STARTED,
        {
            "run_id": RUN_ID,
            "stage": "implement",
            "runtime": runtime,
            "workspace_ref": workspace_ref,
        },
        project_id=project_id,
        work_id=work_id,
    )


@pytest.mark.parametrize(
    ("terminal_type", "terminal_status", "expected_status"),
    [
        (None, None, "running"),
        (WorkEventType.EXECUTION_RECORDED, "passed", "passed"),
        (WorkEventType.EXECUTION_RECORDED, "failed", "failed"),
        (WorkEventType.EXECUTION_RECORDED, "blocked", "blocked"),
        (WorkEventType.CONTROL_DEGRADED, None, "blocked"),
    ],
)
def test_execution_attempt_projects_each_runtime_status(
    terminal_type: WorkEventType | None,
    terminal_status: str | None,
    expected_status: str,
) -> None:
    events = [_started()]
    if terminal_type is WorkEventType.EXECUTION_RECORDED:
        events.append(
            _event(
                2,
                terminal_type,
                {
                    "run_id": RUN_ID,
                    "status": terminal_status,
                    "artifact_refs": ["artifact://result"],
                    "profile_context": {"opaque": True},
                },
            )
        )
    elif terminal_type is WorkEventType.CONTROL_DEGRADED:
        events.append(
            _event(
                2,
                terminal_type,
                {"run_id": RUN_ID, "failed_preconditions": ["workspace"]},
            )
        )

    attempt = execution_attempt_from_events(events, RUN_ID)

    assert attempt is not None
    assert attempt.status == expected_status
    assert attempt.completed_at == (None if terminal_type is None else events[-1].created_at)
    assert attempt.artifact_refs == (
        ("artifact://result",)
        if terminal_type is WorkEventType.EXECUTION_RECORDED
        else ()
    )


def test_execution_attempt_folds_restoration_and_resumed_start_in_sequence() -> None:
    first = _started()
    resumed = _started(
        3, runtime="replacement-codex", workspace_ref="workspace://resumed"
    )
    events = [
        _event(
            5,
            WorkEventType.CONTROL_RESTORED,
            {"run_id": RUN_ID, "precondition_ids": ["workspace"]},
        ),
        _event(
            4,
            WorkEventType.CONTROL_DEGRADED,
            {"run_id": RUN_ID, "failed_preconditions": ["workspace"]},
        ),
        resumed,
        _event(
            2,
            WorkEventType.CONTROL_DEGRADED,
            {"run_id": RUN_ID, "failed_preconditions": ["workspace"]},
        ),
        first,
    ]

    attempt = execution_attempt_from_events(events, RUN_ID)

    assert attempt is not None
    assert attempt.status == "running"
    assert attempt.runtime == "replacement-codex"
    assert attempt.workspace_ref == "workspace://resumed"
    assert attempt.started_at == resumed.created_at
    assert attempt.completed_at is None


def test_execution_attempt_ignores_same_run_from_another_project_and_work() -> None:
    events = [
        _started(project_id="project-b", work_id="work-other"),
        _started(2),
        _event(
            3,
            WorkEventType.EXECUTION_RECORDED,
            {"run_id": RUN_ID, "status": "failed", "artifact_refs": []},
            project_id="project-b",
            work_id="work-other",
        ),
        _event(
            3,
            WorkEventType.EXECUTION_RECORDED,
            {"run_id": RUN_ID, "status": "passed", "artifact_refs": []},
        ),
    ]

    attempt = execution_attempt_from_events(events, RUN_ID)

    assert attempt is not None
    assert attempt.project_id == "project-a"
    assert attempt.work_id == "work-1"
    assert attempt.status == "passed"


def test_new_event_types_exist() -> None:
    assert WorkEventType.WORK_SUPERSEDED.value == "WORK_SUPERSEDED"
    assert WorkEventType.RUNTIME_SELECTED.value == "RUNTIME_SELECTED"
    assert WorkEventType.BASE_MOVED.value == "BASE_MOVED"


def test_next_stage_run_reuses_an_unfinished_run_and_counts_failures() -> None:
    events = [
        _event(
            1,
            WorkEventType.STAGE_STARTED,
            {"stage": "implement", "run_id": "w:implement:1", "runtime": "codex"},
            project_id="p",
            work_id="w",
        ),
    ]
    assert next_stage_run(events, "w", "implement") == ("w:implement:1", 1)
    events.append(
        _event(
            2,
            WorkEventType.EXECUTION_RECORDED,
            {"run_id": "w:implement:1", "status": "failed"},
            project_id="p",
            work_id="w",
        )
    )
    assert next_stage_run(events, "w", "implement") == ("w:implement:2", 2)
    assert stage_run_ids(events, "w", "implement") == ["w:implement:1"]
    assert stage_runtime_failures(events, "w", "implement") == 1
    events.append(
        _event(
            3,
            WorkEventType.STAGE_STARTED,
            {"stage": "implement", "run_id": "w:implement:2", "runtime": "codex"},
            project_id="p",
            work_id="w",
        )
    )
    events.append(
        _event(
            4,
            WorkEventType.EXECUTION_RECORDED,
            {"run_id": "w:implement:2", "status": "blocked"},
            project_id="p",
            work_id="w",
        )
    )
    assert stage_runtime_failures(events, "w", "implement") == 1
    assert next_stage_run(events, "w", "implement") == ("w:implement:3", 3)
    assert next_stage_run(events, "w", "review") == ("w:review:1", 1)
    events.append(
        _event(
            5,
            WorkEventType.STAGE_STARTED,
            {"stage": "implement", "run_id": "w:implement:3", "runtime": "codex"},
            project_id="p",
            work_id="w",
        )
    )
    events.append(
        _event(
            6,
            WorkEventType.EXECUTION_RECORDED,
            {"run_id": "w:implement:3", "status": "passed"},
            project_id="p",
            work_id="w",
        )
    )
    assert next_stage_run(events, "w", "implement") == ("w:implement:3", 3)
    events.append(
        _event(
            7,
            WorkEventType.STAGE_COMPLETED,
            {"stage": "implement", "run_id": "w:implement:3"},
            project_id="p",
            work_id="w",
        )
    )
    assert next_stage_run(events, "w", "implement") == ("w:implement:4", 4)
