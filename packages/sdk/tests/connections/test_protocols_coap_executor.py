# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""CoAP executor + op dispatch tests."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.connections.models import Connection
from sagewai.connections.protocols.coap import (
    CoapNotInstalledError,
    CoapProtocolError,
    CoapProtocolPlugin,
    _run_op,
)


def _conn(protocol_data: dict) -> Connection:
    now = datetime.now(timezone.utc).isoformat()
    return Connection(
        id="conn-coap-test",
        protocol="coap",
        project_id="proj-test",
        display_name="test-device",
        tags=(),
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


def _make_fake_response(*, dotted_code: str, payload: bytes = b"",
                        content_format: int | None = None,
                        location_path: tuple[str, ...] | None = None) -> MagicMock:
    """Build a MagicMock that mimics an aiocoap.Message response."""
    fake = MagicMock()
    fake.code = MagicMock()
    fake.code.dotted = dotted_code
    fake.payload = payload
    fake.opt = MagicMock()
    fake.opt.content_format = content_format
    if location_path is not None:
        fake.opt.location_path = location_path
    return fake


def _make_fake_context(fake_response: MagicMock) -> MagicMock:
    """Build a fake aiocoap Context whose request().response is awaitable."""
    fake_context = MagicMock()
    # context.request(message) returns a MagicMock; .response is an awaitable
    # (a coroutine instance returning fake_response).
    request_obj = MagicMock()

    async def _await_response():
        return fake_response

    request_obj.response = _await_response()
    fake_context.request.return_value = request_obj
    fake_context.shutdown = AsyncMock()
    return fake_context


@pytest.mark.asyncio
async def test_get_returns_payload_and_code():
    conn = _conn({"base_uri": "coap://device.example.com:5683"})
    fake_response = _make_fake_response(
        dotted_code="2.05",
        payload=b'{"temp": 22.5}',
        content_format=50,
    )
    fake_context = _make_fake_context(fake_response)

    with patch("aiocoap.Context.create_client_context",
               AsyncMock(return_value=fake_context)):
        result = await _run_op(conn, op="get", args={"path": "/temperature"})

    assert result["code"] == "2.05"
    assert result["payload"] == b'{"temp": 22.5}'
    assert result["content_format"] == 50


@pytest.mark.asyncio
async def test_post_passes_payload_and_content_format():
    conn = _conn({"base_uri": "coap://device.example.com:5683"})
    fake_response = _make_fake_response(
        dotted_code="2.01",
        payload=b"",
        location_path=("things", "123"),
    )
    fake_context = _make_fake_context(fake_response)

    with patch("aiocoap.Context.create_client_context",
               AsyncMock(return_value=fake_context)):
        result = await _run_op(
            conn,
            op="post",
            args={
                "path": "/things",
                "payload": b'{"name":"x"}',
                "content_format": "application/json",
            },
        )

    assert result["code"] == "2.01"
    assert result["location"] == "/things/123"


@pytest.mark.asyncio
async def test_put_round_trip():
    conn = _conn({"base_uri": "coap://device.example.com:5683"})
    fake_response = _make_fake_response(dotted_code="2.04")
    fake_context = _make_fake_context(fake_response)

    with patch("aiocoap.Context.create_client_context",
               AsyncMock(return_value=fake_context)):
        result = await _run_op(
            conn,
            op="put",
            args={"path": "/config", "payload": b"42", "content_format": "text/plain"},
        )

    assert result["code"] == "2.04"


@pytest.mark.asyncio
async def test_delete_round_trip():
    conn = _conn({"base_uri": "coap://device.example.com:5683"})
    fake_response = _make_fake_response(dotted_code="2.02")
    fake_context = _make_fake_context(fake_response)

    with patch("aiocoap.Context.create_client_context",
               AsyncMock(return_value=fake_context)):
        result = await _run_op(conn, op="delete", args={"path": "/things/123"})

    assert result["code"] == "2.02"


@pytest.mark.asyncio
async def test_non_2xx_raises_protocol_error():
    conn = _conn({"base_uri": "coap://device.example.com:5683"})
    fake_response = _make_fake_response(dotted_code="4.04", payload=b"not found")
    fake_context = _make_fake_context(fake_response)

    with patch("aiocoap.Context.create_client_context",
               AsyncMock(return_value=fake_context)):
        with pytest.raises(CoapProtocolError) as exc_info:
            await _run_op(conn, op="get", args={"path": "/missing"})

    assert exc_info.value.coap_code == "4.04"
    assert exc_info.value.payload == b"not found"


@pytest.mark.asyncio
async def test_unknown_op_raises_value_error():
    conn = _conn({"base_uri": "coap://device.example.com:5683"})
    with pytest.raises(ValueError, match="unknown coap operation"):
        await _run_op(conn, op="patch", args={"path": "/x"})


@pytest.mark.asyncio
async def test_missing_aiocoap_raises_not_installed_error():
    conn = _conn({"base_uri": "coap://device.example.com:5683"})

    with patch(
        "sagewai.connections.protocols.coap._import_aiocoap",
        side_effect=CoapNotInstalledError(),
    ):
        with pytest.raises(CoapNotInstalledError):
            await _run_op(conn, op="get", args={"path": "/"})


@pytest.mark.asyncio
async def test_query_params_appended_to_uri():
    """GET op with query dict produces ?k=v on the request URI."""
    conn = _conn({"base_uri": "coap://device.example.com:5683"})

    captured_kwargs: list = []

    real_message_factory = MagicMock(side_effect=lambda **kw: (
        captured_kwargs.append(kw) or MagicMock()
    ))

    fake_response = _make_fake_response(dotted_code="2.05")
    fake_context = _make_fake_context(fake_response)

    with patch("aiocoap.Message", real_message_factory):
        with patch("aiocoap.Context.create_client_context",
                   AsyncMock(return_value=fake_context)):
            await _run_op(
                conn,
                op="get",
                args={"path": "/sensors", "query": {"limit": "10", "kind": "temp"}},
            )

    assert captured_kwargs, "aiocoap.Message was never called"
    captured_uri = captured_kwargs[0].get("uri", "")
    assert "?" in captured_uri
    assert "limit=10" in captured_uri
    assert "kind=temp" in captured_uri


@pytest.mark.asyncio
async def test_test_endpoint_hits_well_known_core():
    """plugin.test() performs GET on /.well-known/core per RFC 7252 §7.2."""
    conn = _conn({"base_uri": "coap://device.example.com:5683"})

    captured_kwargs: list = []
    msg_factory = MagicMock(side_effect=lambda **kw: (
        captured_kwargs.append(kw) or MagicMock()
    ))

    fake_response = _make_fake_response(
        dotted_code="2.05",
        payload=b'</temperature>;rt="temp"',
        content_format=40,
    )
    fake_context = _make_fake_context(fake_response)

    plugin = CoapProtocolPlugin()
    ctx = MagicMock()
    with patch("aiocoap.Message", msg_factory):
        with patch("aiocoap.Context.create_client_context",
                   AsyncMock(return_value=fake_context)):
            result = await plugin.test(conn, ctx=ctx)

    assert result.ok is True
    assert any("/.well-known/core" in kw.get("uri", "") for kw in captured_kwargs)


@pytest.mark.asyncio
async def test_test_endpoint_failure_returns_not_ok():
    conn = _conn({"base_uri": "coap://device.example.com:5683"})

    with patch(
        "aiocoap.Context.create_client_context",
        AsyncMock(side_effect=OSError("connection refused")),
    ):
        plugin = CoapProtocolPlugin()
        ctx = MagicMock()
        result = await plugin.test(conn, ctx=ctx)

    assert result.ok is False
    assert "connection refused" in (result.message or "").lower()


@pytest.mark.asyncio
async def test_context_shutdown_called_on_psk_setup_failure():
    """If PSK setup fails the context's shutdown() must still be awaited.

    Regression: if PSK setup runs OUTSIDE the try/finally that owns
    context.shutdown(), an exception there leaks the UDP socket + transport
    tasks. The fix is to scope PSK setup inside the try so the finally always
    fires.
    """
    from sagewai.connections.protocols.coap import CoapDtlsError

    conn = _conn({
        "base_uri": "coaps://device.example.com:5684",
        "psk_identity": "id",
        "psk_key": "key",
    })
    fake_context = _make_fake_context(_make_fake_response(dotted_code="2.05"))

    # Force PSK setup to blow up by patching CredentialsMap.load_from_dict.
    with patch(
        "aiocoap.Context.create_client_context",
        AsyncMock(return_value=fake_context),
    ), patch(
        "aiocoap.credentials.CredentialsMap.load_from_dict",
        side_effect=RuntimeError("psk parse boom"),
    ):
        with pytest.raises(CoapDtlsError):
            await _run_op(conn, op="get", args={"path": "/"})

    # The shutdown coroutine must have been awaited despite the failure.
    fake_context.shutdown.assert_awaited()


@pytest.mark.asyncio
async def test_test_method_decrypts_via_ctx_creds():
    """``plugin.test()`` must defensively decrypt via ``ctx.creds`` so the
    underlying ``_run_op`` sees plaintext. The admin route pre-decrypts so
    this is a no-op in that path; the executor / future CLI rely on it."""
    from dataclasses import replace as dc_replace

    from cryptography.fernet import Fernet

    from sagewai.connections.credentials import CredentialsBackendRouter
    from sagewai.connections.protocols.base import PluginContext

    # Provide a master key so the local backend works in this test.
    import os
    saved = os.environ.get("SAGEWAI_MASTER_KEY")
    os.environ["SAGEWAI_MASTER_KEY"] = Fernet.generate_key().decode()
    try:
        router = CredentialsBackendRouter(default_backend="local")
        encrypted_pd = router.encrypt(
            {
                "base_uri": "coaps://device.example.com:5684",
                "psk_identity": "id",
                "psk_key": "realsecrethere",
                "default_timeout_seconds": 5.0,
            },
            sensitive_field_paths=("psk_key",),
            connection_credentials_backend={"kind": "local"},
        )
        encrypted_conn = dc_replace(
            _conn({"base_uri": "coaps://device.example.com:5684"}),
            protocol_data=encrypted_pd,
            credentials_backend={"kind": "local"},
        )
        # Confirm setup: psk_key is ciphertext.
        assert encrypted_conn.protocol_data["psk_key"].startswith("fernet:")

        captured_psk: list[str] = []

        # Intercept the PSK setup by stubbing the CredentialsMap parser so
        # the executor's PSK build path is exercised but the aiocoap-internal
        # ASCII validator doesn't get hit (we only care that the runner sees
        # the plaintext psk_key after decryption).
        from aiocoap.credentials import CredentialsMap

        def _spy_load(self, data):
            for _, v in data.items():
                psk = v["dtls"]["psk"]
                captured_psk.append(
                    psk.get("key-ascii") or psk.get("hex") or ""
                )
            # Don't actually parse; aiocoap's ASCII validator is fussy
            # about non-hex/non-printable bytes in unit-test contexts.
            return None

        fake_context = _make_fake_context(_make_fake_response(dotted_code="2.05"))

        ctx = PluginContext(
            store=MagicMock(),
            creds=router,
            project_id="proj-test",
            request=None,
        )

        with patch.object(CredentialsMap, "load_from_dict", _spy_load):
            with patch(
                "aiocoap.Context.create_client_context",
                AsyncMock(return_value=fake_context),
            ):
                result = await CoapProtocolPlugin().test(encrypted_conn, ctx=ctx)

        assert result.ok is True, f"plugin.test() failed: {result.message}"
        # The runner received the DECRYPTED psk_key.
        assert captured_psk == ["realsecrethere"]
    finally:
        if saved is None:
            os.environ.pop("SAGEWAI_MASTER_KEY", None)
        else:
            os.environ["SAGEWAI_MASTER_KEY"] = saved


@pytest.mark.asyncio
async def test_post_sets_content_format_id_on_message():
    """Regression for #6: content_format string must map to the RFC integer id
    on the constructed aiocoap.Message via opt.content_format."""
    conn = _conn({"base_uri": "coap://device.example.com:5683"})

    constructed_messages: list = []

    def _msg_factory(**kw):
        m = MagicMock()
        m.opt = MagicMock()
        # Track which opt.content_format the executor assigns.
        type(m.opt).content_format = property(
            lambda self: getattr(self, "_cf", None),
            lambda self, v: setattr(self, "_cf", v),
        )
        m._captured_kwargs = kw
        constructed_messages.append(m)
        return m

    fake_response = _make_fake_response(dotted_code="2.01", location_path=("things", "1"))
    fake_context = _make_fake_context(fake_response)

    with patch("aiocoap.Message", side_effect=_msg_factory):
        with patch("aiocoap.Context.create_client_context",
                   AsyncMock(return_value=fake_context)):
            await _run_op(
                conn,
                op="post",
                args={
                    "path": "/things",
                    "payload": b'{"v":1}',
                    "content_format": "application/json",
                },
            )

    assert constructed_messages, "aiocoap.Message was never constructed"
    msg = constructed_messages[0]
    # application/json maps to RFC 7252 content_format id 50.
    assert msg.opt._cf == 50, (
        f"expected opt.content_format=50 for 'application/json', got {msg.opt._cf!r}"
    )
