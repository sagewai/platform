# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Task thread messages and clarification answers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sagewai.admin.tasks_routes import router
from sagewai.artifacts import LocalArtifactStore
from sagewai.work.tasks import (
    TaskDefaults,
    TaskEventType,
    TaskService,
    TaskStore,
    TaskWriter,
)
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _task

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
BRIEF = (
    "Implement the retry queue in the payments service repository, add the failing test first, "
    "and open a pull request when the deterministic verification command passes."
)


@dataclass
class AdminClient:
    app: Any
    http: httpx.AsyncClient
    headers: dict[str, str]


@pytest.fixture
async def client(dialect_engine, tmp_path) -> AdminClient:  # noqa: F811
    task_store = TaskStore(engine=dialect_engine)
    await task_store.init()
    await task_store.put_defaults(
        TaskDefaults(project_id="p", target=_task(project_id="p").target), expected_revision=0
    )
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    app = FastAPI()
    app.state.task_store = task_store
    app.state.task_service = TaskService(store=task_store, artifact_store=artifacts)
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield AdminClient(app=app, http=http, headers={"X-Project-ID": "p"})


async def _create(client: AdminClient, brief: str = BRIEF) -> str:
    """Create one Task over the route and return its id."""
    response = await client.http.post(
        "/api/v1/tasks", headers=client.headers, json={"brief": brief}
    )
    assert response.status_code == 201, response.text
    return response.json()["task"]["id"]


async def _create_clarifying(client: AdminClient) -> tuple[str, str]:
    """A Task whose intake asked, with the id of its first open question.

    ``"tidy up"`` scores below the picker threshold, so intake lands in the ``synthesis`` band
    and asks; the thread projection is the console's own read of what is open.
    """
    task_id = await _create(client, "tidy up")
    thread = await client.http.get(f"/api/v1/tasks/{task_id}/thread", headers=client.headers)
    open_ids = thread.json()["open_question_ids"]
    assert open_ids, thread.text
    return task_id, open_ids[0]


async def _add_clarification_question(
    client: AdminClient, task_id: str, *, default: str | None, defaultable: bool
) -> None:
    record = await client.app.state.task_store.load_record(task_id, project_id="p")
    assert record is not None
    await TaskWriter(client.app.state.task_store).append(
        record,
        [
            (
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": [
                        {
                            "id": "hard",
                            "text": "Which repository?",
                            "kind": "text",
                            "options": [],
                            "default": default,
                            "defaultable": defaultable,
                            "rationale": "",
                            "attention_version": 1,
                        }
                    ],
                    "deadline_at": NOW.isoformat(),
                },
            )
        ],
        now=NOW,
    )


@pytest.mark.asyncio
async def test_a_message_lands_on_the_thread(client: AdminClient) -> None:
    task_id = await _create(client)

    response = await client.http.post(
        f"/api/v1/tasks/{task_id}/messages", headers=client.headers, json={"text": "use redis"}
    )
    thread = await client.http.get(f"/api/v1/tasks/{task_id}/thread", headers=client.headers)

    assert response.status_code == 201
    assert [entry["text"] for entry in thread.json()["entries"] if entry["kind"] == "message"] == [
        "use redis"
    ]


@pytest.mark.asyncio
async def test_a_keyed_message_survives_a_retried_post(client: AdminClient) -> None:
    task_id = await _create(client)
    headers = {**client.headers, "Idempotency-Key": "k1"}

    first = await client.http.post(
        f"/api/v1/tasks/{task_id}/messages", headers=headers, json={"text": "use redis"}
    )
    again = await client.http.post(
        f"/api/v1/tasks/{task_id}/messages", headers=headers, json={"text": "use redis"}
    )
    thread = await client.http.get(f"/api/v1/tasks/{task_id}/thread", headers=client.headers)

    assert first.status_code == again.status_code == 201
    assert again.json()["revision"] == first.json()["revision"]
    assert [entry["text"] for entry in thread.json()["entries"] if entry["kind"] == "message"] == [
        "use redis"
    ]


@pytest.mark.asyncio
async def test_a_message_on_an_unknown_task_is_a_404(client: AdminClient) -> None:
    response = await client.http.post(
        "/api/v1/tasks/missing/messages", headers=client.headers, json={"text": "hi"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_message_that_loses_the_append_race_is_a_409(
    client: AdminClient, monkeypatch
) -> None:
    from sagewai.work.tasks.store import StaleTaskError

    task_id = await _create(client)

    async def _stale(*_args, **_kwargs):
        raise StaleTaskError("concurrent append won the sequence")

    monkeypatch.setattr(client.app.state.task_store, "append", _stale)

    response = await client.http.post(
        f"/api/v1/tasks/{task_id}/messages", headers=client.headers, json={"text": "hi"}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "concurrent append won the sequence"


@pytest.mark.asyncio
async def test_an_answer_binds_to_the_presented_attention_version(client: AdminClient) -> None:
    task_id, question_id = await _create_clarifying(client)

    stale = await client.http.post(
        f"/api/v1/tasks/{task_id}/answers",
        headers=client.headers,
        json={"attention_id": question_id, "attention_version": 7, "answer": "redis"},
    )
    current = await client.http.post(
        f"/api/v1/tasks/{task_id}/answers",
        headers=client.headers,
        json={"attention_id": question_id, "attention_version": 1, "answer": "redis"},
    )

    assert stale.status_code == 409
    assert "attention version" in stale.json()["detail"]
    assert current.status_code == 200


@pytest.mark.asyncio
async def test_an_answer_on_an_unknown_task_is_a_404(client: AdminClient) -> None:
    response = await client.http.post(
        "/api/v1/tasks/missing/answers",
        headers=client.headers,
        json={"attention_id": "q1", "attention_version": 1, "answer": "redis"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_an_answer_to_a_closed_question_is_a_409(client: AdminClient) -> None:
    task_id, question_id = await _create_clarifying(client)
    await client.http.post(
        f"/api/v1/tasks/{task_id}/answers",
        headers=client.headers,
        json={"attention_id": question_id, "attention_version": 1, "answer": "redis"},
    )

    again = await client.http.post(
        f"/api/v1/tasks/{task_id}/answers",
        headers=client.headers,
        json={"attention_id": question_id, "attention_version": 1, "answer": "redis"},
    )

    assert again.status_code == 409
    assert "no open clarification question" in again.json()["detail"]


@pytest.mark.asyncio
async def test_an_answer_that_loses_the_append_race_is_a_409(
    client: AdminClient, monkeypatch
) -> None:
    from sagewai.work.tasks.store import StaleTaskError

    task_id, question_id = await _create_clarifying(client)

    async def _stale(*_args, **_kwargs):
        raise StaleTaskError("concurrent append won the sequence")

    monkeypatch.setattr(client.app.state.task_store, "append", _stale)

    response = await client.http.post(
        f"/api/v1/tasks/{task_id}/answers",
        headers=client.headers,
        json={"attention_id": question_id, "attention_version": 1, "answer": "redis"},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/tasks/missing/messages", {"text": "use redis"}),
        (
            "/api/v1/tasks/missing/answers",
            {"attention_id": "q1", "attention_version": 1, "answer": "redis"},
        ),
    ],
)
async def test_message_routes_refuse_global_scope(
    client: AdminClient, path: str, body: dict[str, object]
) -> None:
    response = await client.http.post(path, headers={"X-Project-ID": "global"}, json=body)

    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"attention_id": "q1", "attention_version": 1, "answer": "redis", "use_default": True},
        {"attention_id": "q1", "attention_version": 1},
    ],
)
async def test_an_answer_carries_exactly_one_of_answer_or_use_default(
    client: AdminClient, body: dict[str, object]
) -> None:
    response = await client.http.post(
        "/api/v1/tasks/missing/answers", headers=client.headers, json=body
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_defaulting_an_answer_uses_the_declared_default(client: AdminClient) -> None:
    task_id, question_id = await _create_clarifying(client)

    response = await client.http.post(
        f"/api/v1/tasks/{task_id}/answers",
        headers=client.headers,
        json={"attention_id": question_id, "attention_version": 1, "use_default": True},
    )
    thread = await client.http.get(f"/api/v1/tasks/{task_id}/thread", headers=client.headers)
    question = next(
        entry for entry in thread.json()["entries"] if entry["attention_id"] == question_id
    )

    assert response.status_code == 200
    assert question["answered_by"] == "default"
    assert (
        question["answer"]
        == "The planner's interpretation of the brief, recorded as an assumption."
    )


@pytest.mark.asyncio
async def test_defaulting_a_non_defaultable_answer_is_a_409(
    client: AdminClient,
) -> None:
    task_id, _question_id = await _create_clarifying(client)
    await _add_clarification_question(client, task_id, default="no", defaultable=False)

    response = await client.http.post(
        f"/api/v1/tasks/{task_id}/answers",
        headers=client.headers,
        json={"attention_id": "hard", "attention_version": 1, "use_default": True},
    )

    assert response.status_code == 409
    assert "not defaultable" in response.json()["detail"]


@pytest.mark.asyncio
async def test_defaulting_an_answer_without_a_declared_default_is_a_409(
    client: AdminClient,
) -> None:
    task_id, _question_id = await _create_clarifying(client)
    await _add_clarification_question(client, task_id, default=None, defaultable=True)

    response = await client.http.post(
        f"/api/v1/tasks/{task_id}/answers",
        headers=client.headers,
        json={"attention_id": "hard", "attention_version": 1, "use_default": True},
    )

    assert response.status_code == 409
    assert "no default" in response.json()["detail"]
