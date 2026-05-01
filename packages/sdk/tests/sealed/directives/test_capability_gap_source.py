from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from sagewai.core.state import StepRecord, StepStatus
from sagewai.sealed.directives.signals import SignalContext
from sagewai.sealed.directives.sources.capability_gap import CapabilityGapSource


@dataclass
class _Run:
    run_id: str = "r-1"
    project_id: str | None = "p"
    workflow_name: str = "wf"
    steps: dict[str, StepRecord] = field(default_factory=dict)
    step_order: list[str] = field(default_factory=list)


class _StoreView:
    """Provides step_name_at_index for the source."""

    def step_name_at_index(self, run, idx: int) -> str | None:
        if 0 <= idx < len(run.step_order):
            return run.step_order[idx]
        return None

    def suggest_profile_for_key(self, key: str) -> str | None:
        if key == "CUSTOMER_DB_URL":
            return "customer-db"
        return None


def _ctx() -> SignalContext:
    return SignalContext(store=_StoreView())


def _failed_step(error_msg: str) -> StepRecord:
    return StepRecord(step_name="s1", status=StepStatus.FAILED, error=error_msg)


@pytest.mark.asyncio
async def test_emits_signal_for_missing_key_error():
    src = CapabilityGapSource()
    run = _Run(
        steps={"s1": _failed_step("MissingKeyError: CUSTOMER_DB_URL not in profile")},
        step_order=["s1"],
    )
    events = await src.collect(run=run, step_index=1, context=_ctx())
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "capability_gap"
    assert ev.evidence["missing_key"] == "CUSTOMER_DB_URL"
    assert ev.evidence["suggested_profile"] == "customer-db"
    assert ev.evidence["error_type"] == "MissingKeyError"


@pytest.mark.asyncio
async def test_no_signal_at_step_zero():
    src = CapabilityGapSource()
    run = _Run(steps={}, step_order=[])
    events = await src.collect(run=run, step_index=0, context=_ctx())
    assert events == []


@pytest.mark.asyncio
async def test_no_signal_when_last_step_succeeded():
    src = CapabilityGapSource()
    ok = StepRecord(step_name="s1", status=StepStatus.COMPLETED)
    run = _Run(steps={"s1": ok}, step_order=["s1"])
    events = await src.collect(run=run, step_index=1, context=_ctx())
    assert events == []


@pytest.mark.asyncio
async def test_no_signal_when_error_is_not_credential_related():
    src = CapabilityGapSource()
    run = _Run(
        steps={"s1": _failed_step("ValueError: bad input")},
        step_order=["s1"],
    )
    events = await src.collect(run=run, step_index=1, context=_ctx())
    assert events == []
