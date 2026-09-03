# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Worker progress batches for claimed Fleet work.operator tasks."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from sagewai.work import ACTIVITY_ROW_CAP
from sagewai.work.activity import OperatorActivity
from tests.fleet.conftest import Stores, Worker
from tests.work.test_fleet_runtime import _request

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _activity_json(sequence: int, fill: str | None = None) -> dict[str, Any]:
    request = _request()
    values = {
        "project_id": request.project_id,
        "work_id": request.work_id,
        "run_id": request.run_id,
        "sequence": sequence,
        "at": NOW,
        "source": "claude",
        "kind": "message",
        "summary": f"activity {sequence}",
    }
    if fill is not None:
        values.update(
            kind="tool_result",
            summary=fill * 2000,
            detail=fill * 8192,
        )
    return OperatorActivity(**values).model_dump(mode="json")


def _maximal_cjk_batch_for_sink_budget() -> list[dict[str, Any]]:
    batch: list[dict[str, Any]] = []
    total = 0
    for sequence in range(1, 51):
        activity = _activity_json(sequence, "中")
        size = len(json.dumps(activity))
        if total + size > 512 * 1024:
            break
        batch.append(activity)
        total += size
    assert batch
    assert total <= 512 * 1024
    return batch


@pytest.mark.asyncio
async def test_progress_accepts_a_batch_for_the_claimed_run_and_ingests_it(
    client: httpx.AsyncClient,
    worker: Worker,
    claimed_task: dict[str, str],
    stores: Stores,
) -> None:
    body = {
        "run_id": claimed_task["run_id"],
        "activities": [_activity_json(1), _activity_json(2)],
    }
    response = await client.post(
        "/api/v1/fleet/progress",
        json=body,
        headers=worker.headers,
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": 2, "last_sequence": 2}
    stored = await stores.activity.read(
        claimed_task["work_id"],
        run_id=claimed_task["run_id"],
        project_id=claimed_task["project_id"],
    )
    assert [item.sequence for item in stored] == [1, 2]


@pytest.mark.asyncio
async def test_progress_accepts_maximal_cjk_batch_bounded_by_sink_budget(
    client: httpx.AsyncClient,
    worker: Worker,
    claimed_task: dict[str, str],
) -> None:
    body = {
        "run_id": claimed_task["run_id"],
        "activities": _maximal_cjk_batch_for_sink_budget(),
    }
    assert len(json.dumps(body)) <= 640 * 1024

    response = await client.post(
        "/api/v1/fleet/progress",
        json=body,
        headers=worker.headers,
    )

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_progress_rejects_empty_and_non_object_body_with_422(
    client: httpx.AsyncClient,
    worker: Worker,
) -> None:
    headers = {**worker.headers, "content-type": "application/json"}

    empty = await client.post(
        "/api/v1/fleet/progress",
        content=b"",
        headers=headers,
    )
    non_object = await client.post(
        "/api/v1/fleet/progress",
        content=b"[]",
        headers=headers,
    )
    string_body = await client.post(
        "/api/v1/fleet/progress",
        content=b'"not an object"',
        headers=headers,
    )

    assert empty.status_code == 422
    assert non_object.status_code == 422
    assert string_body.status_code == 422


@pytest.mark.asyncio
async def test_progress_rejects_wrong_worker_unclaimed_task_and_non_monotonic_batches(
    client: httpx.AsyncClient,
    worker: Worker,
    other_worker: Worker,
    other_project_worker: Worker,
    claimed_task: dict[str, str],
) -> None:
    body = {"run_id": claimed_task["run_id"], "activities": [_activity_json(1)]}
    assert (
        await client.post(
            "/api/v1/fleet/progress",
            json=body,
            headers=other_worker.headers,
        )
    ).status_code == 409
    missing = await client.post(
        "/api/v1/fleet/progress",
        json={**body, "run_id": "missing"},
        headers=worker.headers,
    )
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Not found"}
    no_run_id = await client.post(
        "/api/v1/fleet/progress",
        json={"activities": [_activity_json(1)]},
        headers=worker.headers,
    )
    assert no_run_id.status_code == 404
    assert no_run_id.json() == {"detail": "Not found"}
    hidden_by_project = await client.post(
        "/api/v1/fleet/progress",
        json=body,
        headers=other_project_worker.headers,
    )
    assert hidden_by_project.status_code == 404
    assert hidden_by_project.json() == missing.json()
    assert (
        await client.post(
            "/api/v1/fleet/progress",
            json=body,
            headers=worker.headers,
        )
    ).status_code == 202
    stale = {"run_id": claimed_task["run_id"], "activities": [_activity_json(1)]}
    assert (
        await client.post(
            "/api/v1/fleet/progress",
            json=stale,
            headers=worker.headers,
        )
    ).status_code == 409


@pytest.mark.asyncio
async def test_progress_enforces_batch_limits_and_approval(
    client: httpx.AsyncClient,
    worker: Worker,
    unapproved_worker: Worker,
    claimed_task: dict[str, str],
) -> None:
    too_many_invalid = {
        "run_id": claimed_task["run_id"],
        "activities": [{} for _ in range(51)],
    }
    assert (
        await client.post(
            "/api/v1/fleet/progress",
            json=too_many_invalid,
            headers=worker.headers,
        )
    ).status_code == 422
    too_many = {
        "run_id": claimed_task["run_id"],
        "activities": [_activity_json(n) for n in range(1, 52)],
    }
    assert (
        await client.post(
            "/api/v1/fleet/progress",
            json=too_many,
            headers=worker.headers,
        )
    ).status_code == 413
    oversized = _activity_json(1)
    oversized["summary"] = "€" * (240 * 1024)
    assert (
        await client.post(
            "/api/v1/fleet/progress",
            json={"run_id": claimed_task["run_id"], "activities": [oversized]},
            headers=worker.headers,
        )
    ).status_code == 413
    assert (
        await client.post(
            "/api/v1/fleet/progress",
            json={
                "run_id": claimed_task["run_id"],
                "activities": [_activity_json(1)],
            },
            headers=unapproved_worker.headers,
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_progress_rejects_invalid_activity_with_422(
    client: httpx.AsyncClient,
    worker: Worker,
    claimed_task: dict[str, str],
) -> None:
    response = await client.post(
        "/api/v1/fleet/progress",
        json={"run_id": claimed_task["run_id"], "activities": [{"bad": "payload"}]},
        headers=worker.headers,
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid activity"}


@pytest.mark.asyncio
async def test_progress_rejects_non_work_operator_task_as_identity_mismatch(
    client: httpx.AsyncClient,
    worker: Worker,
) -> None:
    enqueued = await client.post(
        "/api/v1/fleet/tasks",
        json={"payload": {"message": "plain fleet task"}},
    )
    assert enqueued.status_code == 201, enqueued.text
    run_id = enqueued.json()["run_id"]
    claimed = await client.post("/api/v1/fleet/claim", json={}, headers=worker.headers)
    assert claimed.status_code == 200, claimed.text

    response = await client.post(
        "/api/v1/fleet/progress",
        json={"run_id": run_id, "activities": [_activity_json(1)]},
        headers=worker.headers,
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "activity identity mismatch"}


@pytest.mark.asyncio
async def test_progress_returns_stored_last_sequence_after_cap_collapse(
    client: httpx.AsyncClient,
    worker: Worker,
    claimed_task: dict[str, str],
) -> None:
    response = await client.post(
        "/api/v1/fleet/progress",
        json={
            "run_id": claimed_task["run_id"],
            "activities": [
                _activity_json(ACTIVITY_ROW_CAP - 1),
                _activity_json(ACTIVITY_ROW_CAP),
                _activity_json(ACTIVITY_ROW_CAP + 1),
            ],
        },
        headers=worker.headers,
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": 3, "last_sequence": ACTIVITY_ROW_CAP}
