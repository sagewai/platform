# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SubscriptionManager memory-safety + reaper + teardown tests."""
from __future__ import annotations

import asyncio

import pytest

from sagewai.connections.subscriptions.errors import (
    SubscriptionLimitExceededError,
    SubscriptionNotFoundError,
)
from sagewai.connections.subscriptions.manager import (
    SubscriptionManager,
    _event_bytes,
)


async def _settle():
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ── three nested bounds ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ring_buffer_drops_oldest_at_capacity(fake_plugin, fake_clock, fake_connection):
    mgr = SubscriptionManager(time_source=fake_clock, max_events_per_subscription=3)
    sub_id = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None)
    await _settle()
    for i in range(5):  # 5 into a depth-3 ring
        await fake_plugin.feed.put({"v": i})
    await _settle()
    result = await mgr.drain(sub_id, max_events=10)
    assert result.returned == 3
    assert [e["v"] for e in result.events] == [2, 3, 4]  # oldest two evicted
    assert result.overflow_dropped == 2
    await mgr.aclose()


@pytest.mark.asyncio
async def test_oversized_event_rejected(fake_plugin, fake_clock, fake_connection):
    # max_event_bytes=1000 sits cleanly between a minimal-dict event (a few
    # hundred bytes of `sys.getsizeof` overhead, version-dependent) and the
    # 5 KiB payload below — robust across Python 3.10..3.14.
    mgr = SubscriptionManager(time_source=fake_clock, max_event_bytes=1000)
    sub_id = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None)
    await _settle()
    await fake_plugin.feed.put({"big": "x" * 5000})  # well over 1000 bytes
    await fake_plugin.feed.put({"ok": 1})
    await _settle()
    result = await mgr.drain(sub_id, max_events=10)
    assert result.oversized_dropped == 1
    assert result.returned == 1  # only the small event buffered
    await mgr.aclose()


@pytest.mark.asyncio
async def test_global_pressure_ceiling(fake_plugin, fake_clock, fake_connection):
    # Tiny global ceiling: first event fits, second trips it.
    mgr = SubscriptionManager(time_source=fake_clock, max_total_buffered_bytes=400, max_event_bytes=10_000)
    sub_id = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None)
    await _settle()
    await fake_plugin.feed.put({"a": "x" * 100})
    await fake_plugin.feed.put({"b": "x" * 100})
    await fake_plugin.feed.put({"c": "x" * 100})
    await _settle()
    result = await mgr.drain(sub_id, max_events=10)
    assert result.global_pressure_dropped >= 1
    await mgr.aclose()


@pytest.mark.asyncio
async def test_max_active_subscriptions(fake_plugin, fake_clock, fake_connection):
    mgr = SubscriptionManager(time_source=fake_clock, max_active_subscriptions=2)
    await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "a"}, ctx=None)
    await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "b"}, ctx=None)
    with pytest.raises(SubscriptionLimitExceededError):
        await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "c"}, ctx=None)
    await mgr.aclose()


@pytest.mark.asyncio
async def test_per_subscription_cap_clamps_not_raises(fake_plugin, fake_clock, fake_connection):
    """A spec requesting MORE than the hard cap is clamped down."""
    mgr = SubscriptionManager(time_source=fake_clock, max_events_per_subscription=3)
    sub_id = await mgr.subscribe(
        plugin=fake_plugin, connection=fake_connection,
        spec={"name": "x", "max_events_per_subscription": 9999}, ctx=None,
    )
    await _settle()
    for i in range(5):
        await fake_plugin.feed.put({"v": i})
    await _settle()
    result = await mgr.drain(sub_id, max_events=99)
    assert result.returned == 3  # clamped to the hard cap, not 9999
    await mgr.aclose()


@pytest.mark.asyncio
async def test_max_events_floored_to_at_least_one(fake_plugin, fake_clock, fake_connection):
    """A spec requesting max_events_per_subscription=0 must NOT produce a
    deque(maxlen=0) that silently black-holes every event — the ring is
    floored to hold at least 1. (A negative request would otherwise raise a
    raw ValueError at deque construction; the floor prevents that too.)"""
    mgr = SubscriptionManager(time_source=fake_clock, max_events_per_subscription=3)
    sub_id = await mgr.subscribe(
        plugin=fake_plugin, connection=fake_connection,
        spec={"name": "x", "max_events_per_subscription": 0}, ctx=None,
    )
    await _settle()
    assert mgr._subs[sub_id].buffer.maxlen == 1  # floored, not 0
    for i in range(3):
        await fake_plugin.feed.put({"v": i})
    await _settle()
    result = await mgr.drain(sub_id, max_events=99)
    assert result.returned == 1  # buffers at least one (the newest)
    assert result.events[0]["v"] == 2
    await mgr.aclose()


@pytest.mark.asyncio
async def test_byte_accounting_balances_through_eviction_and_drain(
    fake_plugin, fake_clock, fake_connection
):
    """Every _total_bytes increment has a matching decrement: after an
    eviction-heavy fill and a full drain, both the per-sub and process-wide
    counters return to zero."""
    mgr = SubscriptionManager(time_source=fake_clock, max_events_per_subscription=3)
    sub_id = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None)
    await _settle()
    for i in range(10):  # 10 into a depth-3 ring → 7 evictions
        await fake_plugin.feed.put({"v": i})
    await _settle()
    # Buffer holds 3; total/per-sub bytes must equal the sum of the 3 held.
    sub = mgr._subs[sub_id]
    assert len(sub.buffer) == 3
    assert sub.bytes_buffered > 0
    assert mgr._total_bytes == sub.bytes_buffered
    # Drain everything → both counters return to exactly zero.
    result = await mgr.drain(sub_id, max_events=99)
    assert result.returned == 3
    assert sub.bytes_buffered == 0
    assert mgr._total_bytes == 0
    await mgr.aclose()
    assert mgr._total_bytes == 0


@pytest.mark.asyncio
async def test_full_ring_under_global_pressure_still_accepts_via_eviction(
    fake_plugin, fake_clock, fake_connection
):
    """A full drop_oldest ring must keep accepting new events under modest
    global pressure — the append evicts an old event, so net bytes stay flat.
    Regression: previously the gross global-pressure check made a full ring
    go permanently stale."""
    # Ring depth 2; global ceiling just above 2 similar events' bytes.
    # Sized so 2 events fit, but a naive gross check on a 3rd (without
    # eviction credit) would trip it: ceiling = 2.5x a sample event, so
    # 2 events (2.0x) fit, a gross 3rd (3.0x) trips, but the net check (with
    # eviction credit, stays at 2.0x) keeps accepting.
    sample = _event_bytes({"v": 0})
    mgr = SubscriptionManager(
        time_source=fake_clock,
        max_events_per_subscription=2,
        max_event_bytes=10_000,
        max_total_buffered_bytes=int(sample * 2.5),
    )
    sub_id = await mgr.subscribe(
        plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None
    )
    await _settle()
    # Fill the ring
    await fake_plugin.feed.put({"v": 0})
    await fake_plugin.feed.put({"v": 1})
    await _settle()
    # Now push more — these evict oldest, net bytes flat, must be ACCEPTED not
    # pressure-dropped.
    await fake_plugin.feed.put({"v": 2})
    await fake_plugin.feed.put({"v": 3})
    await _settle()
    result = await mgr.drain(sub_id, max_events=10)
    # The newest 2 survived (drop_oldest), NO global-pressure drops occurred.
    assert [e["v"] for e in result.events] == [2, 3]
    assert result.global_pressure_dropped == 0
    assert result.overflow_dropped == 2  # the two oldest evicted
    # Byte accounting balances back to zero after the full drain.
    assert mgr._total_bytes == 0
    await mgr.aclose()
    assert mgr._total_bytes == 0


# ── idle reaper ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idle_reaper_unsubscribes_stale(fake_plugin, fake_clock, fake_connection, caplog):
    import logging

    mgr = SubscriptionManager(time_source=fake_clock, idle_ttl_seconds=600)
    sub_id = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None)
    await _settle()
    # Advance clock past the idle TTL with no drain.
    fake_clock.advance(601)
    with caplog.at_level(logging.INFO, logger="sagewai.connections.subscriptions.manager"):
        await mgr._reap_idle_once()
    assert fake_plugin.closed == 1
    with pytest.raises(SubscriptionNotFoundError):
        mgr.stats(sub_id)
    assert any(getattr(r, "event", None) == "subscription.idle_reaped" for r in caplog.records)
    await mgr.aclose()


@pytest.mark.asyncio
async def test_idle_reaper_spares_recently_drained(fake_plugin, fake_clock, fake_connection):
    mgr = SubscriptionManager(time_source=fake_clock, idle_ttl_seconds=600)
    sub_id = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None)
    await _settle()
    fake_clock.advance(300)
    await mgr.drain(sub_id, max_events=1)   # resets last_drain_at
    fake_clock.advance(300)                 # only 300s since the drain
    await mgr._reap_idle_once()
    assert mgr.stats(sub_id).status == "active"  # spared
    await mgr.aclose()


# ── dead-task reaper ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dead_task_reaper_marks_failed_after_reconnect_exhaustion(
    fake_plugin, fake_clock, fake_connection
):
    """Deterministic via `crash_on_open`: every respawned subscriber task
    crashes immediately on open (before awaiting the empty feed), so each
    `_reap_dead_once` tick reliably sees a done-and-crashed task and the
    bounded-reconnect counter advances every tick until exhaustion."""
    mgr = SubscriptionManager(time_source=fake_clock, max_reconnect_attempts=2)
    fake_plugin.crash_on_open = True  # every open() crashes immediately
    sub_id = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None)
    await _settle()   # first subscriber crashes on open
    assert mgr.stats(sub_id).status == "reconnecting"
    # Run the dead-task reaper enough times to exhaust reconnects. Each tick
    # respawns; the respawn crashes again during _settle().
    for _ in range(5):
        await mgr._reap_dead_once()
        await _settle()
    st = mgr.stats(sub_id)
    assert st.status == "failed"
    await mgr.aclose()


@pytest.mark.asyncio
async def test_dead_task_reaper_recovers_when_open_succeeds(
    fake_plugin, fake_clock, fake_connection
):
    """A crashed subscriber that recovers on respawn (open no longer crashes)
    is restarted and resumes emitting — it does NOT reach `failed`."""
    mgr = SubscriptionManager(time_source=fake_clock, max_reconnect_attempts=3)
    fake_plugin.crash_on_open = True
    sub_id = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None)
    await _settle()
    assert mgr.stats(sub_id).status == "reconnecting"
    # The next respawn succeeds (broker came back).
    fake_plugin.crash_on_open = False
    await mgr._reap_dead_once()
    await _settle()
    # The respawned task is now running (blocked on the feed), not failed.
    st = mgr.stats(sub_id)
    assert st.status == "reconnecting"  # status not bumped back to active until a drain/event path; still alive
    # Prove it's live: feed an event, it lands in the buffer.
    await fake_plugin.feed.put({"v": 1})
    await _settle()
    result = await mgr.drain(sub_id, max_events=10)
    assert result.returned == 1
    await mgr.aclose()


# ── total teardown ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aclose_cancels_all_and_clears(fake_plugin, fake_clock, fake_connection):
    mgr = SubscriptionManager(time_source=fake_clock)
    s1 = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "a"}, ctx=None)
    s2 = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "b"}, ctx=None)
    await _settle()
    await mgr.aclose()
    assert mgr.list_subscriptions() == []
    # both plugins torn down (closed >= opened)
    assert fake_plugin.closed >= 1
    with pytest.raises(SubscriptionNotFoundError):
        mgr.stats(s1)
    with pytest.raises(SubscriptionNotFoundError):
        mgr.stats(s2)


# ── reaper respawns with the subscribe-time ctx (issue #378) ───────────


@pytest.mark.asyncio
async def test_dead_task_reaper_respawns_with_subscription_ctx(fake_clock, fake_connection):
    """Issue #378: the dead-task reaper respawns a crashed subscriber with
    the SAME ctx captured at subscribe time (stored on ``ActiveSubscription``).
    Without this, a subscriber that crashes-and-reconnects would lose its
    credential context and re-emit ciphertext onto the wire."""
    from pydantic import BaseModel, ConfigDict

    seen_ctx: list = []
    sentinel = object()  # a stand-in for the real PluginContext

    class _CtxRecordingPlugin:
        def subscription_spec_schema(self):
            class _S(BaseModel):
                model_config = ConfigDict(extra="forbid")
                name: str = "x"

            return _S

        async def open_subscription(self, connection, *, spec, emit, ctx):
            seen_ctx.append(ctx)
            raise RuntimeError("synthetic crash to force a reaper respawn")

        async def close_subscription(self, connection, *, spec):
            return None

    mgr = SubscriptionManager(time_source=fake_clock, max_reconnect_attempts=3)
    sub_id = await mgr.subscribe(
        plugin=_CtxRecordingPlugin(), connection=fake_connection,
        spec={"name": "x"}, ctx=sentinel,
    )
    await _settle()  # first run crashes
    assert mgr.stats(sub_id).status == "reconnecting"
    await mgr._reap_dead_once()  # respawn
    await _settle()

    assert len(seen_ctx) >= 2  # first run + at least one respawn
    assert all(c is sentinel for c in seen_ctx)  # same ctx every time
    await mgr.aclose()
