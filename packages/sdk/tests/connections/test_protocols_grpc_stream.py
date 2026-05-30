# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""gRPC SubscriptionPlugin half — server-streaming open/close + emit (mocked).

Mirrors ``test_protocols_mqtt_subscription.py``: the ``open_subscription``
coroutine is the manager's background task, so the tests drive it directly
with a mocked ``unary_stream`` channel and assert the emitted event shape +
clean cancellation. Reflection + marshalling are #376's helpers, patched at
the seams (``_fetch_descriptors_for_symbol`` / ``_open_channel`` / ``_aclose``).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from sagewai.connections.models import Connection
from sagewai.connections.protocols.grpc import (
    GrpcProtocolPlugin,
    GrpcStreamSpec,
    _POOL_CACHE,
)
from sagewai.connections.subscriptions.base import EmitResult, SubscriptionPlugin


def _conn(pd: dict) -> Connection:
    now = datetime.now(timezone.utc).isoformat()
    return Connection(
        id="c", protocol="grpc", project_id="p", display_name="svc", tags=(),
        credentials_backend={"kind": "local"}, status="ready", last_tested_at=None,
        last_test_ok=None, is_default=False, created_at=now, updated_at=now,
        last_error=None, protocol_data=pd,
    )


def _streaming_echo_fdp_bytes() -> bytes:
    from google.protobuf import descriptor_pb2

    fdp = descriptor_pb2.FileDescriptorProto()
    fdp.name = "echo.proto"; fdp.package = "echo"; fdp.syntax = "proto3"
    for name in ("EchoRequest", "EchoReply"):
        msg = fdp.message_type.add(); msg.name = name
        f = msg.field.add(); f.name = "message"; f.number = 1
        f.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
        f.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    svc = fdp.service.add(); svc.name = "EchoService"
    m = svc.method.add(); m.name = "EchoStream"
    m.input_type = ".echo.EchoRequest"; m.output_type = ".echo.EchoReply"
    m.server_streaming = True
    return fdp.SerializeToString()


# ── dual-Protocol ─────────────────────────────────────────────────────


def test_plugin_now_implements_subscription_plugin():
    assert isinstance(GrpcProtocolPlugin(), SubscriptionPlugin)


def test_subscription_spec_schema():
    p = GrpcProtocolPlugin()
    assert p.subscription_spec_schema() is GrpcStreamSpec


def test_stream_spec_minimal():
    s = GrpcStreamSpec(method="echo.EchoService/EchoStream")
    assert s.method == "echo.EchoService/EchoStream"
    assert s.request == {}
    assert s.overflow_policy == "drop_oldest"


def test_stream_spec_rejects_pause():
    with pytest.raises(ValidationError):
        GrpcStreamSpec(method="x/y", overflow_policy="pause")


def test_stream_spec_rejects_empty_method():
    with pytest.raises(ValidationError):
        GrpcStreamSpec(method="")


def test_stream_spec_rejects_extra_fields():
    with pytest.raises(ValidationError):
        GrpcStreamSpec(method="x/y", bogus="nope")


# ── open_subscription emit shape ──────────────────────────────────────


def _fake_streaming_channel(responses):
    """A grpc.aio channel mock whose unary_stream callable yields ``responses``.

    Matches the verified grpcio 1.80.0 shape:
    ``channel.unary_stream(path, request_serializer=, response_deserializer=)``
    returns a callable; ``async for r in callable_(request_msg, metadata=...)``
    yields response messages. After the canned responses the call blocks on a
    never-set Event so the stream "stays open" until the task is cancelled.
    """
    channel = MagicMock()

    def _unary_stream(path, request_serializer=None, response_deserializer=None):
        async def _call(request_msg, metadata=None, timeout=None):
            for r in responses:
                yield r
            await asyncio.Event().wait()

        return _call

    channel.unary_stream = _unary_stream
    return channel


@pytest.mark.asyncio
async def test_open_subscription_emits_each_stream_item():
    _POOL_CACHE._pools.clear()
    conn = _conn({"target": "api:443", "tls": "insecure"})

    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    pool = descriptor_pool.DescriptorPool()
    pool.Add(descriptor_pb2.FileDescriptorProto.FromString(_streaming_echo_fdp_bytes()))
    ReplyCls = message_factory.GetMessageClass(pool.FindMessageTypeByName("echo.EchoReply"))
    responses = [ReplyCls(message="a"), ReplyCls(message="b")]

    channel = _fake_streaming_channel(responses)
    emitted = []

    async def fake_fetch(ch, symbol):
        return [_streaming_echo_fdp_bytes()]

    with patch(
        "sagewai.connections.protocols.grpc._fetch_descriptors_for_symbol",
        side_effect=fake_fetch,
    ), patch(
        "sagewai.connections.protocols.grpc._open_channel", return_value=channel,
    ), patch(
        "sagewai.connections.protocols.grpc._aclose", new=AsyncMock(),
    ):
        plugin = GrpcProtocolPlugin()
        task = asyncio.ensure_future(
            plugin.open_subscription(
                conn,
                spec={
                    "method": "echo.EchoService/EchoStream",
                    "request": {"message": "go"},
                },
                emit=lambda e: emitted.append(e) or EmitResult.ACCEPTED,
                ctx=None,
            )
        )
        for _ in range(50):
            await asyncio.sleep(0)
            if len(emitted) >= 2:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert [e["message"] for e in emitted] == ["a", "b"]


@pytest.mark.asyncio
async def test_open_subscription_closes_channel_on_teardown():
    _POOL_CACHE._pools.clear()
    conn = _conn({"target": "api:443", "tls": "insecure"})
    channel = _fake_streaming_channel([])
    aclose_mock = AsyncMock()

    async def fake_fetch(ch, symbol):
        return [_streaming_echo_fdp_bytes()]

    with patch(
        "sagewai.connections.protocols.grpc._fetch_descriptors_for_symbol",
        side_effect=fake_fetch,
    ), patch(
        "sagewai.connections.protocols.grpc._open_channel", return_value=channel,
    ), patch(
        "sagewai.connections.protocols.grpc._aclose", new=aclose_mock,
    ):
        plugin = GrpcProtocolPlugin()
        task = asyncio.ensure_future(
            plugin.open_subscription(
                conn,
                spec={"method": "echo.EchoService/EchoStream"},
                emit=lambda e: EmitResult.ACCEPTED,
                ctx=None,
            )
        )
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    aclose_mock.assert_awaited()


@pytest.mark.asyncio
async def test_open_subscription_reraises_cancellation():
    _POOL_CACHE._pools.clear()
    conn = _conn({"target": "api:443", "tls": "insecure"})
    channel = _fake_streaming_channel([])

    async def fake_fetch(ch, symbol):
        return [_streaming_echo_fdp_bytes()]

    with patch(
        "sagewai.connections.protocols.grpc._fetch_descriptors_for_symbol",
        side_effect=fake_fetch,
    ), patch(
        "sagewai.connections.protocols.grpc._open_channel", return_value=channel,
    ), patch(
        "sagewai.connections.protocols.grpc._aclose", new=AsyncMock(),
    ):
        plugin = GrpcProtocolPlugin()
        task = asyncio.ensure_future(
            plugin.open_subscription(
                conn,
                spec={"method": "echo.EchoService/EchoStream"},
                emit=lambda e: EmitResult.ACCEPTED,
                ctx=None,
            )
        )
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_close_subscription_is_noop():
    conn = _conn({"target": "api:443", "tls": "insecure"})
    plugin = GrpcProtocolPlugin()
    assert await plugin.close_subscription(
        conn, spec={"method": "echo.EchoService/EchoStream"}
    ) is None
