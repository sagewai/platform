# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""MQTT SubscriptionPlugin half — open/close + emit shape (mocked aiomqtt)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.connections.models import Connection
from sagewai.connections.protocols.mqtt import MqttProtocolPlugin
from sagewai.connections.subscriptions.base import EmitResult


def _conn(pd: dict) -> Connection:
    now = datetime.now(timezone.utc).isoformat()
    return Connection(
        id="c", protocol="mqtt", project_id="p", display_name="b", tags=(),
        credentials_backend={"kind": "local"}, status="ready", last_tested_at=None,
        last_test_ok=None, is_default=False, created_at=now, updated_at=now,
        last_error=None, protocol_data=pd,
    )


class _FakeMsg:
    """Mirror of aiomqtt.Message's surface (topic/payload/qos/retain)."""

    def __init__(self, topic, payload, qos=0, retain=False):
        self.topic = topic
        self.payload = payload
        self.qos = qos
        self.retain = retain


def _fake_client_with_messages(messages):
    """Build an aiomqtt.Client mock: ``async with Client(...) as c``,
    ``await c.subscribe(...)``, ``async for m in c.messages``."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.subscribe = AsyncMock()

    async def _gen():
        for m in messages:
            yield m
        # then block forever (a real broker keeps the iterator open)
        await asyncio.Event().wait()

    client.messages = _gen()
    return client


async def _drive_until(task, predicate, ticks=20):
    """Yield the event loop until ``predicate()`` is true (or ticks run out),
    then cancel + await the task. No real sleeps."""
    for _ in range(ticks):
        await asyncio.sleep(0)
        if predicate():
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_open_subscription_emits_message_shape():
    conn = _conn({"host": "broker", "port": 1883})
    msgs = [_FakeMsg(topic="sensors/1/temp", payload=b'{"v":22.5}', qos=1, retain=False)]
    client = _fake_client_with_messages(msgs)

    emitted = []

    def emit(event):
        emitted.append(event)
        return EmitResult.ACCEPTED

    plugin = MqttProtocolPlugin()
    with patch("aiomqtt.Client", return_value=client):
        task = asyncio.ensure_future(
            plugin.open_subscription(
                conn, spec={"topic_filter": "sensors/+/temp", "qos": 1},
                emit=emit, ctx=MagicMock(),
            )
        )
        await _drive_until(task, lambda: bool(emitted))

    assert len(emitted) == 1
    ev = emitted[0]
    assert ev["topic"] == "sensors/1/temp"
    assert ev["payload"] == '{"v":22.5}'  # bytes decoded utf-8
    assert ev["qos"] == 1
    assert ev["retain"] is False
    assert "timestamp" in ev
    client.subscribe.assert_awaited()  # subscribed to the topic_filter


@pytest.mark.asyncio
async def test_open_subscription_subscribes_to_topic_filter():
    conn = _conn({"host": "broker"})
    client = _fake_client_with_messages([])
    plugin = MqttProtocolPlugin()
    with patch("aiomqtt.Client", return_value=client):
        task = asyncio.ensure_future(
            plugin.open_subscription(
                conn, spec={"topic_filter": "fleet/#", "qos": 2},
                emit=lambda e: EmitResult.ACCEPTED, ctx=MagicMock(),
            )
        )
        await _drive_until(task, lambda: client.subscribe.await_count > 0)
    # subscribe called with the topic filter + qos.
    args, kwargs = client.subscribe.await_args
    assert "fleet/#" in args or kwargs.get("topic") == "fleet/#"
    assert kwargs.get("qos") == 2 or 2 in args


@pytest.mark.asyncio
async def test_open_subscription_binary_payload_replacement_decoded():
    conn = _conn({"host": "broker"})
    msgs = [_FakeMsg(topic="t", payload=b"\xff\xfe", qos=0)]
    client = _fake_client_with_messages(msgs)
    emitted = []
    plugin = MqttProtocolPlugin()
    with patch("aiomqtt.Client", return_value=client):
        task = asyncio.ensure_future(
            plugin.open_subscription(
                conn, spec={"topic_filter": "t"},
                emit=lambda e: emitted.append(e) or EmitResult.ACCEPTED,
                ctx=MagicMock(),
            )
        )
        await _drive_until(task, lambda: bool(emitted))
    assert len(emitted) == 1  # binary surfaces with replacement chars, doesn't crash
    assert isinstance(emitted[0]["payload"], str)


@pytest.mark.asyncio
async def test_open_subscription_non_bytes_payload_stringified():
    conn = _conn({"host": "broker"})
    msgs = [_FakeMsg(topic="t", payload=42, qos=0)]
    client = _fake_client_with_messages(msgs)
    emitted = []
    plugin = MqttProtocolPlugin()
    with patch("aiomqtt.Client", return_value=client):
        task = asyncio.ensure_future(
            plugin.open_subscription(
                conn, spec={"topic_filter": "t"},
                emit=lambda e: emitted.append(e) or EmitResult.ACCEPTED,
                ctx=MagicMock(),
            )
        )
        await _drive_until(task, lambda: bool(emitted))
    assert emitted[0]["payload"] == "42"


@pytest.mark.asyncio
async def test_open_subscription_propagates_cancellation():
    conn = _conn({"host": "broker"})
    client = _fake_client_with_messages([])
    plugin = MqttProtocolPlugin()
    with patch("aiomqtt.Client", return_value=client):
        task = asyncio.ensure_future(
            plugin.open_subscription(
                conn, spec={"topic_filter": "t"},
                emit=lambda e: EmitResult.ACCEPTED, ctx=MagicMock(),
            )
        )
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_open_subscription_connect_failure_raises_typed():
    from sagewai.connections.protocols.mqtt import MqttConnectionError

    conn = _conn({"host": "unreachable"})
    client = MagicMock()
    client.__aenter__ = AsyncMock(side_effect=OSError("connection refused"))
    client.__aexit__ = AsyncMock(return_value=None)

    plugin = MqttProtocolPlugin()
    with patch("aiomqtt.Client", return_value=client):
        with pytest.raises(MqttConnectionError):
            await plugin.open_subscription(
                conn, spec={"topic_filter": "t"},
                emit=lambda e: EmitResult.ACCEPTED, ctx=MagicMock(),
            )


@pytest.mark.asyncio
async def test_close_subscription_is_noop():
    conn = _conn({"host": "broker"})
    plugin = MqttProtocolPlugin()
    # Must not raise even with no live connection.
    assert await plugin.close_subscription(conn, spec={"topic_filter": "t"}) is None
