# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the mission lifecycle pub/sub bus."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from sagewai.admin.autopilot_lifecycle_bus import LifecycleBus, MissionStatusChanged


def _event(mission_id: str = "m1", old: str = "pending", new: str = "running") -> MissionStatusChanged:
    return MissionStatusChanged(
        mission_id=mission_id,
        old_status=old,
        new_status=new,
        ts=datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_two_subscribers_both_receive():
    bus = LifecycleBus()
    received_a: list[MissionStatusChanged] = []
    received_b: list[MissionStatusChanged] = []

    async def consume(collector):
        async for evt in bus.subscribe("org-1"):
            collector.append(evt)
            return  # exit after first event

    task_a = asyncio.create_task(consume(received_a))
    task_b = asyncio.create_task(consume(received_b))
    await asyncio.sleep(0)  # let subscriptions register

    evt = _event()
    await bus.publish("org-1", evt)
    await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=2)

    assert len(received_a) == 1
    assert len(received_b) == 1
    assert received_a[0].mission_id == "m1"
    assert received_b[0].mission_id == "m1"


@pytest.mark.asyncio
async def test_org_isolation():
    bus = LifecycleBus()
    received: list[MissionStatusChanged] = []

    async def consume():
        async for evt in bus.subscribe("org-B"):
            received.append(evt)
            return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)

    # Publish to a different org — subscriber on org-B must not receive it
    await bus.publish("org-A", _event())
    # Give the event loop a chance to propagate (it shouldn't)
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert received == []


@pytest.mark.asyncio
async def test_cancelled_subscription_does_not_raise():
    bus = LifecycleBus()

    async def consume():
        async for _ in bus.subscribe("org-1"):
            pass  # pragma: no cover

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)  # let it register

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Publish after the subscriber is gone — must not raise
    await bus.publish("org-1", _event())
