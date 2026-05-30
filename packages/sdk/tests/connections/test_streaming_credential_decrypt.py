# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Issue #378 — streaming connections must send PLAINTEXT credentials.

Regression coverage for the cross-cutting bug where the streaming subscriber
tasks ran with ``ctx=None`` (so the plugins' defensive ``_maybe_decrypt`` was
inert) and MQTT's ``open_subscription`` never called ``_maybe_decrypt`` at
all. The encrypted credential (``fernet:…``) was injected onto the wire
verbatim instead of the plaintext token.

The two capturing-double tests are the proof: a credential encrypted at rest
must arrive as plaintext at the broker client (MQTT ``password``) / the gRPC
call metadata (``auth_token``). The dispatch tests prove the executor call
sites now thread a router-bearing ``PluginContext`` to the manager.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from sagewai.connections.credentials import CredentialsBackendRouter
from sagewai.connections.models import Connection
from sagewai.connections.protocols.grpc import GrpcProtocolPlugin, _POOL_CACHE
from sagewai.connections.protocols.mqtt import MqttProtocolPlugin
from sagewai.connections.store import ConnectionStore
from sagewai.connections.subscriptions.base import DrainResult, EmitResult
from sagewai.tools.executors.connections import run as connections_run

PLAINTEXT_MQTT_PASSWORD = "s3cr3t-mqtt-pw"
PLAINTEXT_GRPC_TOKEN = "real-bearer-secret"


@pytest.fixture
def router(monkeypatch) -> CredentialsBackendRouter:
    """A real local-backend router with a per-test Fernet master key."""
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    return CredentialsBackendRouter(default_backend="local")


def _conn(protocol: str, pd: dict) -> Connection:
    now = datetime.now(timezone.utc).isoformat()
    return Connection(
        id="c", protocol=protocol, project_id="p", display_name="d", tags=(),
        credentials_backend={"kind": "local"}, status="ready", last_tested_at=None,
        last_test_ok=None, is_default=False, created_at=now, updated_at=now,
        last_error=None, protocol_data=pd,
    )


async def _drive_until(task, predicate, ticks=50):
    for _ in range(ticks):
        await asyncio.sleep(0)
        if predicate():
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ── MQTT: encrypted password → plaintext at the aiomqtt client ─────────


@pytest.mark.asyncio
async def test_mqtt_open_subscription_sends_plaintext_password(router):
    """The MQTT subscriber must decrypt ``password`` via ``ctx.creds`` before
    connecting — the encrypted-at-rest ``fernet:…`` value must NOT reach the
    broker client."""
    encrypted_pd = router.encrypt(
        {"host": "broker.example", "port": 1883, "username": "u",
         "password": PLAINTEXT_MQTT_PASSWORD},
        sensitive_field_paths=("password",),
        connection_credentials_backend={"kind": "local"},
    )
    assert encrypted_pd["password"].startswith("fernet:"), "setup: must be encrypted"

    conn = _conn("mqtt", encrypted_pd)

    captured: dict = {}

    def _fake_client_factory(**kwargs):
        captured.update(kwargs)
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.subscribe = AsyncMock()

        async def _gen():
            await asyncio.Event().wait()  # block forever, like a live broker
            yield  # unreachable — marks this an async generator, not a coroutine

        client.messages = _gen()
        return client

    plugin = MqttProtocolPlugin()
    ctx = SimpleNamespace(creds=router)
    with patch("aiomqtt.Client", side_effect=_fake_client_factory):
        task = asyncio.ensure_future(
            plugin.open_subscription(
                conn, spec={"topic_filter": "fleet/#", "qos": 1},
                emit=lambda e: EmitResult.ACCEPTED, ctx=ctx,
            )
        )
        await _drive_until(task, lambda: bool(captured))

    assert captured.get("password") == PLAINTEXT_MQTT_PASSWORD
    assert not str(captured.get("password")).startswith("fernet:")


@pytest.mark.asyncio
async def test_mqtt_open_subscription_ctx_none_is_a_noop_safe(router):
    """With no router in the ctx the decrypt is a no-op: the value stays as
    stored (here: plaintext) and nothing crashes. Guards the dormant
    default-case path."""
    conn = _conn("mqtt", {"host": "b", "password": "plain"})
    captured: dict = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.subscribe = AsyncMock()

        async def _gen():
            await asyncio.Event().wait()
            yield  # unreachable — marks this an async generator, not a coroutine

        client.messages = _gen()
        return client

    plugin = MqttProtocolPlugin()
    with patch("aiomqtt.Client", side_effect=_factory):
        task = asyncio.ensure_future(
            plugin.open_subscription(
                conn, spec={"topic_filter": "t"},
                emit=lambda e: EmitResult.ACCEPTED, ctx=None,
            )
        )
        await _drive_until(task, lambda: bool(captured))
    assert captured.get("password") == "plain"


# ── gRPC: encrypted auth_token → plaintext in call metadata ────────────


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


def _metadata_capturing_channel(responses, captured_metadata: list):
    channel = MagicMock()

    def _unary_stream(path, request_serializer=None, response_deserializer=None):
        async def _call(request_msg, metadata=None, timeout=None):
            captured_metadata.append(metadata)
            for r in responses:
                yield r
            await asyncio.Event().wait()

        return _call

    channel.unary_stream = _unary_stream
    return channel


@pytest.mark.asyncio
async def test_grpc_open_subscription_sends_plaintext_auth_token(router):
    """The gRPC server-streaming subscriber must decrypt ``auth_token`` via
    ``ctx.creds`` — the call metadata must carry ``Bearer <plaintext>``, not
    the ``fernet:…`` ciphertext."""
    _POOL_CACHE._pools.clear()
    encrypted_pd = router.encrypt(
        {"target": "api:443", "tls": "insecure", "auth_mode": "metadata_token",
         "auth_metadata_key": "authorization", "auth_token": PLAINTEXT_GRPC_TOKEN,
         "auth_token_prefix": "Bearer "},
        sensitive_field_paths=("auth_token",),
        connection_credentials_backend={"kind": "local"},
    )
    assert encrypted_pd["auth_token"].startswith("fernet:"), "setup: must be encrypted"

    conn = _conn("grpc", encrypted_pd)

    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    pool = descriptor_pool.DescriptorPool()
    pool.Add(descriptor_pb2.FileDescriptorProto.FromString(_streaming_echo_fdp_bytes()))
    ReplyCls = message_factory.GetMessageClass(pool.FindMessageTypeByName("echo.EchoReply"))
    responses = [ReplyCls(message="a")]

    captured_metadata: list = []
    channel = _metadata_capturing_channel(responses, captured_metadata)
    emitted: list = []

    async def fake_fetch(ch, symbol):
        return [_streaming_echo_fdp_bytes()]

    ctx = SimpleNamespace(creds=router)
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
                spec={"method": "echo.EchoService/EchoStream", "request": {"message": "go"}},
                emit=lambda e: emitted.append(e) or EmitResult.ACCEPTED, ctx=ctx,
            )
        )
        await _drive_until(task, lambda: bool(captured_metadata) and bool(emitted))

    assert captured_metadata, "the stream call was never opened"
    md = dict(captured_metadata[0] or [])
    assert md.get("authorization") == f"Bearer {PLAINTEXT_GRPC_TOKEN}"
    assert "fernet:" not in str(captured_metadata[0])


# ── Executor dispatch threads a router-bearing ctx to the manager ──────


def _seed(store: ConnectionStore, protocol: str, pd: dict) -> None:
    store.create(
        protocol=protocol, project_id="proj", display_name="svc", tags=[],
        credentials_backend={"kind": "local"}, protocol_data=pd,
    )


@pytest.mark.asyncio
async def test_mqtt_dispatch_threads_router_bearing_ctx(tmp_path, router):
    store = ConnectionStore(
        store_path=str(tmp_path / "c.json"), allowed_protocols=("mqtt",)
    )
    _seed(store, "mqtt", {"host": "broker"})
    mgr = MagicMock()
    mgr.subscribe = AsyncMock(return_value="sub-m1")
    payload = {
        "_kind": "mqtt", "project_id": "proj",
        "exec": {"mqtt": {"connection_ref": "svc", "operation": "subscribe",
                          "args": {"topic_filter": "t"}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager", return_value=mgr,
    ):
        result = await connections_run(payload, store=store, router=router)
    assert result == {"subscription_id": "sub-m1"}
    ctx = mgr.subscribe.await_args.kwargs["ctx"]
    assert ctx is not None
    assert ctx.creds is router


@pytest.mark.asyncio
async def test_grpc_dispatch_threads_router_bearing_ctx(tmp_path, router):
    store = ConnectionStore(
        store_path=str(tmp_path / "c.json"), allowed_protocols=("grpc",)
    )
    _seed(store, "grpc", {"target": "api:443", "tls": "insecure"})
    mgr = MagicMock()
    mgr.subscribe = AsyncMock(return_value="sub-g1")
    payload = {
        "_kind": "grpc", "project_id": "proj",
        "exec": {"grpc": {"connection_ref": "svc", "operation": "subscribe",
                          "args": {"method": "echo.EchoService/EchoStream"}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager", return_value=mgr,
    ):
        result = await connections_run(payload, store=store, router=router)
    assert result == {"subscription_id": "sub-g1"}
    ctx = mgr.subscribe.await_args.kwargs["ctx"]
    assert ctx is not None
    assert ctx.creds is router


@pytest.mark.asyncio
async def test_drain_and_unsubscribe_never_build_the_router(tmp_path):
    """Issue #378 review fix: ``drain`` / ``unsubscribe`` operate on a
    subscription id alone and never decrypt — they must NOT build (or require)
    the credentials router. A backend misconfiguration must not break a
    teardown/read op. We patch ``_build_default_router`` to explode and assert
    both ops still succeed for both protocols."""
    store = ConnectionStore(
        store_path=str(tmp_path / "c.json"), allowed_protocols=("mqtt", "grpc")
    )
    mgr = MagicMock()
    mgr.drain = AsyncMock(return_value=DrainResult(
        events=[], returned=0, remaining=0, overflow_dropped=0,
        oversized_dropped=0, global_pressure_dropped=0,
    ))
    mgr.unsubscribe = AsyncMock(return_value=None)

    def _boom():
        raise RuntimeError("router must not be built on drain/unsubscribe")

    cases = [
        ("drain", {"subscription_id": "s", "max_events": 5}),
        ("unsubscribe", {"subscription_id": "s"}),
    ]
    for kind in ("mqtt", "grpc"):
        for op, args in cases:
            payload = {
                "_kind": kind, "project_id": "proj",
                "exec": {kind: {"connection_ref": "svc", "operation": op,
                                "args": args}},
            }
            with patch(
                "sagewai.tools.executors.connections.get_subscription_manager",
                return_value=mgr,
            ), patch(
                "sagewai.tools.executors.connections._build_default_router",
                side_effect=_boom,
            ):
                # Must not raise — no router build on these ops.
                await connections_run(payload, store=store, router=None)
