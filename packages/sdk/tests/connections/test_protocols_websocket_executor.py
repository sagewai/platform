# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""WebSocket executor + op dispatch + JSONPath tests."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.connections.models import Connection
from sagewai.connections.protocols.websocket import (
    WebsocketAuthError,
    WebsocketConnectionError,
    WebsocketHandshakeError,
    WebsocketNotInstalledError,
    WebsocketProtocolPlugin,
    WebsocketResponseError,
    WebsocketTemplateError,
    WebsocketTimeoutError,
    WebsocketUnknownOperationError,
    _run_op,
)


def _conn(protocol_data: dict) -> Connection:
    now = datetime.now(timezone.utc).isoformat()
    return Connection(
        id="conn-websocket-test",
        protocol="websocket",
        project_id="proj-test",
        display_name="test-ws",
        tags=[],
        credentials_backend={"kind": "local"},
        status="ready",
        last_tested_at=None,
        last_test_ok=None,
        is_default=False,
        created_at=now,
        updated_at=now,
        last_error=None,
        protocol_data=protocol_data,
    )


def _make_fake_ws_context(*, response=None, recv_raises=None):
    """Build an async-context-manager mock for the websockets.connect() call.

    Returns (context_manager, ws_object) so tests can inspect what was
    sent / received and which kwargs the handshake was invoked with.
    """
    fake_ws = MagicMock()
    fake_ws.send = AsyncMock(return_value=None)
    fake_ws.close = AsyncMock(return_value=None)
    if recv_raises is not None:
        fake_ws.recv = AsyncMock(side_effect=recv_raises)
    else:
        fake_ws.recv = AsyncMock(return_value=response)

    captured: dict = {"last_url": None, "last_kwargs": None}

    @asynccontextmanager
    async def _aenter(url, **kwargs):
        captured["last_url"] = url
        captured["last_kwargs"] = kwargs
        yield fake_ws

    # The plugin calls `connect(url, additional_headers=...)` and uses the
    # return value as an async context manager via `async with`. So `connect`
    # here is a callable that returns an async context manager.
    def cm_factory(url, **kwargs):
        return _aenter(url, **kwargs)

    cm_factory.captured = captured  # type: ignore[attr-defined]
    return cm_factory, fake_ws


def _last_kwargs(cm) -> dict:
    return cm.captured["last_kwargs"]


# ── happy path ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_and_receive_returns_raw_frame_when_no_response_match():
    conn = _conn({
        "url": "wss://x.com/ws",
        "headers": {},
        "auth_header_name": "Authorization",
        "auth_header_value": "",
        "default_timeout_seconds": 5.0,
        "operations": [
            {"name": "ping", "message_template": '{"type": "ping"}'},
        ],
    })

    cm, ws = _make_fake_ws_context(response='{"type":"pong","ts":12345}')
    with patch("websockets.asyncio.client.connect", cm):
        result = await _run_op(conn, op="ping", args={})

    assert result == {"frame": '{"type":"pong","ts":12345}'}
    ws.send.assert_awaited_once_with('{"type": "ping"}')


@pytest.mark.asyncio
async def test_send_and_receive_with_response_match_extracts_jsonpath():
    conn = _conn({
        "url": "wss://x.com/ws",
        "headers": {},
        "auth_header_value": "",
        "default_timeout_seconds": 5.0,
        "operations": [
            {
                "name": "quote",
                "message_template": '{"symbol": "{symbol}"}',
                "response_match": "$.price",
            },
        ],
    })

    cm, ws = _make_fake_ws_context(response='{"price": 42.5, "symbol": "AAPL"}')
    with patch("websockets.asyncio.client.connect", cm):
        result = await _run_op(conn, op="quote", args={"symbol": "AAPL"})

    # JSONPath $.price returns the matched value (single hit unwrapped).
    assert result == 42.5


# ── template rendering ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_message_template_renders_kwargs():
    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "operations": [
            {"name": "echo", "message_template": '{"msg": "{text}"}'},
        ],
    })

    cm, ws = _make_fake_ws_context(response='{}')
    with patch("websockets.asyncio.client.connect", cm):
        await _run_op(conn, op="echo", args={"text": "hello world"})

    ws.send.assert_awaited_once_with('{"msg": "hello world"}')


@pytest.mark.asyncio
async def test_message_template_missing_keys_raises():
    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "operations": [
            {"name": "needs_keys", "message_template": '{"a": "{a}", "b": "{b}"}'},
        ],
    })

    cm, _ = _make_fake_ws_context(response='{}')
    with patch("websockets.asyncio.client.connect", cm):
        with pytest.raises(WebsocketTemplateError) as exc_info:
            await _run_op(conn, op="needs_keys", args={"a": "x"})

    assert "b" in exc_info.value.missing_keys


# ── auth header ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_header_added_to_handshake_when_set():
    conn = _conn({
        "url": "wss://x.com/ws",
        "headers": {"User-Agent": "sagewai/test"},
        "auth_header_name": "Authorization",
        "auth_header_value": "Bearer abc123",
        "operations": [{"name": "ping", "message_template": '{}'}],
    })

    cm, _ = _make_fake_ws_context(response='{}')
    with patch("websockets.asyncio.client.connect", cm):
        await _run_op(conn, op="ping", args={})

    kwargs = _last_kwargs(cm)
    headers = kwargs.get("additional_headers") or kwargs.get("extra_headers")
    assert headers is not None, f"expected additional_headers in {kwargs!r}"
    headers_dict = dict(headers) if not isinstance(headers, dict) else headers
    assert headers_dict.get("Authorization") == "Bearer abc123"
    assert headers_dict.get("User-Agent") == "sagewai/test"


@pytest.mark.asyncio
async def test_auth_header_omitted_when_value_empty():
    conn = _conn({
        "url": "wss://x.com/ws",
        "headers": {"User-Agent": "sagewai/test"},
        "auth_header_name": "Authorization",
        "auth_header_value": "",
        "operations": [{"name": "ping", "message_template": '{}'}],
    })

    cm, _ = _make_fake_ws_context(response='{}')
    with patch("websockets.asyncio.client.connect", cm):
        await _run_op(conn, op="ping", args={})

    kwargs = _last_kwargs(cm)
    headers = kwargs.get("additional_headers") or kwargs.get("extra_headers") or {}
    headers_dict = dict(headers) if not isinstance(headers, dict) else headers
    assert "Authorization" not in headers_dict


@pytest.mark.asyncio
async def test_custom_auth_header_name():
    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_name": "X-API-Key",
        "auth_header_value": "secret123",
        "operations": [{"name": "ping", "message_template": '{}'}],
    })

    cm, _ = _make_fake_ws_context(response='{}')
    with patch("websockets.asyncio.client.connect", cm):
        await _run_op(conn, op="ping", args={})

    kwargs = _last_kwargs(cm)
    headers = kwargs.get("additional_headers") or kwargs.get("extra_headers")
    headers_dict = dict(headers) if not isinstance(headers, dict) else headers
    assert headers_dict.get("X-API-Key") == "secret123"


# ── timeout ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_per_op_timeout_overrides_default():
    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "default_timeout_seconds": 30.0,
        "operations": [
            {"name": "fast", "message_template": '{}', "timeout_seconds": 0.5},
        ],
    })

    cm, _ = _make_fake_ws_context(recv_raises=asyncio.TimeoutError("expired"))
    with patch("websockets.asyncio.client.connect", cm):
        with pytest.raises(WebsocketTimeoutError):
            await _run_op(conn, op="fast", args={})


@pytest.mark.asyncio
async def test_default_timeout_used_when_no_per_op():
    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "default_timeout_seconds": 0.5,
        "operations": [
            {"name": "no_per_op_timeout", "message_template": '{}'},
        ],
    })

    cm, _ = _make_fake_ws_context(recv_raises=asyncio.TimeoutError("expired"))
    with patch("websockets.asyncio.client.connect", cm):
        with pytest.raises(WebsocketTimeoutError):
            await _run_op(conn, op="no_per_op_timeout", args={})


# ── response_match error paths ────────────────────────────────────────


@pytest.mark.asyncio
async def test_response_match_non_json_frame_raises():
    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "operations": [
            {"name": "expects_json", "message_template": '{}', "response_match": "$.x"},
        ],
    })

    cm, _ = _make_fake_ws_context(response="not json at all")
    with patch("websockets.asyncio.client.connect", cm):
        with pytest.raises(WebsocketResponseError, match="non-JSON"):
            await _run_op(conn, op="expects_json", args={})


@pytest.mark.asyncio
async def test_response_match_missing_path_raises():
    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "operations": [
            {"name": "expects_path", "message_template": '{}', "response_match": "$.nonexistent"},
        ],
    })

    cm, _ = _make_fake_ws_context(response='{"actual": "value"}')
    with patch("websockets.asyncio.client.connect", cm):
        with pytest.raises(WebsocketResponseError, match="did not match"):
            await _run_op(conn, op="expects_path", args={})


# ── unknown op ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_op_raises():
    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "operations": [{"name": "real_op", "message_template": '{}'}],
    })

    with pytest.raises(WebsocketUnknownOperationError, match="ghost_op"):
        await _run_op(conn, op="ghost_op", args={})


# ── missing library ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_websockets_raises_not_installed():
    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "operations": [{"name": "ping", "message_template": '{}'}],
    })

    with patch(
        "sagewai.connections.protocols.websocket._import_websockets",
        side_effect=WebsocketNotInstalledError(),
    ):
        with pytest.raises(WebsocketNotInstalledError):
            await _run_op(conn, op="ping", args={})


# ── websockets exception normalization ────────────────────────────────


@pytest.mark.asyncio
async def test_websockets_invalid_status_normalized_to_auth_error():
    """401/403 from the upgrade handshake should normalize to WebsocketAuthError."""
    try:
        from websockets.exceptions import InvalidStatus  # type: ignore[import-not-found]
        modern = True
    except ImportError:
        try:
            from websockets.exceptions import InvalidStatusCode as InvalidStatus  # type: ignore[import-not-found]
            modern = False
        except ImportError:
            pytest.skip("websockets not installed")

    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "operations": [{"name": "ping", "message_template": '{}'}],
    })

    # Build an InvalidStatus-shaped exception with status=401 without
    # exercising the websockets constructor (which depends on internal
    # Response shape across versions).
    fake_exc = InvalidStatus.__new__(InvalidStatus)
    Exception.__init__(fake_exc, "401 Unauthorized")
    setattr(fake_exc, "status_code", 401)
    fake_resp = type("FakeResp", (), {"status_code": 401})()
    setattr(fake_exc, "response", fake_resp)

    @asynccontextmanager
    async def failing_cm(url, **kwargs):
        raise fake_exc
        yield  # unreachable

    def factory(url, **kwargs):
        return failing_cm(url, **kwargs)

    with patch("websockets.asyncio.client.connect", factory):
        with pytest.raises(WebsocketAuthError):
            await _run_op(conn, op="ping", args={})


@pytest.mark.asyncio
async def test_websockets_invalid_status_500_normalized_to_handshake_error():
    """Non-401/403 status codes route to WebsocketHandshakeError."""
    try:
        from websockets.exceptions import InvalidStatus  # type: ignore[import-not-found]
    except ImportError:
        try:
            from websockets.exceptions import InvalidStatusCode as InvalidStatus  # type: ignore[import-not-found]
        except ImportError:
            pytest.skip("websockets not installed")

    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "operations": [{"name": "ping", "message_template": '{}'}],
    })

    fake_exc = InvalidStatus.__new__(InvalidStatus)
    Exception.__init__(fake_exc, "500 Internal Server Error")
    setattr(fake_exc, "status_code", 500)
    setattr(fake_exc, "response", type("FakeResp", (), {"status_code": 500})())

    @asynccontextmanager
    async def failing_cm(url, **kwargs):
        raise fake_exc
        yield  # unreachable

    def factory(url, **kwargs):
        return failing_cm(url, **kwargs)

    with patch("websockets.asyncio.client.connect", factory):
        with pytest.raises(WebsocketHandshakeError):
            await _run_op(conn, op="ping", args={})


@pytest.mark.asyncio
async def test_websockets_connection_closed_normalized():
    """ConnectionClosed mid-operation should normalize to WebsocketConnectionError."""
    try:
        from websockets.exceptions import ConnectionClosed  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("websockets not installed")

    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "operations": [{"name": "ping", "message_template": '{}'}],
    })

    # Construct ConnectionClosed defensively — its constructor signature
    # has changed across websockets versions.
    cc_exc = ConnectionClosed.__new__(ConnectionClosed)
    Exception.__init__(cc_exc, "connection closed")
    setattr(cc_exc, "rcvd", None)
    setattr(cc_exc, "sent", None)

    cm, ws = _make_fake_ws_context()
    ws.send = AsyncMock(side_effect=cc_exc)

    with patch("websockets.asyncio.client.connect", cm):
        with pytest.raises(WebsocketConnectionError):
            await _run_op(conn, op="ping", args={})


@pytest.mark.asyncio
async def test_os_error_normalized_to_connection_error():
    conn = _conn({
        "url": "wss://unreachable.example.com/ws",
        "auth_header_value": "",
        "operations": [{"name": "ping", "message_template": '{}'}],
    })

    @asynccontextmanager
    async def failing_cm(url, **kwargs):
        raise OSError("dns failed")
        yield  # unreachable

    def factory(url, **kwargs):
        return failing_cm(url, **kwargs)

    with patch("websockets.asyncio.client.connect", factory):
        with pytest.raises(WebsocketConnectionError, match="dns failed"):
            await _run_op(conn, op="ping", args={})


# ── close on success ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_called_on_success():
    """The async-with handles close automatically — verify the context
    manager exits cleanly on success path (no exception leaks)."""
    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "operations": [{"name": "ping", "message_template": '{}'}],
    })

    cm, ws = _make_fake_ws_context(response='{}')
    with patch("websockets.asyncio.client.connect", cm):
        result = await _run_op(conn, op="ping", args={})
    assert result == {"frame": "{}"}


# ── test() endpoint ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_endpoint_opens_handshake_and_closes():
    """plugin.test() does open-handshake + immediate close. No frames sent."""
    conn = _conn({
        "url": "wss://x.com/ws",
        "auth_header_value": "",
        "operations": [],
    })

    cm, ws = _make_fake_ws_context(response=None)
    plugin = WebsocketProtocolPlugin()
    ctx = MagicMock()
    with patch("websockets.asyncio.client.connect", cm):
        result = await plugin.test(conn, ctx=ctx)

    assert result.ok is True
    ws.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_test_endpoint_failure_returns_not_ok():
    conn = _conn({
        "url": "wss://unreachable.example.com/ws",
        "auth_header_value": "",
        "operations": [],
    })

    @asynccontextmanager
    async def failing_cm(url, **kwargs):
        raise OSError("connection refused")
        yield  # unreachable

    def factory(url, **kwargs):
        return failing_cm(url, **kwargs)

    plugin = WebsocketProtocolPlugin()
    ctx = MagicMock()
    with patch("websockets.asyncio.client.connect", factory):
        result = await plugin.test(conn, ctx=ctx)

    assert result.ok is False
    assert "connection refused" in (result.message or "").lower()


@pytest.mark.asyncio
async def test_test_method_decrypts_via_ctx_creds():
    """``plugin.test()`` must defensively decrypt encrypted ``auth_header_value``
    via ``ctx.creds``. The admin route pre-decrypts so this is a no-op in that
    path; the executor / autopilot health-check / future CLI rely on it.

    Mirrors the OPC UA test pattern (PR3 lesson). Without the defensive decrypt,
    the ciphertext would be sent as the literal handshake header value and the
    vendor would reject it with a misleading auth error.
    """
    import os
    from dataclasses import replace as dc_replace

    from cryptography.fernet import Fernet

    from sagewai.connections.credentials import CredentialsBackendRouter
    from sagewai.connections.protocols.base import PluginContext

    saved = os.environ.get("SAGEWAI_MASTER_KEY")
    os.environ["SAGEWAI_MASTER_KEY"] = Fernet.generate_key().decode()
    try:
        router = CredentialsBackendRouter(default_backend="local")
        encrypted_pd = router.encrypt(
            {
                "url": "wss://x.com/ws",
                "headers": {},
                "auth_header_name": "Authorization",
                "auth_header_value": "Bearer realtoken",
                "default_timeout_seconds": 5.0,
                "operations": [],
            },
            sensitive_field_paths=("auth_header_value",),
            connection_credentials_backend={"kind": "local"},
        )
        # Confirm setup: auth_header_value is ciphertext (fernet:-prefixed).
        assert encrypted_pd["auth_header_value"].startswith("fernet:")

        encrypted_conn = dc_replace(
            _conn({"url": "wss://x.com/ws", "auth_header_value": ""}),
            protocol_data=encrypted_pd,
            credentials_backend={"kind": "local"},
        )

        # Capture the headers that arrive at websockets.connect.
        captured_headers: list = []
        cm, _ = _make_fake_ws_context(response=None)

        def capturing_factory(url, **kwargs):
            captured_headers.append(
                kwargs.get("additional_headers") or kwargs.get("extra_headers")
            )
            return cm(url, **kwargs)

        plugin = WebsocketProtocolPlugin()
        ctx = PluginContext(
            store=MagicMock(),
            creds=router,
            project_id="proj-test",
            request=None,
        )

        with patch("websockets.asyncio.client.connect", capturing_factory):
            result = await plugin.test(encrypted_conn, ctx=ctx)

        assert result.ok is True, f"plugin.test() failed: {result.message}"
        assert captured_headers, "expected handshake headers to be captured"
        headers_dict = (
            dict(captured_headers[0])
            if not isinstance(captured_headers[0], dict)
            else captured_headers[0]
        )
        # The runner received the DECRYPTED plaintext — not the fernet ciphertext.
        assert headers_dict.get("Authorization") == "Bearer realtoken"
    finally:
        if saved is None:
            os.environ.pop("SAGEWAI_MASTER_KEY", None)
        else:
            os.environ["SAGEWAI_MASTER_KEY"] = saved
