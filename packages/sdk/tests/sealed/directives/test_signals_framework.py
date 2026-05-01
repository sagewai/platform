"""Signal-source framework — registry + SignalCollector behaviour."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

from sagewai.sealed.directives.models import SignalEvent
from sagewai.sealed.directives.signals import (
    SignalCollector,
    SignalContext,
    SignalSource,
    list_signal_sources,
    register_signal_source,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _signal(kind: str, run_id: str = "r-1") -> SignalEvent:
    return SignalEvent(
        kind=kind, run_id=run_id, project_id=None, workflow_name="wf",
        step_index=0, severity="info", detail="", evidence={}, emitted_at=_now(),
    )


@dataclass
class _FakeSource:
    name: str
    events: list[SignalEvent]
    raise_exc: Exception | None = None

    async def collect(self, *, run, step_index, context):
        if self.raise_exc:
            raise self.raise_exc
        return list(self.events)


def _fake_run() -> Any:
    @dataclass
    class R:
        run_id: str = "r-1"
        project_id: str | None = None
        workflow_name: str = "wf"
    return R()


def _ctx() -> SignalContext:
    return SignalContext(cost_tracker=None, audit_reader=None, store=None)


@pytest.mark.asyncio
async def test_collector_aggregates_events_from_multiple_sources():
    s1 = _FakeSource(name="a", events=[_signal("kind_a")])
    s2 = _FakeSource(name="b", events=[_signal("kind_b"), _signal("kind_b")])
    c = SignalCollector(sources=[s1, s2])
    events = await c.collect(run=_fake_run(), step_index=0, context=_ctx())
    assert [e.kind for e in events] == ["kind_a", "kind_b", "kind_b"]


@pytest.mark.asyncio
async def test_collector_skips_failing_source(caplog):
    s_ok = _FakeSource(name="ok", events=[_signal("ok")])
    s_bad = _FakeSource(name="bad", events=[], raise_exc=RuntimeError("boom"))
    c = SignalCollector(sources=[s_bad, s_ok])
    events = await c.collect(run=_fake_run(), step_index=0, context=_ctx())
    assert [e.kind for e in events] == ["ok"]
    assert any("Signal source 'bad'" in r.getMessage() for r in caplog.records)


def test_register_and_list_signal_sources():
    src = _FakeSource(name="zfresh", events=[])
    register_signal_source(src)
    names = [s.name for s in list_signal_sources()]
    assert "zfresh" in names


def test_signal_source_protocol_runtime_check():
    src = _FakeSource(name="x", events=[])
    assert isinstance(src, SignalSource)
