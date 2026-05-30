# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SubscriptionManager core lifecycle tests."""
from __future__ import annotations

import asyncio

import pytest

from sagewai.connections.subscriptions.errors import SubscriptionNotFoundError
from sagewai.connections.subscriptions.manager import SubscriptionManager


async def _settle():
    """Yield control so the background subscriber task runs."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_subscribe_returns_id_and_opens_plugin(fake_plugin, fake_clock, fake_connection):
    mgr = SubscriptionManager(time_source=fake_clock)
    sub_id = await mgr.subscribe(
        plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None
    )
    await _settle()
    assert isinstance(sub_id, str) and sub_id
    assert fake_plugin.opened == 1
    await mgr.aclose()


@pytest.mark.asyncio
async def test_drain_returns_buffered_events(fake_plugin, fake_clock, fake_connection):
    mgr = SubscriptionManager(time_source=fake_clock)
    sub_id = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None)
    await _settle()
    await fake_plugin.feed.put({"v": 1})
    await fake_plugin.feed.put({"v": 2})
    await _settle()

    result = await mgr.drain(sub_id, max_events=10)
    assert result.returned == 2
    assert result.remaining == 0
    assert [e["v"] for e in result.events] == [1, 2]
    await mgr.aclose()


@pytest.mark.asyncio
async def test_drain_respects_max_events(fake_plugin, fake_clock, fake_connection):
    mgr = SubscriptionManager(time_source=fake_clock)
    sub_id = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None)
    await _settle()
    for i in range(5):
        await fake_plugin.feed.put({"v": i})
    await _settle()

    result = await mgr.drain(sub_id, max_events=2)
    assert result.returned == 2
    assert result.remaining == 3
    await mgr.aclose()


@pytest.mark.asyncio
async def test_unsubscribe_closes_plugin_and_frees(fake_plugin, fake_clock, fake_connection):
    mgr = SubscriptionManager(time_source=fake_clock)
    sub_id = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None)
    await _settle()
    await mgr.unsubscribe(sub_id)
    assert fake_plugin.closed == 1
    with pytest.raises(SubscriptionNotFoundError):
        await mgr.drain(sub_id, max_events=1)
    await mgr.aclose()


@pytest.mark.asyncio
async def test_drain_unknown_id_raises(fake_clock):
    mgr = SubscriptionManager(time_source=fake_clock)
    with pytest.raises(SubscriptionNotFoundError):
        await mgr.drain("nope", max_events=1)
    await mgr.aclose()


@pytest.mark.asyncio
async def test_invalid_spec_raises_before_opening(fake_plugin, fake_clock, fake_connection):
    """A spec that fails the plugin's schema must not open a subscriber."""
    mgr = SubscriptionManager(time_source=fake_clock)
    with pytest.raises(Exception):  # pydantic ValidationError
        await mgr.subscribe(
            plugin=fake_plugin, connection=fake_connection,
            spec={"unknown_field": True}, ctx=None,
        )
    assert fake_plugin.opened == 0
    await mgr.aclose()


@pytest.mark.asyncio
async def test_stats_reports_buffer_depth(fake_plugin, fake_clock, fake_connection):
    mgr = SubscriptionManager(time_source=fake_clock)
    sub_id = await mgr.subscribe(plugin=fake_plugin, connection=fake_connection, spec={"name": "x"}, ctx=None)
    await _settle()
    await fake_plugin.feed.put({"v": 1})
    await _settle()
    st = mgr.stats(sub_id)
    assert st.buffer_depth == 1
    assert st.status == "active"
    assert sub_id in {s.subscription_id for s in mgr.list_subscriptions()}
    await mgr.aclose()
