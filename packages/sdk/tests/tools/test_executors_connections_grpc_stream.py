# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tool-catalog executor — gRPC server-streaming dispatch.

gRPC is dual-mode in the executor: the unary ``call`` op stays on the
stateless ``_grpc_run_op`` decrypt-then-call path (#376, UNCHANGED), while
the three streaming ops (``subscribe`` / ``drain`` / ``unsubscribe``) route
to the process-wide ``SubscriptionManager`` via a new ``_grpc_stream_dispatch``
(mirrors ``_mqtt_dispatch``).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from sagewai.connections.credentials import CredentialsBackendRouter
from sagewai.connections.store import ConnectionStore
from sagewai.connections.subscriptions.base import DrainResult
from sagewai.tools.executors.connections import run as connections_run


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(
        store_path=str(tmp_path / "c.json"), allowed_protocols=("grpc",)
    )


@pytest.fixture
def router(monkeypatch):
    """Router with a per-test Fernet key — used by the `call` fall-through
    test, which routes through the standard decrypt path."""
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    return CredentialsBackendRouter(default_backend="local")


def _seed(store):
    return store.create(
        protocol="grpc", project_id="proj", display_name="svc", tags=[],
        credentials_backend={"kind": "local"},
        protocol_data={"target": "api:443", "tls": "insecure"},
    )


@pytest.mark.asyncio
async def test_grpc_subscribe_dispatches_to_manager(store):
    _seed(store)
    mgr = MagicMock()
    mgr.subscribe = AsyncMock(return_value="sub-g1")
    payload = {
        "_kind": "grpc", "project_id": "proj",
        "exec": {"grpc": {"connection_ref": "svc", "operation": "subscribe",
                          "args": {"method": "echo.EchoService/EchoStream",
                                   "request": {}}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager",
        return_value=mgr,
    ):
        result = await connections_run(payload, store=store)
    assert result == {"subscription_id": "sub-g1"}
    kwargs = mgr.subscribe.await_args.kwargs
    assert kwargs["spec"]["method"] == "echo.EchoService/EchoStream"
    assert kwargs["connection"].display_name == "svc"
    from sagewai.connections.protocols.grpc import GrpcProtocolPlugin

    assert isinstance(kwargs["plugin"], GrpcProtocolPlugin)


@pytest.mark.asyncio
async def test_grpc_drain(store):
    _seed(store)
    dr = DrainResult(events=[{"message": "x"}], returned=1, remaining=0,
                     overflow_dropped=0, oversized_dropped=0,
                     global_pressure_dropped=0)
    mgr = MagicMock()
    mgr.drain = AsyncMock(return_value=dr)
    payload = {
        "_kind": "grpc", "project_id": "proj",
        "exec": {"grpc": {"connection_ref": "svc", "operation": "drain",
                          "args": {"subscription_id": "sub-g1",
                                   "max_events": 50}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager",
        return_value=mgr,
    ):
        result = await connections_run(payload, store=store)
    assert result["returned"] == 1
    assert result["events"] == [{"message": "x"}]
    mgr.drain.assert_awaited_with("sub-g1", 50)


@pytest.mark.asyncio
async def test_grpc_drain_default_max_events(store):
    _seed(store)
    dr = DrainResult(events=[], returned=0, remaining=0,
                     overflow_dropped=0, oversized_dropped=0,
                     global_pressure_dropped=0)
    mgr = MagicMock()
    mgr.drain = AsyncMock(return_value=dr)
    payload = {
        "_kind": "grpc", "project_id": "proj",
        "exec": {"grpc": {"connection_ref": "svc", "operation": "drain",
                          "args": {"subscription_id": "sub-g1"}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager",
        return_value=mgr,
    ):
        await connections_run(payload, store=store)
    mgr.drain.assert_awaited_with("sub-g1", 100)


@pytest.mark.asyncio
async def test_grpc_unsubscribe(store):
    _seed(store)
    mgr = MagicMock()
    mgr.unsubscribe = AsyncMock(return_value=None)
    payload = {
        "_kind": "grpc", "project_id": "proj",
        "exec": {"grpc": {"connection_ref": "svc", "operation": "unsubscribe",
                          "args": {"subscription_id": "sub-g1"}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager",
        return_value=mgr,
    ):
        result = await connections_run(payload, store=store)
    assert result == {"ok": True}
    mgr.unsubscribe.assert_awaited_with("sub-g1")


@pytest.mark.asyncio
async def test_grpc_unknown_streaming_op_raises(store, router):
    _seed(store)
    mgr = MagicMock()
    payload = {
        "_kind": "grpc", "project_id": "proj",
        "exec": {"grpc": {"connection_ref": "svc", "operation": "publish",
                          "args": {}}},
    }
    # 'publish' is neither a streaming op nor the unary 'call' — the
    # streaming branch only diverts subscribe/drain/unsubscribe, so this
    # falls through to _grpc_run_op which raises on the unknown op.
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager",
        return_value=mgr,
    ):
        with pytest.raises(ValueError, match="unknown grpc operation"):
            await connections_run(payload, store=store, router=router)


@pytest.mark.asyncio
async def test_grpc_call_op_still_uses_run_op(store, router):
    """The unary ``call`` op must STILL route through the standard _run_op
    path, untouched. The streaming branch only diverts the 3 streaming ops."""
    _seed(store)
    with patch(
        "sagewai.tools.executors.connections._grpc_run_op",
        new=AsyncMock(return_value={"reply": "ok"}),
    ) as m:
        payload = {
            "_kind": "grpc", "project_id": "proj",
            "exec": {"grpc": {"connection_ref": "svc", "operation": "call",
                              "args": {"method": "echo.EchoService/Echo",
                                       "request": {}}}},
        }
        result = await connections_run(payload, store=store, router=router)
    assert result == {"reply": "ok"}
    m.assert_awaited()


@pytest.mark.asyncio
async def test_grpc_subscribe_unknown_connection_raises(store):
    # No seed → connection_ref doesn't resolve.
    mgr = MagicMock()
    mgr.subscribe = AsyncMock(return_value="sub")
    payload = {
        "_kind": "grpc", "project_id": "proj",
        "exec": {"grpc": {"connection_ref": "nope", "operation": "subscribe",
                          "args": {"method": "x/y"}}},
    }
    with patch(
        "sagewai.tools.executors.connections.get_subscription_manager",
        return_value=mgr,
    ):
        with pytest.raises(ValueError, match="not found"):
            await connections_run(payload, store=store)
