"""CostOverrunSource — emits when actual_cost > estimated × multiplier."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from sagewai.sealed.directives.signals import SignalContext
from sagewai.sealed.directives.sources.cost_overrun import (
    CostOverrunSource,
    CostTrackerView,
)


@dataclass
class _Run:
    run_id: str = "r-1"
    project_id: str | None = "p"
    workflow_name: str = "wf"
    estimated_cost_usd: float | None = 1.0


class _FakeTracker:
    def __init__(self, cost: float | None) -> None:
        self.cost = cost

    def get_run_cost_usd(self, run_id: str) -> float | None:
        return self.cost


def _ctx() -> SignalContext:
    return SignalContext()


@pytest.mark.asyncio
async def test_no_signal_when_under_threshold():
    src = CostOverrunSource(_FakeTracker(cost=2.0), multiplier=5.0)  # threshold=5.0
    events = await src.collect(run=_Run(), step_index=0, context=_ctx())
    assert events == []


@pytest.mark.asyncio
async def test_signal_when_over_threshold_warning_severity():
    src = CostOverrunSource(_FakeTracker(cost=6.0), multiplier=5.0)  # estimate=1, actual=6 → 6× → warning
    events = await src.collect(run=_Run(), step_index=0, context=_ctx())
    assert len(events) == 1
    assert events[0].kind == "cost_overrun"
    assert events[0].severity == "warning"
    assert events[0].evidence["actual_cost_usd"] == 6.0
    assert events[0].evidence["multiplier"] == 5.0


@pytest.mark.asyncio
async def test_critical_severity_when_actual_more_than_10x_estimate():
    src = CostOverrunSource(_FakeTracker(cost=12.0), multiplier=5.0)
    events = await src.collect(run=_Run(), step_index=0, context=_ctx())
    assert events[0].severity == "critical"


@pytest.mark.asyncio
async def test_no_signal_when_estimate_missing():
    src = CostOverrunSource(_FakeTracker(cost=100.0), multiplier=5.0)
    run = _Run(estimated_cost_usd=None)
    events = await src.collect(run=run, step_index=0, context=_ctx())
    assert events == []


@pytest.mark.asyncio
async def test_no_signal_when_actual_unknown():
    src = CostOverrunSource(_FakeTracker(cost=None), multiplier=5.0)
    events = await src.collect(run=_Run(), step_index=0, context=_ctx())
    assert events == []
