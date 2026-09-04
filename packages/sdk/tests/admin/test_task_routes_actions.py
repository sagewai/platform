# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Action records, the artifact read, and the rollback request."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sagewai.admin.tasks_routes import artifacts_router, router
from sagewai.artifacts.models import ArtifactRef
from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.work.tasks import TaskService, TaskStore
from sagewai.work.tasks.events import TaskEventType, fold_record
from sagewai.work.tasks.models import Sensitivity, Task, TaskStatus
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _event, _record, _task

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
BRIEF_BYTES = b"the brief"
EVIDENCE_BYTES = b"evidence artifact"
STEP = {
    "id": "s1",
    "title": "Deliver the report",
    "goal": "Deliver the report",
    "allowed_scope": ["reports"],
    "acceptance_criteria": [
        {"statement": "the report is delivered", "verification_kind": "deterministic"}
    ],
    "constraints": [],
    "non_goals": [],
    "risk": "low",
    "design_required": False,
    "depends_on": [],
    "domain": "backend",
    "size": "s",
}
MATRIX = [
    {
        "id": "m1",
        "statement": "the report is delivered",
        "verification_kind": "deterministic",
        "command": "just smoke",
    }
]
ACTION = {
    "project_id": "p",
    "action": "report_delivered",
    "work_id": "w1",
    "risk": "medium",
    "reversibility": "compensatable",
    "scope": "https://github.com/o/r/issues/1",
    "evidence_refs": [],
    "rollback": "delete_comment",
    "post_check": "comment_read_back",
}


@dataclass
class AdminClient:
    app: Any
    http: httpx.AsyncClient
    headers: dict[str, str]
    brief: ArtifactRef
    evidence_ref: str


def _action(*, work_id: str = "w1", rollback: str | None = "delete_comment") -> dict:
    return {**ACTION, "work_id": work_id, "rollback": rollback}


def _stream(
    task: Task,
    brief_ref: str,
    *,
    action_id: str = "deliver:w1:2",
    work_id: str = "w1",
    step_work_id: str = "w1",
    action: dict | None = None,
    result_evidence_refs: tuple[str, ...] = (),
    status: TaskStatus | None = None,
    pending_gate: str | None = None,
    rollback_gate: bool = False,
    rolled_back: bool = False,
) -> tuple:
    events = [
        _event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),
        _event(task, 2, TaskEventType.BRIEF_RECORDED, {"brief_ref": brief_ref, "summary": "s"}),
        _event(
            task,
            3,
            TaskEventType.PLAN_PROPOSED,
            {"version": 1, "steps": [STEP], "acceptance_matrix": MATRIX},
        ),
        _event(task, 4, TaskEventType.PLAN_ACCEPTED, {"version": 1}),
        _event(task, 5, TaskEventType.CYCLE_STARTED, {"cycle": 1, "scheduled_for": None}),
        _event(
            task,
            6,
            TaskEventType.STEP_WORK_STARTED,
            {
                "step_id": "s1",
                "work_id": step_work_id,
                "issue_url": "https://github.com/o/r/issues/1",
                "base_sha": "a" * 40,
            },
        ),
        _event(
            task,
            7,
            TaskEventType.ACTION_INTENT_RECORDED,
            {
                "action_id": action_id,
                "work_id": work_id,
                "gate_id": action_id,
                "action": action or _action(work_id=work_id),
            },
        ),
        _event(
            task,
            8,
            TaskEventType.ACTION_RESULT_RECORDED,
            {
                "work_id": work_id,
                "project_id": "p",
                "action_id": action_id,
                "status": "succeeded",
                "external_ref": "https://github.com/o/r/issues/1#issuecomment-9",
                "evidence_refs": list(result_evidence_refs),
                "started_at": NOW.isoformat(),
                "completed_at": NOW.isoformat(),
            },
        ),
        _event(
            task,
            9,
            TaskEventType.OBSERVATION_RECORDED,
            {
                "work_id": work_id,
                "action_id": action_id,
                "check": "comment_read_back",
                "passed": True,
                "detail": "read back",
                "evidence_refs": [],
            },
        ),
    ]
    sequence = 10
    if rollback_gate:
        rollback_action = dict(action or _action(work_id=work_id))
        events.append(
            _event(
                task,
                sequence,
                TaskEventType.GATE_REQUESTED,
                {
                    "gate_id": f"rollback:{work_id}",
                    "question": (
                        f"Allow the recorded rollback ({rollback_action['rollback']}) of "
                        f"{rollback_action['scope']}?"
                    ),
                    "action": rollback_action,
                    "work_id": work_id,
                },
            )
        )
        sequence += 1
        events.append(
            _event(
                task,
                sequence,
                TaskEventType.GATE_DECIDED,
                {"gate_id": f"rollback:{work_id}", "decision": "allow"},
            )
        )
        sequence += 1
    if rolled_back:
        events.append(
            _event(
                task,
                sequence,
                TaskEventType.ACTION_RESULT_RECORDED,
                {
                    "work_id": work_id,
                    "project_id": "p",
                    "action_id": f"delete_comment:{work_id}:9",
                    "status": "succeeded",
                    "external_ref": None,
                    "evidence_refs": [],
                    "started_at": NOW.isoformat(),
                    "completed_at": NOW.isoformat(),
                },
            )
        )
        sequence += 1
    if pending_gate is not None:
        events.append(
            _event(
                task,
                sequence,
                TaskEventType.GATE_REQUESTED,
                {"gate_id": pending_gate, "question": "Still open."},
            )
        )
        sequence += 1
    if status is not None:
        events.append(
            _event(task, sequence, TaskEventType.TASK_STATUS_CHANGED, {"status": status.value})
        )
    return tuple(events)


@pytest.fixture
async def client(dialect_engine, tmp_path) -> AdminClient:  # noqa: F811
    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    ref = artifacts.put_bytes(
        BRIEF_BYTES, project_id="p", media_type="text/plain", created_by="test"
    )
    evidence = artifacts.put_bytes(
        EVIDENCE_BYTES, project_id="p", media_type="application/json", created_by="test"
    )
    task = _task("t-1", project_id="p").model_copy(update={"brief_ref": ref})
    events = _stream(task, ref.storage_ref, result_evidence_refs=(evidence.storage_ref,))
    await task_store.create(task, events=events, record=fold_record(_record(task), events))
    app = FastAPI()
    app.state.task_store = task_store
    app.state.artifact_store = artifacts
    app.state.task_service = TaskService(store=task_store, artifact_store=artifacts)
    app.include_router(router)
    app.include_router(artifacts_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield AdminClient(
            app=app,
            http=http,
            headers={"X-Project-ID": "p"},
            brief=ref,
            evidence_ref=evidence.storage_ref,
        )


async def _reseed_restricted(client: AdminClient) -> None:
    """A second Task at RESTRICTED that references the same brief object."""
    task = _task("t-restricted", project_id="p").model_copy(
        update={"brief_ref": client.brief, "sensitivity": Sensitivity.RESTRICTED}
    )
    events = _stream(task, client.brief.storage_ref)[:2]
    await client.app.state.task_store.create(
        task, events=events, record=fold_record(_record(task), events)
    )


async def _seed_task(client: AdminClient, task_id: str, **stream_kwargs: Any) -> None:
    task = _task(task_id, project_id="p").model_copy(update={"brief_ref": client.brief})
    events = _stream(task, client.brief.storage_ref, **stream_kwargs)
    await client.app.state.task_store.create(
        task, events=events, record=fold_record(_record(task), events)
    )


@pytest.mark.asyncio
async def test_actions_lists_the_folded_records(client: AdminClient) -> None:
    response = await client.http.get("/api/v1/tasks/t-1/actions", headers=client.headers)

    assert response.status_code == 200
    actions = response.json()["actions"]
    assert [action["action_id"] for action in actions] == ["deliver:w1:2"]
    assert actions[0]["rollback"] == "delete_comment"
    assert actions[0]["passed"] is True


@pytest.mark.asyncio
async def test_rollback_opens_and_allows_the_gate(client: AdminClient) -> None:
    response = await client.http.post(
        "/api/v1/tasks/t-1/actions/deliver:w1:2/rollback", headers=client.headers
    )

    assert response.status_code == 200
    events = await client.app.state.task_store.read_events("t-1", project_id="p")
    tail = [event.event_type.value for event in events[-3:]]
    assert tail == ["GATE_REQUESTED", "GATE_DECIDED", "TASK_STATUS_CHANGED"]
    assert events[-3].payload_json["gate_id"] == "rollback:w1"
    assert events[-3].payload_json["action"]["scope"].endswith("#issuecomment-9")
    assert events[-2].payload_json["decision"] == "allow"
    assert events[-1].payload_json == {"status": "EXECUTING"}
    assert response.json()["status"] == "EXECUTING"


@pytest.mark.asyncio
async def test_rollback_from_blocked_moves_to_executing(client: AdminClient) -> None:
    await _seed_task(client, "t-blocked", status=TaskStatus.BLOCKED)

    response = await client.http.post(
        "/api/v1/tasks/t-blocked/actions/deliver:w1:2/rollback", headers=client.headers
    )

    assert response.status_code == 200
    events = await client.app.state.task_store.read_events("t-blocked", project_id="p")
    assert [event.event_type.value for event in events[-3:]] == [
        "GATE_REQUESTED",
        "GATE_DECIDED",
        "TASK_STATUS_CHANGED",
    ]
    assert events[-1].payload_json == {"status": "EXECUTING"}


@pytest.mark.asyncio
async def test_rollback_from_assessing_keeps_its_status(client: AdminClient) -> None:
    await _seed_task(client, "t-assessing", status=TaskStatus.ASSESSING)

    response = await client.http.post(
        "/api/v1/tasks/t-assessing/actions/deliver:w1:2/rollback", headers=client.headers
    )

    assert response.status_code == 200
    events = await client.app.state.task_store.read_events("t-assessing", project_id="p")
    assert [event.event_type.value for event in events[-2:]] == ["GATE_REQUESTED", "GATE_DECIDED"]
    assert response.json()["status"] == "ASSESSING"


@pytest.mark.asyncio
async def test_rollback_from_complete_is_a_409(client: AdminClient) -> None:
    await _seed_task(client, "t-complete", status=TaskStatus.COMPLETE)

    response = await client.http.post(
        "/api/v1/tasks/t-complete/actions/deliver:w1:2/rollback", headers=client.headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "cannot move Task from COMPLETE to EXECUTING"


@pytest.mark.asyncio
async def test_rollback_of_an_unknown_action_is_a_404(client: AdminClient) -> None:
    response = await client.http.post(
        "/api/v1/tasks/t-1/actions/merge:w9:1/rollback", headers=client.headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rollback_refuses_an_action_without_a_recipe(client: AdminClient) -> None:
    await _seed_task(client, "t-no-recipe", action=_action(rollback=None))

    response = await client.http.post(
        "/api/v1/tasks/t-no-recipe/actions/deliver:w1:2/rollback", headers=client.headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "action deliver:w1:2 declares no rollback recipe"


@pytest.mark.asyncio
async def test_rollback_refuses_when_another_gate_is_open(client: AdminClient) -> None:
    await _seed_task(client, "t-open-gate", pending_gate="plan:t-open-gate:1")

    response = await client.http.post(
        "/api/v1/tasks/t-open-gate/actions/deliver:w1:2/rollback", headers=client.headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "gate plan:t-open-gate:1 is still open"


@pytest.mark.asyncio
async def test_rollback_refuses_work_outside_the_current_cycle(client: AdminClient) -> None:
    await _seed_task(
        client,
        "t-stale-work",
        action_id="deliver:w9:2",
        work_id="w9",
        step_work_id="w1",
        action=_action(work_id="w9"),
    )

    response = await client.http.post(
        "/api/v1/tasks/t-stale-work/actions/deliver:w9:2/rollback", headers=client.headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "work w9 is not in the current cycle"


@pytest.mark.asyncio
async def test_rollback_refuses_work_that_was_already_rolled_back(client: AdminClient) -> None:
    await _seed_task(client, "t-rolled-back", rolled_back=True)

    response = await client.http.post(
        "/api/v1/tasks/t-rolled-back/actions/deliver:w1:2/rollback", headers=client.headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "work w1 was already rolled back"


@pytest.mark.asyncio
async def test_rollback_retry_after_execution_is_idempotent(client: AdminClient) -> None:
    await _seed_task(client, "t-retry-rolled-back", rollback_gate=True, rolled_back=True)
    before = await client.app.state.task_store.read_events("t-retry-rolled-back", project_id="p")

    response = await client.http.post(
        "/api/v1/tasks/t-retry-rolled-back/actions/deliver:w1:2/rollback",
        headers=client.headers,
    )

    assert response.status_code == 200
    after = await client.app.state.task_store.read_events("t-retry-rolled-back", project_id="p")
    assert len(after) == len(before)


@pytest.mark.asyncio
async def test_the_artifact_route_serves_a_referenced_brief(client: AdminClient) -> None:
    digest = client.brief.storage_ref.removeprefix("artifact://")

    response = await client.http.get(
        f"/api/v1/artifacts/{digest}", headers=client.headers, params={"task_id": "t-1"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.content == BRIEF_BYTES


@pytest.mark.asyncio
async def test_the_artifact_route_serves_evidence_as_octet_stream(client: AdminClient) -> None:
    digest = client.evidence_ref.removeprefix("artifact://")

    response = await client.http.get(
        f"/api/v1/artifacts/{digest}", headers=client.headers, params={"task_id": "t-1"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.content == EVIDENCE_BYTES


@pytest.mark.asyncio
async def test_the_artifact_route_refuses_an_unreferenced_digest(client: AdminClient) -> None:
    response = await client.http.get(
        f"/api/v1/artifacts/sha256:{'f' * 64}",
        headers=client.headers,
        params={"task_id": "t-1"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_the_artifact_route_refuses_restricted_content(client: AdminClient) -> None:
    await _reseed_restricted(client)
    digest = client.brief.storage_ref.removeprefix("artifact://")

    response = await client.http.get(
        f"/api/v1/artifacts/{digest}",
        headers=client.headers,
        params={"task_id": "t-restricted"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "restricted content never leaves the console sink"


@pytest.mark.asyncio
async def test_the_artifact_route_refuses_global_scope(client: AdminClient) -> None:
    digest = client.brief.storage_ref.removeprefix("artifact://")

    response = await client.http.get(
        f"/api/v1/artifacts/{digest}",
        headers={"X-Project-ID": "global"},
        params={"task_id": "t-1"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("task_id", ["missing", "foreign"])
async def test_the_artifact_route_returns_404_for_unknown_or_foreign_task(
    client: AdminClient, task_id: str
) -> None:
    foreign = _task("foreign", project_id="other")
    foreign_events = (_event(foreign, 1, TaskEventType.TASK_CREATED, {"title": foreign.title}),)
    await client.app.state.task_store.create(
        foreign,
        events=foreign_events,
        record=fold_record(_record(foreign), foreign_events),
    )
    digest = client.brief.storage_ref.removeprefix("artifact://")

    response = await client.http.get(
        f"/api/v1/artifacts/{digest}", headers=client.headers, params={"task_id": task_id}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_repeated_rollback_request_appends_nothing(client: AdminClient) -> None:
    first = await client.http.post(
        "/api/v1/tasks/t-1/actions/deliver:w1:2/rollback", headers=client.headers
    )
    again = await client.http.post(
        "/api/v1/tasks/t-1/actions/deliver:w1:2/rollback", headers=client.headers
    )

    assert first.status_code == 200
    assert again.status_code == 200
    assert again.json()["revision"] == first.json()["revision"]
    events = await client.app.state.task_store.read_events("t-1", project_id="p")
    assert sum(1 for event in events if event.event_type is TaskEventType.GATE_REQUESTED) == 1


@pytest.mark.asyncio
async def test_a_rollback_that_loses_the_revision_race_is_a_409(
    client: AdminClient, monkeypatch
) -> None:
    from sagewai.work.tasks.store import StaleTaskError

    async def _stale(*_args, **_kwargs):
        raise StaleTaskError("projection changed under the append")

    monkeypatch.setattr(client.app.state.task_store, "append", _stale)

    response = await client.http.post(
        "/api/v1/tasks/t-1/actions/deliver:w1:2/rollback", headers=client.headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "projection changed under the append"
