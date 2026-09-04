# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task gates are decided on the Task; Work gates have their own route."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError

from sagewai.admin.tasks_routes import router, work_router
from sagewai.work import WorkEvent, WorkEventType, WorkRecord, WorkStore
from sagewai.work.tasks import TaskService, TaskStore
from sagewai.work.tasks.events import TaskEvent, TaskEventType, fold_record
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _record, _task

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
MERGE_ACTION = {
    "project_id": "p",
    "action": "merge",
    "work_id": "w1",
    "risk": "medium",
    "reversibility": "compensatable",
    "scope": "https://github.com/o/r/pull/3",
    "evidence_refs": [],
    "rollback": "revert_pull_request",
    "post_check": "merged_sha_read_back",
}
NON_MERGE_GATE_DETAIL = (
    "gate {gate_id} is not a merge gate: Task gates are decided at "
    "POST /api/v1/tasks/{{task_id}}/gates/{{gate_id}}, delivery gates with "
    "sagewai work approve"
)


@dataclass
class AdminClient:
    app: Any
    http: httpx.AsyncClient
    headers: dict[str, str]


@pytest.fixture
async def client(dialect_engine, tmp_path) -> AdminClient:  # noqa: F811
    from sagewai.artifacts.object_store import LocalArtifactStore

    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    app = FastAPI()
    app.state.task_store = task_store
    app.state.work_store = work_store
    app.state.task_service = TaskService(
        store=task_store, artifact_store=LocalArtifactStore(root=tmp_path / "objects")
    )
    app.include_router(router)
    app.include_router(work_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield AdminClient(app=app, http=http, headers={"X-Project-ID": "p"})


async def _task_with_gate(client: AdminClient, gate_id: str, task_id: str = "t-1") -> str:
    """A Task whose projection carries ``gate_id`` as its open gate."""
    task = _task(task_id, project_id="p")
    events = (
        TaskEvent(
            id=f"{task_id}-1",
            project_id="p",
            task_id=task_id,
            sequence=1,
            event_type=TaskEventType.TASK_CREATED,
            actor_type="human",
            actor_ref="arda",
            payload_json={"title": task.title},
            created_at=NOW,
        ),
        TaskEvent(
            id=f"{task_id}-2",
            project_id="p",
            task_id=task_id,
            sequence=2,
            event_type=TaskEventType.GATE_REQUESTED,
            actor_type="system",
            actor_ref="coordinator",
            payload_json={"gate_id": gate_id, "question": "Approve it."},
            created_at=NOW,
        ),
    )
    await client.app.state.task_store.create(
        task, events=events, record=fold_record(_record(task), events)
    )
    return task_id


async def _work_with_gate(
    client: AdminClient, work_id: str, gate_id: str, status: str = "READY_TO_MERGE"
) -> None:
    """A Work with ``gate_id`` pending, as the profile stage leaves it."""
    store: WorkStore = client.app.state.work_store
    await store.save_work(
        WorkRecord(
            work_id=work_id,
            project_id="p",
            source_ref="https://github.com/o/r/issues/1",
            profile="software",
            status=status,
            active_run_id=None,
            pending_gate=gate_id,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    for sequence, (event_type, payload) in enumerate(
        (
            (WorkEventType.WORK_CREATED, {"work_id": work_id}),
            (
                WorkEventType.GATE_REQUESTED,
                {
                    "gate_id": gate_id,
                    "question": "Approve merge of PR #3.",
                    "action": MERGE_ACTION,
                },
            ),
        ),
        start=1,
    ):
        await store.append_event(
            WorkEvent(
                id=f"{work_id}-{sequence}",
                project_id="p",
                work_id=work_id,
                sequence=sequence,
                event_type=event_type,
                actor_type="github_lifecycle",
                actor_ref="policy",
                payload_json=payload,
                created_at=NOW,
            )
        )


@pytest.mark.asyncio
async def test_a_plan_gate_is_decided_on_the_task(client: AdminClient) -> None:
    task_id = await _task_with_gate(client, "plan:t-1:1")

    response = await client.http.post(
        f"/api/v1/tasks/{task_id}/gates/plan:t-1:1",
        headers=client.headers,
        json={"decision": "allow"},
    )

    assert response.status_code == 200
    assert response.json()["pending_gate"] is None
    assert response.json()["status"] == "PLANNING"


@pytest.mark.asyncio
async def test_a_denied_gate_blocks_the_task_and_records_the_note(client: AdminClient) -> None:
    task_id = await _task_with_gate(client, "plan:t-1:1")

    response = await client.http.post(
        f"/api/v1/tasks/{task_id}/gates/plan:t-1:1",
        headers=client.headers,
        json={"decision": "deny", "note": "wrong repository"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "BLOCKED"
    events = await client.app.state.task_store.read_events(task_id, project_id="p")
    assert any(
        event.payload_json.get("text") == "wrong repository"
        for event in events
        if event.event_type is TaskEventType.TASK_MESSAGE
    )


@pytest.mark.asyncio
async def test_deciding_a_gate_that_is_not_open_is_a_409(client: AdminClient) -> None:
    task_id = await _task_with_gate(client, "plan:t-1:1")

    response = await client.http.post(
        f"/api/v1/tasks/{task_id}/gates/plan:t-1:2",
        headers=client.headers,
        json={"decision": "allow"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "no open gate plan:t-1:2"


@pytest.mark.asyncio
async def test_a_task_gate_that_loses_the_append_race_is_a_409(
    client: AdminClient, monkeypatch
) -> None:
    from sagewai.work.tasks.store import StaleTaskError

    task_id = await _task_with_gate(client, "plan:t-1:1")

    async def _stale(*_args, **_kwargs):
        raise StaleTaskError("projection changed under the append")

    monkeypatch.setattr(client.app.state.task_store, "append", _stale)

    response = await client.http.post(
        f"/api/v1/tasks/{task_id}/gates/plan:t-1:1",
        headers=client.headers,
        json={"decision": "allow"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "projection changed under the append"


@pytest.mark.asyncio
async def test_a_work_gate_on_the_task_route_names_the_work_route(client: AdminClient) -> None:
    task_id = await _task_with_gate(client, "merge:w1:3")

    response = await client.http.post(
        f"/api/v1/tasks/{task_id}/gates/merge:w1:3",
        headers=client.headers,
        json={"decision": "allow"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "gate merge:w1:3 belongs to a Work; decide it at "
        "POST /api/v1/work/{work_id}/gates/{gate_id} (or sagewai work approve)"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/tasks/missing/gates/plan:missing:1", {"decision": "allow"}),
        ("/api/v1/work/missing/gates/merge:missing:1", {"decision": "allow"}),
    ],
)
async def test_gate_routes_refuse_global_scope(
    client: AdminClient, path: str, body: dict[str, object]
) -> None:
    response = await client.http.post(path, headers={"X-Project-ID": "global"}, json=body)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_the_work_gate_route_records_the_decision_and_clears_the_gate(
    client: AdminClient,
) -> None:
    await _work_with_gate(client, "w1", "merge:w1:3")

    response = await client.http.post(
        "/api/v1/work/w1/gates/merge:w1:3", headers=client.headers, json={"decision": "allow"}
    )

    assert response.status_code == 200
    record = await client.app.state.work_store.load_work("w1", project_id="p")
    assert record.pending_gate is None
    assert record.status == "MERGING"
    events = await client.app.state.work_store.read_events("w1", project_id="p")
    assert events[-1].event_type is WorkEventType.GATE_DECIDED
    assert events[-1].payload_json["decision"] == "allow"
    assert events[-1].payload_json["action"] == MERGE_ACTION
    assert events[-1].actor_type == "human"


@pytest.mark.asyncio
async def test_the_work_gate_route_rejects_a_delivery_gate_without_changing_work(
    client: AdminClient,
) -> None:
    gate_id = "deploy_production:w2:prod"
    await _work_with_gate(client, "w2", gate_id, status="PRODUCTION_ROLLOUT")
    before = await client.app.state.work_store.load_work("w2", project_id="p")
    events_before = await client.app.state.work_store.read_events("w2", project_id="p")

    response = await client.http.post(
        f"/api/v1/work/w2/gates/{gate_id}",
        headers=client.headers,
        json={"decision": "allow"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == NON_MERGE_GATE_DETAIL.format(gate_id=gate_id)
    assert await client.app.state.work_store.load_work("w2", project_id="p") == before
    assert await client.app.state.work_store.read_events("w2", project_id="p") == events_before


@pytest.mark.asyncio
async def test_the_work_gate_route_rejects_a_task_gate_without_checking_pending(
    client: AdminClient,
) -> None:
    gate_id = "deliver:w1:2"
    await _work_with_gate(client, "w1", "merge:w1:3")

    response = await client.http.post(
        f"/api/v1/work/w1/gates/{gate_id}",
        headers=client.headers,
        json={"decision": "allow"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == NON_MERGE_GATE_DETAIL.format(gate_id=gate_id)


@pytest.mark.asyncio
async def test_the_work_gate_route_rejects_notes(client: AdminClient) -> None:
    await _work_with_gate(client, "w1", "merge:w1:3")

    response = await client.http.post(
        "/api/v1/work/w1/gates/merge:w1:3",
        headers=client.headers,
        json={"decision": "allow", "note": "merge now"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_work_gate_that_loses_the_append_race_is_a_409(
    client: AdminClient, monkeypatch
) -> None:
    await _work_with_gate(client, "w1", "merge:w1:3")
    before = await client.app.state.work_store.load_work("w1", project_id="p")

    async def _integrity_error(*_args, **_kwargs):
        raise IntegrityError("stmt", {}, Exception("race"))

    monkeypatch.setattr(client.app.state.work_store, "append_next", _integrity_error)

    response = await client.http.post(
        "/api/v1/work/w1/gates/merge:w1:3",
        headers=client.headers,
        json={"decision": "allow"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "concurrent append won the sequence"
    assert await client.app.state.work_store.load_work("w1", project_id="p") == before


@pytest.mark.asyncio
async def test_a_denied_work_gate_leaves_the_merge_stage_to_block_it(client: AdminClient) -> None:
    await _work_with_gate(client, "w1", "merge:w1:3")

    response = await client.http.post(
        "/api/v1/work/w1/gates/merge:w1:3", headers=client.headers, json={"decision": "deny"}
    )

    assert response.status_code == 200
    record = await client.app.state.work_store.load_work("w1", project_id="p")
    assert record.pending_gate is None
    assert record.status == "READY_TO_MERGE"


@pytest.mark.asyncio
async def test_a_work_gate_that_is_not_pending_is_a_409(client: AdminClient) -> None:
    await _work_with_gate(client, "w1", "merge:w1:3")

    response = await client.http.post(
        "/api/v1/work/w1/gates/merge:w1:9", headers=client.headers, json={"decision": "allow"}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "gate is not pending: merge:w1:9"


@pytest.mark.asyncio
async def test_deciding_the_same_work_gate_twice_appends_once(client: AdminClient) -> None:
    await _work_with_gate(client, "w1", "merge:w1:3")
    first = await client.http.post(
        "/api/v1/work/w1/gates/merge:w1:3", headers=client.headers, json={"decision": "allow"}
    )

    again = await client.http.post(
        "/api/v1/work/w1/gates/merge:w1:3", headers=client.headers, json={"decision": "allow"}
    )

    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()
    events = await client.app.state.work_store.read_events("w1", project_id="p")
    assert sum(1 for event in events if event.event_type is WorkEventType.GATE_DECIDED) == 1


@pytest.mark.asyncio
async def test_the_work_gate_route_404s_for_an_unknown_work(client: AdminClient) -> None:
    response = await client.http.post(
        "/api/v1/work/missing/gates/merge:missing:1",
        headers=client.headers,
        json={"decision": "allow"},
    )
    assert response.status_code == 404
