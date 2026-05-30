# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""gRPC reflection + descriptor-pool cache + marshalling + call flow (mocked).

The reflection + marshalling tests build a REAL ``FileDescriptorProto`` for a
tiny ``echo.EchoService/Echo`` inline (``_echo_file_descriptor_proto_bytes()``,
defined HERE — NOT imported from grpc.py), mock the reflection fetch + the
unary call to isolate the live gRPC surface, and drive the genuine
descriptor-pool build + JSON↔protobuf marshalling against real protobuf
machinery without a live server.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from sagewai.connections.models import Connection


def _conn(pd: dict) -> Connection:
    now = datetime.now(timezone.utc).isoformat()
    return Connection(
        id="c",
        protocol="grpc",
        project_id="p",
        display_name="svc",
        tags=(),
        credentials_backend={"kind": "local"},
        status="ready",
        last_tested_at=None,
        last_test_ok=None,
        is_default=False,
        created_at=now,
        updated_at=now,
        last_error=None,
        protocol_data=pd,
    )


def _echo_file_descriptor_proto_bytes() -> bytes:
    """Hand-build a FileDescriptorProto for echo.EchoService/Echo and return
    its serialized bytes (what reflection would otherwise return)."""
    from google.protobuf import descriptor_pb2

    fdp = descriptor_pb2.FileDescriptorProto()
    fdp.name = "echo.proto"
    fdp.package = "echo"
    fdp.syntax = "proto3"
    # EchoRequest { string message = 1; }
    req = fdp.message_type.add()
    req.name = "EchoRequest"
    f = req.field.add()
    f.name = "message"
    f.number = 1
    f.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    f.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    # EchoReply { string message = 1; }
    rep = fdp.message_type.add()
    rep.name = "EchoReply"
    f2 = rep.field.add()
    f2.name = "message"
    f2.number = 1
    f2.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    f2.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    # service EchoService { rpc Echo(EchoRequest) returns (EchoReply); }
    svc = fdp.service.add()
    svc.name = "EchoService"
    m = svc.method.add()
    m.name = "Echo"
    m.input_type = ".echo.EchoRequest"
    m.output_type = ".echo.EchoReply"
    return fdp.SerializeToString()


# ── descriptor-pool cache ─────────────────────────────────────────────


def test_pool_cache_builds_and_resolves_method():
    from sagewai.connections.protocols.grpc import _DescriptorPoolCache

    cache = _DescriptorPoolCache(max_targets=8)
    pool = cache.build_pool_from_fdps("api:443", [_echo_file_descriptor_proto_bytes()])
    svc = pool.FindServiceByName("echo.EchoService")
    method = svc.FindMethodByName("Echo")
    assert method.input_type.full_name == "echo.EchoRequest"
    assert method.output_type.full_name == "echo.EchoReply"


def test_pool_cache_hit_reuses():
    from sagewai.connections.protocols.grpc import _DescriptorPoolCache

    cache = _DescriptorPoolCache(max_targets=8)
    cache.build_pool_from_fdps("api:443", [_echo_file_descriptor_proto_bytes()])
    assert cache.get("api:443") is not None
    assert cache.get("other:443") is None


def test_pool_cache_lru_evicts():
    from sagewai.connections.protocols.grpc import _DescriptorPoolCache

    cache = _DescriptorPoolCache(max_targets=2)
    cache.build_pool_from_fdps("a:1", [_echo_file_descriptor_proto_bytes()])
    cache.build_pool_from_fdps("b:1", [_echo_file_descriptor_proto_bytes()])
    cache.get("a:1")  # touch a → b is now LRU
    cache.build_pool_from_fdps("c:1", [_echo_file_descriptor_proto_bytes()])
    assert cache.get("b:1") is None  # evicted
    assert cache.get("a:1") is not None
    assert cache.get("c:1") is not None


# ── marshalling round-trip ────────────────────────────────────────────


def test_marshal_request_and_response_roundtrip():
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    from sagewai.connections.protocols.grpc import _marshal_request, _unmarshal_response

    pool = descriptor_pool.DescriptorPool()
    pool.Add(
        descriptor_pb2.FileDescriptorProto.FromString(_echo_file_descriptor_proto_bytes())
    )
    req_desc = pool.FindMessageTypeByName("echo.EchoRequest")
    rep_desc = pool.FindMessageTypeByName("echo.EchoReply")
    ReqCls = message_factory.GetMessageClass(req_desc)
    RepCls = message_factory.GetMessageClass(rep_desc)

    req_msg = _marshal_request({"message": "hi"}, ReqCls)
    assert req_msg.message == "hi"

    rep = RepCls(message="echoed: hi")
    out = _unmarshal_response(rep)
    assert out == {"message": "echoed: hi"}


def test_marshal_request_unknown_field_raises():
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    from sagewai.connections.protocols.grpc import GrpcMarshalError, _marshal_request

    pool = descriptor_pool.DescriptorPool()
    pool.Add(
        descriptor_pb2.FileDescriptorProto.FromString(_echo_file_descriptor_proto_bytes())
    )
    ReqCls = message_factory.GetMessageClass(pool.FindMessageTypeByName("echo.EchoRequest"))
    with pytest.raises(GrpcMarshalError):
        _marshal_request({"nonexistent_field": "x"}, ReqCls)


# ── reflection fetch + call runner + test() ───────────────────────────


@pytest.mark.asyncio
async def test_call_unary_roundtrip():
    """call(method, request) → reflection → marshal → unary_unary → unmarshal."""
    from sagewai.connections.protocols.grpc import _POOL_CACHE, _run_op

    _POOL_CACHE._pools.clear()
    conn = _conn({"target": "api:443", "tls": "insecure"})

    async def fake_fetch(channel, symbol):
        return [_echo_file_descriptor_proto_bytes()]

    async def fake_unary(
        channel, method_path, request_msg, request_cls, response_cls, metadata, timeout
    ):
        return response_cls(message="echoed: " + request_msg.message)

    with patch(
        "sagewai.connections.protocols.grpc._fetch_descriptors_for_symbol",
        side_effect=fake_fetch,
    ), patch(
        "sagewai.connections.protocols.grpc._do_unary_call", side_effect=fake_unary
    ), patch(
        "sagewai.connections.protocols.grpc._open_channel", return_value=MagicMock()
    ):
        result = await _run_op(
            conn,
            op="call",
            args={"method": "echo.EchoService/Echo", "request": {"message": "hi"}},
        )

    assert result == {"message": "echoed: hi"}


@pytest.mark.asyncio
async def test_call_cache_hit_skips_reflection():
    """A second call to the same target reuses the cached pool — no second
    reflection round-trip."""
    from sagewai.connections.protocols.grpc import _POOL_CACHE, _run_op

    _POOL_CACHE._pools.clear()
    conn = _conn({"target": "api:443", "tls": "insecure"})

    fetch_calls = {"n": 0}

    async def fake_fetch(channel, symbol):
        fetch_calls["n"] += 1
        return [_echo_file_descriptor_proto_bytes()]

    async def fake_unary(
        channel, method_path, request_msg, request_cls, response_cls, metadata, timeout
    ):
        return response_cls(message="echoed: " + request_msg.message)

    with patch(
        "sagewai.connections.protocols.grpc._fetch_descriptors_for_symbol",
        side_effect=fake_fetch,
    ), patch(
        "sagewai.connections.protocols.grpc._do_unary_call", side_effect=fake_unary
    ), patch(
        "sagewai.connections.protocols.grpc._open_channel", return_value=MagicMock()
    ):
        await _run_op(
            conn,
            op="call",
            args={"method": "echo.EchoService/Echo", "request": {"message": "a"}},
        )
        await _run_op(
            conn,
            op="call",
            args={"method": "echo.EchoService/Echo", "request": {"message": "b"}},
        )

    assert fetch_calls["n"] == 1  # reflection fetched once, cached for the rest


@pytest.mark.asyncio
async def test_call_unknown_op_raises():
    from sagewai.connections.protocols.grpc import _run_op

    conn = _conn({"target": "api:443"})
    with pytest.raises(ValueError, match="unknown grpc operation"):
        await _run_op(conn, op="stream", args={})


@pytest.mark.asyncio
async def test_call_method_not_in_reflection_raises_method_error():
    from sagewai.connections.protocols.grpc import (
        GrpcMethodError,
        _POOL_CACHE,
        _run_op,
    )

    _POOL_CACHE._pools.clear()
    conn = _conn({"target": "api:443", "tls": "insecure"})

    async def fake_fetch(channel, symbol):
        return [_echo_file_descriptor_proto_bytes()]  # only echo.EchoService

    with patch(
        "sagewai.connections.protocols.grpc._fetch_descriptors_for_symbol",
        side_effect=fake_fetch,
    ), patch(
        "sagewai.connections.protocols.grpc._open_channel", return_value=MagicMock()
    ):
        with pytest.raises(GrpcMethodError):
            await _run_op(
                conn, op="call", args={"method": "other.Svc/Missing", "request": {}}
            )


def test_method_path_normalization():
    from sagewai.connections.protocols.grpc import _normalize_method_path, _split_method

    assert _normalize_method_path("pkg.Svc/M") == "/pkg.Svc/M"
    assert _normalize_method_path("/pkg.Svc/M") == "/pkg.Svc/M"
    svc, meth = _split_method("pkg.Svc/M")
    assert svc == "pkg.Svc" and meth == "M"


def test_split_method_rejects_missing_slash():
    from sagewai.connections.protocols.grpc import GrpcMethodError, _split_method

    with pytest.raises(GrpcMethodError):
        _split_method("no_slash_here")


def test_call_metadata_injects_auth_token():
    from sagewai.connections.protocols.grpc import _call_metadata

    md = _call_metadata(
        {
            "auth_mode": "metadata_token",
            "auth_metadata_key": "Authorization",
            "auth_token": "secret",
            "auth_token_prefix": "Bearer ",
        },
        {"x-request-id": "abc"},
    )
    assert ("authorization", "Bearer secret") in md
    assert ("x-request-id", "abc") in md


def test_call_metadata_none_mode_skips_auth():
    from sagewai.connections.protocols.grpc import _call_metadata

    md = _call_metadata({"auth_mode": "none", "auth_token": "secret"}, None)
    assert md == []


@pytest.mark.asyncio
async def test_test_lists_services():
    from sagewai.connections.protocols.grpc import GrpcProtocolPlugin

    conn = _conn({"target": "api:443", "tls": "insecure"})

    async def fake_list(channel):
        return ["echo.EchoService", "grpc.health.v1.Health"]

    with patch(
        "sagewai.connections.protocols.grpc._list_services", side_effect=fake_list
    ), patch(
        "sagewai.connections.protocols.grpc._open_channel", return_value=MagicMock()
    ):
        result = await GrpcProtocolPlugin().test(conn, ctx=MagicMock())
    assert result.ok is True
    assert "2" in (result.message or "")  # service count


@pytest.mark.asyncio
async def test_test_reflection_disabled_returns_typed_error():
    from sagewai.connections.protocols.grpc import GrpcMethodError, GrpcProtocolPlugin

    conn = _conn({"target": "api:443", "tls": "insecure"})

    async def fake_list(channel):
        raise GrpcMethodError("server reflection is not enabled")

    with patch(
        "sagewai.connections.protocols.grpc._list_services", side_effect=fake_list
    ), patch(
        "sagewai.connections.protocols.grpc._open_channel", return_value=MagicMock()
    ):
        result = await GrpcProtocolPlugin().test(conn, ctx=MagicMock())
    assert result.ok is False
    assert "reflection" in (result.message or "").lower()


# ── status-code normalization (exhaustive mapping table) ──────────────


def _aio_rpc_error(status_code, details="boom"):
    """Build a fake grpc.aio.AioRpcError-shaped object for _raise_normalized."""
    import grpc

    class _FakeAioRpcError(grpc.aio.AioRpcError):
        def __init__(self, code, det):
            self._code = code
            self._det = det

        def code(self):
            return self._code

        def details(self):
            return self._det

    return _FakeAioRpcError(status_code, details)


def test_raise_normalized_maps_each_status_code():
    import grpc
    from sagewai.connections.protocols.grpc import (
        _raise_normalized,
        GrpcAuthError,
        GrpcConnectionError,
        GrpcDeadlineError,
        GrpcMethodError,
        GrpcCallError,
    )

    SC = grpc.StatusCode
    cases = [
        (SC.UNAVAILABLE, GrpcConnectionError),
        (SC.UNAUTHENTICATED, GrpcAuthError),
        (SC.PERMISSION_DENIED, GrpcAuthError),
        (SC.UNIMPLEMENTED, GrpcMethodError),
        (SC.NOT_FOUND, GrpcMethodError),
        (SC.DEADLINE_EXCEEDED, GrpcDeadlineError),
        (SC.RESOURCE_EXHAUSTED, GrpcCallError),   # catch-all
        (SC.INTERNAL, GrpcCallError),             # catch-all
    ]
    for status_code, expected_cls in cases:
        with pytest.raises(expected_cls):
            _raise_normalized(_aio_rpc_error(status_code), context="t")


def test_raise_normalized_handles_none_code():
    from sagewai.connections.protocols.grpc import _raise_normalized, GrpcCallError

    err = _aio_rpc_error(None)
    with pytest.raises(GrpcCallError) as exc_info:
        _raise_normalized(err, context="t")
    assert exc_info.value.status_code == "UNKNOWN"
