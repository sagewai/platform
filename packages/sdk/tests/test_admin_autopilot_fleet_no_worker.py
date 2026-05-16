# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for fail-fast on no matching worker — Plan I Task 6."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.autopilot._types import AgentKind, MissionState
from sagewai.autopilot.agent_graph import Agent
from sagewai.autopilot.controller.fleet_adapter import FleetMissionAdapter
from sagewai.autopilot.errors import NoWorkerAvailableError
from sagewai.autopilot.mission import Mission
from sagewai.fleet.dispatcher import FleetDispatcher, InMemoryTaskStore
from sagewai.fleet.models import WorkerApprovalStatus, WorkerCapabilities, WorkerRecord
from sagewai.fleet.registry import InMemoryFleetRegistry


_NOW = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)


class _FakeRegistry(InMemoryFleetRegistry):
    def __init__(self, workers: list[WorkerRecord]) -> None:
        super().__init__()
        self._workers = workers

    async def list_workers(self, org_id: str, **_) -> list[WorkerRecord]:
        return list(self._workers)


def _worker(wid: str, labels: list[str]) -> WorkerRecord:
    return WorkerRecord(
        id=wid,
        name=wid,
        org_id="org1",
        capabilities=WorkerCapabilities(labels={t: "true" for t in labels}),
        approval_status=WorkerApprovalStatus.APPROVED,
        registered_at=_NOW,
    )


def _agent(agent_id: str, tools: tuple[str, ...]) -> Agent:
    return Agent(id=agent_id, kind=AgentKind.LLM, prompt_ref="t", tools=tools)


def _mission() -> Mission:
    return Mission(
        mission_id="m1",
        blueprint_id="bp1",
        blueprint_version="1.0",
        project_id="proj1",
        slots={},
    )


def _adapter(workers: list[WorkerRecord], events: list) -> FleetMissionAdapter:
    store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(store=store, poll_timeout=0.1)

    async def _emit(name, payload):
        events.append({"kind": name, **payload})

    return FleetMissionAdapter(
        dispatcher=dispatcher,
        registry=_FakeRegistry(workers),
        poll_timeout=0.1,
        event_emitter=_emit,
    )


# ── tests ─────────────────────────────────────────────────────────────


async def test_missing_tool_raises_no_worker_error_and_emits_event():
    """Pool worker missing pdf_parse → NoWorkerAvailableError + no_worker_available event."""
    events: list = []
    workers = [_worker("w1", labels=["fetch_url"])]  # missing pdf_parse
    adapter = _adapter(workers, events)
    agent = _agent("step-pdf", tools=("pdf_parse",))

    with pytest.raises(NoWorkerAvailableError) as exc_info:
        await adapter.dispatch_step(agent, _mission(), {})

    err = exc_info.value
    assert err.agent_id == "step-pdf"
    assert "pdf_parse" in err.unmet_labels

    no_worker_ev = [e for e in events if e["kind"] == "agent.no_worker_available"]
    assert len(no_worker_ev) == 1
    assert "pdf_parse" in no_worker_ev[0]["unmet_capabilities"]["labels"]

    # No dispatched_to_worker event should have been emitted.
    dispatched = [e for e in events if e["kind"] == "agent.dispatched_to_worker"]
    assert dispatched == []


async def test_unmet_capabilities_include_set_difference():
    """unmet_labels is the set difference of required vs union of pool labels."""
    events: list = []
    workers = [_worker("w1", labels=["web_search"])]  # has web_search but not pdf_parse
    adapter = _adapter(workers, events)
    agent = _agent("step-both", tools=("web_search", "pdf_parse"))

    with pytest.raises(NoWorkerAvailableError) as exc_info:
        await adapter.dispatch_step(agent, _mission(), {})

    err = exc_info.value
    assert "pdf_parse" in err.unmet_labels
    assert "web_search" not in err.unmet_labels


@pytest.mark.xfail(
    reason=(
        "Empty-pool behaviour was redesigned in PR #268: an empty fleet "
        "pool falls through to claim/timeout instead of failing fast, so "
        "non-fleet workers can still pick up the task. This test "
        "contradicts the new design and is pre-existing red on main; "
        "tracked for cleanup."
    ),
    strict=False,
)
async def test_empty_pool_raises_no_worker_error():
    events: list = []
    adapter = _adapter([], events)
    agent = _agent("step-x", tools=("web_search",))

    with pytest.raises(NoWorkerAvailableError):
        await adapter.dispatch_step(agent, _mission(), {})
