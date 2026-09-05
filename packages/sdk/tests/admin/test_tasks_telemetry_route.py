# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task telemetry route derives from Task and linked Work streams."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sagewai.admin.tasks_routes import router
from sagewai.work import WorkEvent, WorkEventType, WorkRecord, WorkStore
from sagewai.work.runtime import OperatorResult
from sagewai.work.tasks import (
    Authority,
    Schedule,
    TaskEventType,
    TaskKind,
    TaskOrigin,
    TaskStatus,
    TaskStore,
)
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _event, _record, _task

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


@dataclass
class AdminClient:
    app: Any
    http: httpx.AsyncClient
    headers: dict[str, str]


@pytest.fixture
async def client(dialect_engine) -> AdminClient:  # noqa: F811
    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    app = FastAPI()
    app.state.task_store = task_store
    app.state.work_store = work_store
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield AdminClient(app=app, http=http, headers={"X-Project-ID": "p"})


def _work_record(work_id: str, *, profile_context: dict[str, Any]) -> WorkRecord:
    return WorkRecord(
        work_id=work_id,
        project_id="p",
        source_ref=f"issue:{work_id}",
        profile="software",
        status="COMPLETE",
        active_run_id=None,
        pending_gate=None,
        profile_context=profile_context,
        created_at=NOW,
        updated_at=NOW,
    )


def _selection(
    work_id: str,
    *,
    sequence: int,
    run_id: str,
    runtime: str,
    reason: str = "initial",
) -> WorkEvent:
    return WorkEvent(
        id=f"{work_id}:event:{sequence}",
        project_id="p",
        work_id=work_id,
        sequence=sequence,
        event_type=WorkEventType.RUNTIME_SELECTED,
        actor_type="system",
        actor_ref="test",
        payload_json={
            "role": "implementer",
            "stage": "implement",
            "run_id": run_id,
            "attempt": 1,
            "position": 1,
            "runtime": runtime,
            "reason": reason,
        },
        created_at=NOW,
    )


def _execution(work_id: str, *, sequence: int, run_id: str) -> WorkEvent:
    result = OperatorResult(
        project_id="p",
        work_id=work_id,
        run_id=run_id,
        status="passed",
        summary="done",
        evidence_refs=(),
        artifact_refs=(),
        changes=(),
        verification=("selected model",),
        risks=(),
        action_results=(),
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0,
    )
    return WorkEvent(
        id=f"{work_id}:event:{sequence}",
        project_id="p",
        work_id=work_id,
        sequence=sequence,
        event_type=WorkEventType.EXECUTION_RECORDED,
        actor_type="system",
        actor_ref="test",
        payload_json=result.model_dump(mode="json"),
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_telemetry_route_projects_only_work_linked_to_the_task(
    client: AdminClient,
) -> None:
    task = _task("t1", project_id="p").model_copy(
        update={
            "kind": TaskKind.SCHEDULED,
            "origin": TaskOrigin.SCHEDULE,
            "schedule": Schedule(cron="0 * * * *", timezone="UTC"),
            "authority": Authority.for_kind(TaskKind.SCHEDULED),
        }
    )
    record = _record(task).model_copy(
        update={"status": TaskStatus.SCHEDULED, "next_run_at": NOW}
    )
    await client.app.state.task_store.create(
        task,
        record=record,
        events=(
            _event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),
            _event(task, 2, TaskEventType.CYCLE_STARTED, {"cycle": 1}),
            _event(
                task,
                3,
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "step-1", "work_id": "w1"},
            ),
            _event(
                task,
                4,
                TaskEventType.STEP_WORK_STARTED,
                {"step_id": "step-2", "work_id": "w2"},
            ),
            _event(task, 5, TaskEventType.CYCLE_COMPLETED, {"outcome": "succeeded"}),
            _event(task, 6, TaskEventType.CYCLE_STARTED, {"cycle": 2}),
            _event(task, 7, TaskEventType.CYCLE_COMPLETED, {"outcome": "failed"}),
        ),
    )
    await client.app.state.work_store.save_work(
        _work_record("w1", profile_context={"task_id": "t1"})
    )
    await client.app.state.work_store.save_work(_work_record("w2", profile_context={}))
    await client.app.state.work_store.append_events(
        (
            _selection("w1", sequence=1, run_id="w1:implement:1", runtime="harness"),
            _execution("w1", sequence=2, run_id="w1:implement:1"),
            _selection(
                "w1",
                sequence=3,
                run_id="w1:implement:2",
                runtime="claude",
                reason="escalated",
            ),
            _execution("w1", sequence=4, run_id="w1:implement:2"),
            _selection("w2", sequence=1, run_id="w2:implement:1", runtime="codex"),
            _execution("w2", sequence=2, run_id="w2:implement:1"),
            _selection("w3", sequence=1, run_id="w3:implement:1", runtime="harness"),
        )
    )

    response = await client.http.get("/api/v1/tasks/t1/telemetry", headers=client.headers)

    assert response.status_code == 200
    body = response.json()
    assert [work["work_id"] for work in body["works"]] == ["w1", "w2"]
    assert body["works"][0]["stage_attempts"][0]["runtime"] == "harness"
    assert body["works"][0]["stage_attempts"][0]["selection_note"] == "selected model"
    assert [cycle["cycle"] for cycle in body["cycles"]] == [1, 2]
    assert [cycle["usd_actual"] for cycle in body["cycles"]] == ["0", "0"]
    assert body["scheduled"]["cycles"][0]["usd_actual"] == "0"
    assert body["project"]["escalation_rate_per_role"]["implementer"] == 1 / 4


@pytest.mark.asyncio
async def test_telemetry_route_404s_unknown_task_and_requires_project_scope(
    client: AdminClient,
) -> None:
    task = _task("t1", project_id="p")
    await client.app.state.task_store.create(
        task,
        record=_record(task),
        events=(_event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),),
    )

    missing = await client.http.get("/api/v1/tasks/missing/telemetry", headers=client.headers)
    unscoped = await client.http.get("/api/v1/tasks/t1/telemetry")
    other_project = await client.http.get(
        "/api/v1/tasks/t1/telemetry",
        headers={"X-Project-ID": "other-project"},
    )
    global_scope = await client.http.get(
        "/api/v1/tasks/t1/telemetry",
        headers={"X-Project-ID": "global"},
    )

    assert missing.status_code == 404
    assert unscoped.status_code == 400
    assert unscoped.json() == {"detail": "Work project scope is required"}
    assert other_project.status_code == 404
    assert global_scope.status_code == 400
    assert global_scope.json() == {
        "detail": "Tasks require an explicit project; there is no global Task scope"
    }


@pytest.mark.asyncio
async def test_telemetry_route_covers_the_planning_work(client: AdminClient) -> None:
    task = _task("t1", project_id="p")
    await client.app.state.task_store.create(
        task,
        record=_record(task),
        events=(_event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),),
    )
    await client.app.state.work_store.save_work(
        _work_record("t1:plan:0:1", profile_context={"task_id": "t1"})
    )
    await client.app.state.work_store.append_events(
        (
            _selection("t1:plan:0:1", sequence=1, run_id="t1:plan:0:1:plan:1", runtime="claude"),
            _execution("t1:plan:0:1", sequence=2, run_id="t1:plan:0:1:plan:1"),
        )
    )

    response = await client.http.get("/api/v1/tasks/t1/telemetry", headers=client.headers)

    assert response.status_code == 200
    assert [work["work_id"] for work in response.json()["works"]] == ["t1:plan:0:1"]
