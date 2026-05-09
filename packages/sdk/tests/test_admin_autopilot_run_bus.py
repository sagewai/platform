# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for MissionRunBus — in-process per-mission asyncio bus with replay."""

from __future__ import annotations

import asyncio

import pytest

from sagewai.admin.autopilot_run_bus import MissionRunBus


@pytest.mark.asyncio
async def test_subscribe_and_publish_fanout():
    bus = MissionRunBus()
    q1 = bus.subscribe("m1")
    q2 = bus.subscribe("m1")
    await bus.publish("m1", {"kind": "mission.started", "ts": "t0"})
    a = await asyncio.wait_for(q1.get(), 0.5)
    b = await asyncio.wait_for(q2.get(), 0.5)
    assert a == b and a["kind"] == "mission.started"


@pytest.mark.asyncio
async def test_replay_for_late_subscriber():
    bus = MissionRunBus()
    for i in range(3):
        await bus.publish("m1", {"kind": "agent.started", "ts": str(i)})
    q = bus.subscribe("m1")
    seen = [await q.get() for _ in range(3)]
    assert [e["ts"] for e in seen] == ["0", "1", "2"]


@pytest.mark.asyncio
async def test_ring_buffer_caps_at_1000():
    bus = MissionRunBus()
    for i in range(1100):
        await bus.publish("m1", {"kind": "agent.llm_call", "i": i})
    q = bus.subscribe("m1")
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert len(items) == 1000
    assert items[0]["i"] == 100


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    bus = MissionRunBus()
    q = bus.subscribe("m1")
    bus.unsubscribe("m1", q)
    await bus.publish("m1", {"kind": "x"})
    # q should remain empty after unsubscribe
    assert q.empty()


@pytest.mark.asyncio
async def test_unsubscribe_idempotent():
    bus = MissionRunBus()
    q = bus.subscribe("m1")
    bus.unsubscribe("m1", q)
    bus.unsubscribe("m1", q)  # must not raise


@pytest.mark.asyncio
async def test_isolation_across_mission_ids():
    bus = MissionRunBus()
    qa = bus.subscribe("a")
    qb = bus.subscribe("b")
    await bus.publish("a", {"kind": "x"})
    assert qb.empty()
    assert (await asyncio.wait_for(qa.get(), 0.5))["kind"] == "x"


def test_singleton():
    from sagewai.admin.autopilot_run_bus import get_run_bus
    assert get_run_bus() is get_run_bus()


@pytest.mark.asyncio
async def test_custom_ring_max():
    bus = MissionRunBus(ring_max=3)
    for i in range(5):
        await bus.publish("m1", {"i": i})
    q = bus.subscribe("m1")
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert [e["i"] for e in items] == [2, 3, 4]


@pytest.mark.asyncio
async def test_publish_before_any_subscriber():
    """publish with no subscribers must not raise and must populate ring buffer."""
    bus = MissionRunBus()
    await bus.publish("m1", {"kind": "early"})
    q = bus.subscribe("m1")
    item = q.get_nowait()
    assert item["kind"] == "early"


@pytest.mark.asyncio
async def test_multiple_missions_independent_buffers():
    """Each mission_id has its own ring buffer."""
    bus = MissionRunBus()
    await bus.publish("ma", {"kind": "a"})
    await bus.publish("mb", {"kind": "b"})
    qa = bus.subscribe("ma")
    qb = bus.subscribe("mb")
    assert qa.get_nowait()["kind"] == "a"
    assert qb.get_nowait()["kind"] == "b"
    assert qa.empty()
    assert qb.empty()
