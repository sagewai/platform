# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for FleetMissionAdapter capability matching + SSE events — Plan I Tasks 4+5."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from sagewai.autopilot._types import AgentKind, MissionState
from sagewai.autopilot.agent_graph import Agent, AgentGraph
from sagewai.autopilot.controller.fleet_adapter import FleetMissionAdapter
from sagewai.autopilot.errors import NoWorkerAvailableError
from sagewai.autopilot.mission import Mission
from sagewai.fleet.dispatcher import FleetDispatcher, InMemoryTaskStore
from sagewai.fleet.models import WorkerApprovalStatus, WorkerCapabilities, WorkerRecord
from sagewai.fleet.registry import InMemoryFleetRegistry


_NOW = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)


# ── fake registry ─────────────────────────────────────────────────────


class _FakeRegistry(InMemoryFleetRegistry):
    """InMemoryFleetRegistry with a pre-populated worker list."""

    def __init__(self, workers: list[WorkerRecord]) -> None:
        super().__init__()
        self._workers = workers

    async def list_workers(self, org_id: str, **_) -> list[WorkerRecord]:
        return list(self._workers)


def _worker(wid: str, labels: list[str] = (), models: list[str] = ()) -> WorkerRecord:
    return WorkerRecord(
        id=wid,
        name=wid,
        org_id="org1",
        capabilities=WorkerCapabilities(
            models_canonical=list(models),
            labels={t: "true" for t in labels},
        ),
        approval_status=WorkerApprovalStatus.APPROVED,
        registered_at=_NOW,
    )


def _agent(agent_id: str, tools: tuple[str, ...] = ()) -> Agent:
    return Agent(
        id=agent_id,
        kind=AgentKind.LLM,
        prompt_ref="test/prompt",
        tools=tools,
    )


def _mission(mid: str = "m1") -> Mission:
    return Mission(
        mission_id=mid,
        blueprint_id="bp1",
        blueprint_version="1.0",
        project_id="proj1",
        slots={},
    )


def _make_adapter(
    workers: list[WorkerRecord],
    events: list,
    poll_timeout: float = 0.1,
) -> FleetMissionAdapter:
    store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(store=store, poll_timeout=poll_timeout)
    registry = _FakeRegistry(workers)

    async def _emit(name: str, payload: dict) -> None:
        events.append({"kind": name, **payload})

    return FleetMissionAdapter(
        dispatcher=dispatcher,
        registry=registry,
        poll_timeout=poll_timeout,
        event_emitter=_emit,
    )


# ── tests ─────────────────────────────────────────────────────────────


async def test_dispatch_emits_dispatched_event_on_eligible_worker():
    """dispatched_to_worker event emitted before claim attempt."""
    events: list = []
    workers = [_worker("w1", labels=["web_search"])]
    adapter = _make_adapter(workers, events)
    mission = _mission()
    agent = _agent("step-1", tools=("web_search",))

    # Dispatch will emit dispatched_to_worker, then attempt claim (times out → skipped).
    result = await adapter.dispatch_step(agent, mission, {})

    dispatched = [e for e in events if e["kind"] == "agent.dispatched_to_worker"]
    assert len(dispatched) == 1
    assert dispatched[0]["step_id"] == "step-1"
    assert "w1" in dispatched[0]["eligible_worker_ids"]


async def test_dispatch_no_worker_raises_error_and_emits_event():
    """NoWorkerAvailableError raised when pool has no matching worker."""
    events: list = []
    workers = [_worker("w1", labels=["fetch_url"])]  # doesn't have web_search
    adapter = _make_adapter(workers, events)
    mission = _mission()
    agent = _agent("step-1", tools=("web_search",))

    with pytest.raises(NoWorkerAvailableError) as exc_info:
        await adapter.dispatch_step(agent, mission, {})

    assert exc_info.value.agent_id == "step-1"
    no_worker_events = [e for e in events if e["kind"] == "agent.no_worker_available"]
    assert len(no_worker_events) == 1
    assert "web_search" in no_worker_events[0]["unmet_capabilities"]["labels"]


@pytest.mark.xfail(
    reason=(
        "Empty-pool behaviour was redesigned in PR #268: an empty fleet "
        "pool falls through to claim/timeout (so non-fleet workers can "
        "still pick up the task), and only fails fast when the pool has "
        "workers but none of them match. This test contradicts the new "
        "design and is pre-existing red on main; tracked for cleanup."
    ),
    strict=False,
)
async def test_dispatch_empty_pool_raises_error():
    """NoWorkerAvailableError with empty unmet_capabilities when pool is empty."""
    events: list = []
    adapter = _make_adapter([], events)
    mission = _mission()
    agent = _agent("step-1", tools=())

    with pytest.raises(NoWorkerAvailableError):
        await adapter.dispatch_step(agent, mission, {})


async def test_task_enqueued_with_job_id():
    """Task dict includes a job_id field for Fleet post-mortem tracing."""
    events: list = []
    workers = [_worker("w1", labels=["web_search"])]
    store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(store=store, poll_timeout=0.1)
    registry = _FakeRegistry(workers)

    enqueued = []
    original_enqueue = store.enqueue

    def _capture(task):
        enqueued.append(dict(task))
        return original_enqueue(task)

    store.enqueue = _capture

    async def _emit(name: str, payload: dict) -> None:
        events.append({"kind": name, **payload})

    adapter = FleetMissionAdapter(
        dispatcher=dispatcher,
        registry=registry,
        poll_timeout=0.1,
        event_emitter=_emit,
    )

    mission = _mission()
    agent = _agent("step-1", tools=("web_search",))

    await adapter.dispatch_step(agent, mission, {})

    assert len(enqueued) == 1
    # job_id falls back to a generated UUID when mission has no run_id attribute
    assert "job_id" in enqueued[0]
    assert enqueued[0]["job_id"]
