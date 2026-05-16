# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from sagewai.sealed.directives.signals import SignalContext
from sagewai.sealed.directives.sources.rotation_drift import RotationDriftSource


@dataclass
class _Event:
    id: int
    profile_id: str
    details: dict[str, Any] = field(default_factory=dict)


class _AuditReader:
    def __init__(self, events: list[_Event]) -> None:
        self.events = events

    async def recent_events(self, *, event_type: str, run_id: str, since=None):
        return list(self.events)


@dataclass
class _Run:
    run_id: str = "r-1"
    project_id: str | None = "p"
    workflow_name: str = "wf"
    started_at: float | None = 100.0
    signals: dict[str, Any] = field(default_factory=dict)


def _ctx(events: list[_Event]) -> SignalContext:
    return SignalContext(audit_reader=_AuditReader(events))


@pytest.mark.asyncio
async def test_emits_signal_for_each_new_drift_event():
    events = [
        _Event(id=1, profile_id="acme", details={"drift_diff": {"added": ["X"]}}),
        _Event(id=2, profile_id="acme", details={"drift_diff": {"removed": ["Y"]}}),
    ]
    src = RotationDriftSource()
    run = _Run()
    sig = await src.collect(run=run, step_index=0, context=_ctx(events))
    assert len(sig) == 2
    assert sig[0].kind == "rotation_drift"
    assert run.signals["rotation_drift_seen_event_ids"] == [1, 2]


@pytest.mark.asyncio
async def test_does_not_re_emit_seen_events():
    events = [_Event(id=1, profile_id="acme")]
    src = RotationDriftSource()
    run = _Run(signals={"rotation_drift_seen_event_ids": [1]})
    sig = await src.collect(run=run, step_index=0, context=_ctx(events))
    assert sig == []


@pytest.mark.asyncio
async def test_no_signal_when_no_audit_events():
    src = RotationDriftSource()
    run = _Run()
    sig = await src.collect(run=run, step_index=0, context=_ctx([]))
    assert sig == []
