# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Deterministic SQL-boundary tests for PostgreSQL workflow project scope."""

from __future__ import annotations

from typing import Any

import pytest

from sagewai.core.state import WorkflowRun
from sagewai.core.stores.postgres import PostgresStore


class _RecordingPool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(("execute", query, args))
        return "UPDATE 1"

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append(("fetchval", query, args))
        if "RETURNING run_id" in query:
            return args[2]
        return 0

    async def fetchrow(self, query: str, *args: Any) -> None:
        self.calls.append(("fetchrow", query, args))
        return None

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.calls.append(("fetch", query, args))
        return []


@pytest.mark.asyncio
async def test_enqueue_scopes_row_and_idempotency_identity() -> None:
    pool = _RecordingPool()
    store = PostgresStore(pool=pool)

    for project_id in ("alpha", "a:b", None):
        run = WorkflowRun(
            workflow_name="wf",
            run_id="same",
            project_id=project_id,
        )
        assert await store.enqueue_run(
            run,
            idempotency_key="caller",
            project_id=project_id,
        ) == ("same", True)

    args = [call[2] for call in pool.calls]
    assert args[0][0] == "7:p:alpha2:wfsame"
    assert args[0][5] == "7:p:alphacaller"
    assert args[1][0] == "5:p:a:b2:wfsame"
    assert args[1][5] == "5:p:a:bcaller"
    assert args[2][0] == "2:g:2:wfsame"
    assert args[2][5] == "2:g:caller"


@pytest.mark.asyncio
async def test_mutations_and_events_bind_explicit_project_scope() -> None:
    pool = _RecordingPool()
    store = PostgresStore(pool=pool)

    await store.complete_run("wf", "same", {}, project_id="alpha")
    await store.fail_run("wf", "same", "error", project_id="alpha")
    assert await store.cancel_run("wf", "same", project_id="alpha") is True
    await store.update_steps_completed(
        "wf", "same", 2, project_id="alpha"
    )
    await store.heartbeat("wf", "same", project_id="alpha")
    await store.persist_event(
        "same", "STEP", {"ok": True}, project_id="alpha"
    )
    assert await store.list_events("same", project_id="alpha") == []

    run_key = "7:p:alpha2:wfsame"
    mutation_calls = pool.calls[:5]
    assert all(call[2][0] == run_key for call in mutation_calls)
    assert all("project_id IS NOT DISTINCT FROM" in call[1] for call in mutation_calls)

    event_insert = pool.calls[5]
    assert event_insert[2][0:3] == ("alpha", "same", "STEP")
    event_list = pool.calls[6]
    assert event_list[2] == ("same", "alpha")
    assert "project_id IS NOT DISTINCT FROM $2" in event_list[1]


@pytest.mark.asyncio
async def test_claim_task_binds_worker_org_and_project_before_routing() -> None:
    pool = _RecordingPool()
    store = PostgresStore(pool=pool)

    assert await store.claim_task(
        "worker",
        "org",
        [],
        "pool",
        None,
        project_id="alpha",
    ) is None

    _kind, query, args = pool.calls[-1]
    assert args[:4] == ("worker", "org", "alpha", "pool")
    assert "(org_id IS NULL OR org_id = $2)" in query
    assert "project_id IS NOT DISTINCT FROM $3" in query
