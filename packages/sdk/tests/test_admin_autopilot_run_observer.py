# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for ``run_mission_with_observer`` — Plan H Task 3.

These tests exercise the translator that turns ``MissionDriver``
telemetry into ``MissionRunBus`` events.  We build canned
``MissionRunResult`` instances and a fake driver to keep the tests
free of LLM provider configuration; the production
:class:`~sagewai.autopilot.controller.driver.MissionDriver` requires
real provider wiring.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sagewai.admin.autopilot_run_bus import MissionRunBus
from sagewai.admin.autopilot_run_observer import (
    StepEmitter,
    run_mission_with_observer,
)
from sagewai.autopilot.controller.types import (
    MissionRunResult,
    StepResult,
    StepTelemetry,
)


# ── helpers ────────────────────────────────────────────────────────


class _FakeDriver:
    """Minimal stand-in for :class:`MissionDriver` that returns a canned result."""

    def __init__(self, result: MissionRunResult | None = None, *, raises: BaseException | None = None) -> None:
        self._result = result
        self._raises = raises

    async def execute(self, mission: Any) -> MissionRunResult:
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


def _bp(*, bp_id: str = "bp-1", name: str = "Demo Blueprint") -> Any:
    """Duck-typed blueprint with only the fields the observer reads."""
    return SimpleNamespace(id=bp_id, name=name, title=name)


def _mission() -> Any:
    """Duck-typed mission — observer never touches it; driver is faked."""
    return SimpleNamespace(mission_id="mission-x")


async def _drain(bus: MissionRunBus, mission_id: str) -> list[dict[str, Any]]:
    """Subscribe and synchronously drain whatever is in the ring buffer."""
    q = bus.subscribe(mission_id)
    out: list[dict[str, Any]] = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _step(
    *,
    node_id: str = "n",
    status: str = "completed",
    output_preview: str | None = "ok",
    output: str | None = None,
    tool_calls: tuple[str, ...] | None = None,
    telemetry: StepTelemetry | None = None,
) -> StepResult:
    return StepResult(
        node_id=node_id,
        status=status,
        output_preview=output_preview,
        output=output,
        tool_calls=tool_calls,
        telemetry=telemetry,
    )


# ── tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emits_mission_started_first_with_blueprint_metadata():
    bus = MissionRunBus()
    result = MissionRunResult(
        mission_id="m1",
        status="completed",
        steps=(_step(node_id="a"),),
        duration_seconds=0.01,
        error=None,
    )
    driver = _FakeDriver(result)
    summary = await run_mission_with_observer(
        bus=bus,
        mission_id="m1",
        run_id="r1",
        blueprint=_bp(bp_id="bp-42", name="My BP"),
        mission=_mission(),
        driver=driver,
    )
    events = await _drain(bus, "m1")
    assert events[0]["kind"] == "mission.started"
    assert events[0]["blueprint_id"] == "bp-42"
    assert events[0]["blueprint_name"] == "My BP"
    assert summary["status"] == "completed"


@pytest.mark.asyncio
async def test_emits_per_step_events_in_order():
    bus = MissionRunBus()
    tel = StepTelemetry(model_used="haiku", input_tokens=10, output_tokens=5, cost_usd=0.001, latency_ms=12.0)
    s1 = _step(node_id="n1", tool_calls=("search", "fetch"), telemetry=tel)
    s2 = _step(node_id="n2", tool_calls=("write",), telemetry=tel, output="hello")
    result = MissionRunResult(
        mission_id="m1",
        status="completed",
        steps=(s1, s2),
        duration_seconds=0.02,
        error=None,
    )
    await run_mission_with_observer(
        bus=bus,
        mission_id="m1",
        run_id="r1",
        blueprint=_bp(),
        mission=_mission(),
        driver=_FakeDriver(result),
    )
    kinds = [e["kind"] for e in await _drain(bus, "m1")]
    expected = [
        "mission.started",
        # s1
        "agent.started",
        "agent.tool_call",
        "agent.tool_call",
        "agent.tool_result",
        "agent.tool_result",
        "agent.llm_call",
        "agent.finished",
        # s2
        "agent.started",
        "agent.tool_call",
        "agent.tool_result",
        "agent.llm_call",
        "agent.finished",
        "mission.finished",
    ]
    assert kinds == expected


@pytest.mark.asyncio
async def test_total_cost_matches_sum_of_step_telemetry():
    bus = MissionRunBus()
    steps = tuple(
        _step(
            node_id=f"n{i}",
            telemetry=StepTelemetry(model_used="m", cost_usd=c),
        )
        for i, c in enumerate([0.001, 0.002, 0.003])
    )
    result = MissionRunResult(
        mission_id="m1",
        status="completed",
        steps=steps,
        duration_seconds=0.03,
        error=None,
    )
    summary = await run_mission_with_observer(
        bus=bus,
        mission_id="m1",
        run_id="r1",
        blueprint=_bp(),
        mission=_mission(),
        driver=_FakeDriver(result),
    )
    assert summary["total_cost_usd"] == pytest.approx(0.006, abs=1e-9)
    events = await _drain(bus, "m1")
    finished = [e for e in events if e["kind"] == "mission.finished"][0]
    assert finished["total_cost_usd"] == pytest.approx(0.006, abs=1e-9)


@pytest.mark.asyncio
async def test_failed_mission_emits_tool_failed_for_failing_step():
    bus = MissionRunBus()
    bad = _step(node_id="boom", status="failed", tool_calls=("explode",), output_preview="nope")
    result = MissionRunResult(
        mission_id="m1",
        status="failed",
        steps=(bad,),
        duration_seconds=0.01,
        error="bang",
    )
    summary = await run_mission_with_observer(
        bus=bus,
        mission_id="m1",
        run_id="r1",
        blueprint=_bp(),
        mission=_mission(),
        driver=_FakeDriver(result),
    )
    events = await _drain(bus, "m1")
    failed = [e for e in events if e["kind"] == "agent.tool_failed"]
    assert len(failed) == 1
    assert failed[0]["tool"] == "explode"
    assert failed[0]["error"] == "bang"
    finished = [e for e in events if e["kind"] == "mission.finished"][0]
    assert finished["status"] == "failed"
    assert finished["error"] == "bang"
    assert summary["status"] == "failed"
    assert summary["error"] == "bang"
    # No success tool_result events for a failed step
    assert not any(e["kind"] == "agent.tool_result" for e in events)


@pytest.mark.asyncio
async def test_no_telemetry_yields_no_llm_call_event():
    bus = MissionRunBus()
    s = _step(node_id="n1", telemetry=None, tool_calls=None)
    result = MissionRunResult(
        mission_id="m1",
        status="completed",
        steps=(s,),
        duration_seconds=0.01,
    )
    await run_mission_with_observer(
        bus=bus,
        mission_id="m1",
        run_id="r1",
        blueprint=_bp(),
        mission=_mission(),
        driver=_FakeDriver(result),
    )
    events = await _drain(bus, "m1")
    kinds = [e["kind"] for e in events]
    assert "agent.llm_call" not in kinds
    assert "agent.started" in kinds
    assert "agent.finished" in kinds


@pytest.mark.asyncio
async def test_no_tool_calls_skips_tool_events():
    bus = MissionRunBus()
    s = _step(node_id="n1", tool_calls=None)
    result = MissionRunResult(
        mission_id="m1",
        status="completed",
        steps=(s,),
        duration_seconds=0.01,
    )
    await run_mission_with_observer(
        bus=bus,
        mission_id="m1",
        run_id="r1",
        blueprint=_bp(),
        mission=_mission(),
        driver=_FakeDriver(result),
    )
    kinds = [e["kind"] for e in await _drain(bus, "m1")]
    for forbidden in ("agent.tool_call", "agent.tool_result", "agent.tool_failed"):
        assert forbidden not in kinds


@pytest.mark.asyncio
async def test_step_emitter_override():
    bus = MissionRunBus()
    s = _step(node_id="n1")
    result = MissionRunResult(
        mission_id="m1",
        status="completed",
        steps=(s,),
        duration_seconds=0.01,
    )

    async def custom_emitter(*, bus: MissionRunBus, mission_id: str, run_id: str, step: StepResult) -> float:
        from sagewai.admin.autopilot_run_observer import _ev  # internal helper

        await bus.publish(mission_id, _ev("custom.sentinel", mission_id, run_id, node_id=step.node_id))
        return 0.42

    summary = await run_mission_with_observer(
        bus=bus,
        mission_id="m1",
        run_id="r1",
        blueprint=_bp(),
        mission=_mission(),
        driver=_FakeDriver(result),
        step_emitter=custom_emitter,
    )
    events = await _drain(bus, "m1")
    kinds = [e["kind"] for e in events]
    assert kinds == ["mission.started", "custom.sentinel", "mission.finished"]
    assert summary["total_cost_usd"] == pytest.approx(0.42, abs=1e-9)


@pytest.mark.asyncio
async def test_event_id_unique():
    bus = MissionRunBus()
    tel = StepTelemetry(model_used="m", cost_usd=0.001)
    steps = (
        _step(node_id="a", tool_calls=("t1", "t2"), telemetry=tel),
        _step(node_id="b", tool_calls=("t3",), telemetry=tel),
    )
    result = MissionRunResult(
        mission_id="m1",
        status="completed",
        steps=steps,
        duration_seconds=0.01,
    )
    await run_mission_with_observer(
        bus=bus,
        mission_id="m1",
        run_id="r1",
        blueprint=_bp(),
        mission=_mission(),
        driver=_FakeDriver(result),
    )
    events = await _drain(bus, "m1")
    ids = [e["event_id"] for e in events]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_event_envelope_shape():
    bus = MissionRunBus()
    tel = StepTelemetry(model_used="m", cost_usd=0.001)
    s = _step(node_id="a", tool_calls=("t1",), telemetry=tel)
    result = MissionRunResult(
        mission_id="m1",
        status="completed",
        steps=(s,),
        duration_seconds=0.01,
    )
    await run_mission_with_observer(
        bus=bus,
        mission_id="m1",
        run_id="r1",
        blueprint=_bp(),
        mission=_mission(),
        driver=_FakeDriver(result),
    )
    events = await _drain(bus, "m1")
    for e in events:
        for key in ("event_id", "ts", "mission_id", "run_id", "kind"):
            assert key in e, f"missing {key} in {e!r}"
        assert e["mission_id"] == "m1"
        assert e["run_id"] == "r1"


@pytest.mark.asyncio
async def test_driver_exception_emits_mission_finished_failed():
    bus = MissionRunBus()
    driver = _FakeDriver(raises=RuntimeError("boom"))
    summary = await run_mission_with_observer(
        bus=bus,
        mission_id="m1",
        run_id="r1",
        blueprint=_bp(),
        mission=_mission(),
        driver=driver,
    )
    events = await _drain(bus, "m1")
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "mission.started"
    assert kinds[-1] == "mission.finished"
    finished = events[-1]
    assert finished["status"] == "failed"
    assert "boom" in finished["error"]
    assert summary["status"] == "failed"
    assert "boom" in summary["error"]


def test_step_emitter_protocol_is_exported():
    """``StepEmitter`` must be importable for Plans I/J/K to type their wrappers."""
    assert StepEmitter is not None
