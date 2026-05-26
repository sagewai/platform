# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OPC UA executor + op dispatch tests."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.connections.models import Connection
from sagewai.connections.protocols.opcua import (
    OpcuaAuthError,
    OpcuaConnectionError,
    OpcuaError,
    OpcuaNotInstalledError,
    OpcuaProtocolPlugin,
    OpcuaReadError,
    OpcuaUnknownOperationError,
    _run_op,
)


def _conn(protocol_data: dict) -> Connection:
    now = datetime.now(timezone.utc).isoformat()
    return Connection(
        id="conn-opcua-test",
        protocol="opcua",
        project_id="proj-test",
        display_name="test-opcua",
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


def _make_fake_client(*, datavalue: MagicMock | None = None, connect_raises=None):
    """Build an asyncua.Client mock supporting connect/disconnect + read_data_value."""
    fake = MagicMock()
    if connect_raises is not None:
        fake.connect = AsyncMock(side_effect=connect_raises)
    else:
        fake.connect = AsyncMock(return_value=None)
    fake.disconnect = AsyncMock(return_value=None)

    fake_node = MagicMock()
    fake_node.read_data_value = AsyncMock(return_value=datavalue)
    fake.get_node = MagicMock(return_value=fake_node)

    # Auth setters are no-ops in the mock.
    fake.set_user = MagicMock()
    fake.set_password = MagicMock()
    fake.set_security_string = MagicMock()
    return fake, fake_node


def _make_datavalue(*, value=42, status_name="Good", is_good=True):
    """Build a fake asyncua DataValue."""
    dv = MagicMock()
    inner = MagicMock()
    inner.Value = value
    dv.Value = inner
    src = MagicMock()
    src.isoformat = MagicMock(return_value="2026-05-26T12:00:00+00:00")
    dv.SourceTimestamp = src
    srv = MagicMock()
    srv.isoformat = MagicMock(return_value="2026-05-26T12:00:00.001+00:00")
    dv.ServerTimestamp = srv
    sc = MagicMock()
    sc.name = status_name
    sc.is_good = MagicMock(return_value=is_good)
    dv.StatusCode = sc
    return dv


# ── happy path ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_op_by_name_returns_data_value_dict():
    conn = _conn({
        "endpoint_url": "opc.tcp://x:4840",
        "security_mode": "None",
        "security_policy": "None",
        "auth_mode": "anonymous",
        "username": "",
        "password": "",
        "operations": [
            {"name": "read_temperature", "kind": "read", "node_id": "ns=2;s=Devices/Temperature"},
        ],
    })

    dv = _make_datavalue(value=22.5, status_name="Good")
    fake_client, fake_node = _make_fake_client(datavalue=dv)

    with patch("asyncua.Client", return_value=fake_client):
        result = await _run_op(conn, op="read_temperature", args={})

    assert result["value"] == 22.5
    assert result["status_code"] == "Good"
    assert "source_timestamp" in result
    assert "server_timestamp" in result
    fake_client.get_node.assert_called_once_with("ns=2;s=Devices/Temperature")
    fake_node.read_data_value.assert_awaited()


@pytest.mark.asyncio
async def test_unknown_op_raises_unknown_operation_error():
    conn = _conn({
        "endpoint_url": "opc.tcp://x:4840",
        "auth_mode": "anonymous",
        "operations": [
            {"name": "read_temp", "kind": "read", "node_id": "ns=2;s=T"},
        ],
    })

    with pytest.raises(OpcuaUnknownOperationError, match="read_pressure"):
        await _run_op(conn, op="read_pressure", args={})


# ── auth ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_username_auth_calls_set_user_and_set_password():
    conn = _conn({
        "endpoint_url": "opc.tcp://x:4840",
        "auth_mode": "username",
        "username": "svc_sagewai",
        "password": "supersecret",
        "operations": [{"name": "read_x", "kind": "read", "node_id": "ns=2;s=X"}],
    })

    dv = _make_datavalue(value=1)
    fake_client, _ = _make_fake_client(datavalue=dv)

    with patch("asyncua.Client", return_value=fake_client):
        await _run_op(conn, op="read_x", args={})

    fake_client.set_user.assert_called_with("svc_sagewai")
    fake_client.set_password.assert_called_with("supersecret")


@pytest.mark.asyncio
async def test_anonymous_auth_does_not_set_credentials():
    conn = _conn({
        "endpoint_url": "opc.tcp://x:4840",
        "auth_mode": "anonymous",
        "operations": [{"name": "read_x", "kind": "read", "node_id": "ns=2;s=X"}],
    })

    dv = _make_datavalue(value=1)
    fake_client, _ = _make_fake_client(datavalue=dv)

    with patch("asyncua.Client", return_value=fake_client):
        await _run_op(conn, op="read_x", args={})

    fake_client.set_user.assert_not_called()
    fake_client.set_password.assert_not_called()


# ── security (Phase A: enforced None,None at schema level) ────────────


@pytest.mark.asyncio
async def test_security_string_never_called_in_phase_a():
    """Phase A enforces security_mode=None + security_policy=None at the
    schema level (see test_protocols_opcua.py::test_protocol_data_schema_
    rejects_non_default_security_in_phase_a). The executor therefore must
    not call set_security_string at all — asyncua's set_security_string
    is an async coroutine and was being silently dropped (unawaited)."""
    conn = _conn({
        "endpoint_url": "opc.tcp://x:4840",
        "security_mode": "None",
        "security_policy": "None",
        "auth_mode": "anonymous",
        "operations": [{"name": "read_x", "kind": "read", "node_id": "ns=2;s=X"}],
    })

    dv = _make_datavalue(value=1)
    fake_client, _ = _make_fake_client(datavalue=dv)

    with patch("asyncua.Client", return_value=fake_client):
        await _run_op(conn, op="read_x", args={})

    # Phase A: set_security_string MUST NOT be called.
    fake_client.set_security_string.assert_not_called()


# ── error paths ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connection_failure_raises_connection_error():
    """asyncua raises ConnectionError or OSError on transport failure."""
    conn = _conn({
        "endpoint_url": "opc.tcp://unreachable:4840",
        "auth_mode": "anonymous",
        "operations": [{"name": "read_x", "kind": "read", "node_id": "ns=2;s=X"}],
    })

    fake_client, _ = _make_fake_client(connect_raises=OSError("connection refused"))

    with patch("asyncua.Client", return_value=fake_client):
        with pytest.raises(OpcuaConnectionError, match="connection refused"):
            await _run_op(conn, op="read_x", args={})


@pytest.mark.asyncio
async def test_bad_status_code_raises_read_error():
    """A DataValue with non-Good StatusCode should raise OpcuaReadError."""
    conn = _conn({
        "endpoint_url": "opc.tcp://x:4840",
        "auth_mode": "anonymous",
        "operations": [{"name": "read_bad", "kind": "read", "node_id": "ns=2;s=Missing"}],
    })

    dv = _make_datavalue(value=None, status_name="BadNodeIdUnknown", is_good=False)
    fake_client, _ = _make_fake_client(datavalue=dv)

    with patch("asyncua.Client", return_value=fake_client):
        with pytest.raises(OpcuaReadError) as exc_info:
            await _run_op(conn, op="read_bad", args={})

    assert exc_info.value.node_id == "ns=2;s=Missing"
    assert exc_info.value.status_code == "BadNodeIdUnknown"


@pytest.mark.asyncio
async def test_missing_asyncua_raises_not_installed_error():
    conn = _conn({
        "endpoint_url": "opc.tcp://x:4840",
        "auth_mode": "anonymous",
        "operations": [{"name": "read_x", "kind": "read", "node_id": "ns=2;s=X"}],
    })

    with patch(
        "sagewai.connections.protocols.opcua._import_asyncua",
        side_effect=OpcuaNotInstalledError(),
    ):
        with pytest.raises(OpcuaNotInstalledError):
            await _run_op(conn, op="read_x", args={})


# ── asyncua exception normalization (PR2 lesson) ──────────────────────


@pytest.mark.asyncio
async def test_asyncua_uaerror_normalized_to_read_error():
    """Raw asyncua UaError should normalize to OpcuaError subclass, not escape."""
    try:
        from asyncua.ua.uaerrors import UaError, BadUserAccessDenied  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("asyncua not installed in test env")

    conn = _conn({
        "endpoint_url": "opc.tcp://x:4840",
        "auth_mode": "anonymous",
        "operations": [{"name": "read_x", "kind": "read", "node_id": "ns=2;s=X"}],
    })

    fake_client, fake_node = _make_fake_client()
    fake_node.read_data_value = AsyncMock(side_effect=BadUserAccessDenied())

    with patch("asyncua.Client", return_value=fake_client):
        with pytest.raises(OpcuaError):  # subclass either way
            await _run_op(conn, op="read_x", args={})


@pytest.mark.asyncio
async def test_test_endpoint_reads_server_status_state():
    """plugin.test() reads node i=2259 (Server_ServerStatus_State)."""
    conn = _conn({
        "endpoint_url": "opc.tcp://x:4840",
        "auth_mode": "anonymous",
        "operations": [],  # no declared ops; test() injects its own
    })

    dv = _make_datavalue(value=0, status_name="Good")  # 0 = Running enum
    fake_client, fake_node = _make_fake_client(datavalue=dv)

    plugin = OpcuaProtocolPlugin()
    ctx = MagicMock()
    with patch("asyncua.Client", return_value=fake_client):
        result = await plugin.test(conn, ctx=ctx)

    assert result.ok is True
    fake_client.get_node.assert_called_once_with("i=2259")


@pytest.mark.asyncio
async def test_test_endpoint_failure_returns_not_ok():
    conn = _conn({
        "endpoint_url": "opc.tcp://unreachable:4840",
        "auth_mode": "anonymous",
        "operations": [],
    })

    fake_client, _ = _make_fake_client(connect_raises=OSError("connection refused"))

    plugin = OpcuaProtocolPlugin()
    ctx = MagicMock()
    with patch("asyncua.Client", return_value=fake_client):
        result = await plugin.test(conn, ctx=ctx)

    assert result.ok is False
    assert "connection refused" in (result.message or "").lower()


@pytest.mark.asyncio
async def test_test_method_decrypts_via_ctx_creds():
    """``plugin.test()`` must defensively decrypt via ``ctx.creds`` so the
    underlying ``_run_op_with_node`` sees plaintext credentials. The admin
    route pre-decrypts so this is a no-op in that path; the executor /
    autopilot health-check / future CLI rely on it."""
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
                "endpoint_url": "opc.tcp://x:4840",
                "security_mode": "None",
                "security_policy": "None",
                "auth_mode": "username",
                "username": "svc_sagewai",
                "password": "realsecretpassword",
                "operations": [],
            },
            sensitive_field_paths=("password",),
            connection_credentials_backend={"kind": "local"},
        )
        # Confirm setup: password is ciphertext (fernet:-prefixed).
        assert encrypted_pd["password"].startswith("fernet:")

        encrypted_conn = dc_replace(
            _conn({"endpoint_url": "opc.tcp://x:4840", "auth_mode": "username"}),
            protocol_data=encrypted_pd,
            credentials_backend={"kind": "local"},
        )

        captured_passwords: list[str] = []

        def _capture_set_password(pw: str) -> None:
            captured_passwords.append(pw)

        dv = _make_datavalue(value=0, status_name="Good")
        fake_client, _ = _make_fake_client(datavalue=dv)
        fake_client.set_password = MagicMock(side_effect=_capture_set_password)

        plugin = OpcuaProtocolPlugin()
        ctx = PluginContext(
            store=MagicMock(),
            creds=router,
            project_id="proj-test",
            request=None,
        )

        with patch("asyncua.Client", return_value=fake_client):
            result = await plugin.test(encrypted_conn, ctx=ctx)

        assert result.ok is True, f"plugin.test() failed: {result.message}"
        # The runner received the DECRYPTED password — not the fernet ciphertext.
        assert captured_passwords == ["realsecretpassword"]
    finally:
        if saved is None:
            os.environ.pop("SAGEWAI_MASTER_KEY", None)
        else:
            os.environ["SAGEWAI_MASTER_KEY"] = saved


# ── resource cleanup ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disconnect_called_on_success():
    conn = _conn({
        "endpoint_url": "opc.tcp://x:4840",
        "auth_mode": "anonymous",
        "operations": [{"name": "read_x", "kind": "read", "node_id": "ns=2;s=X"}],
    })

    dv = _make_datavalue(value=1)
    fake_client, _ = _make_fake_client(datavalue=dv)

    with patch("asyncua.Client", return_value=fake_client):
        await _run_op(conn, op="read_x", args={})

    fake_client.disconnect.assert_awaited()


@pytest.mark.asyncio
async def test_disconnect_called_on_read_error():
    conn = _conn({
        "endpoint_url": "opc.tcp://x:4840",
        "auth_mode": "anonymous",
        "operations": [{"name": "read_x", "kind": "read", "node_id": "ns=2;s=X"}],
    })

    dv = _make_datavalue(value=None, status_name="BadNodeIdUnknown", is_good=False)
    fake_client, _ = _make_fake_client(datavalue=dv)

    with patch("asyncua.Client", return_value=fake_client):
        with pytest.raises(OpcuaReadError):
            await _run_op(conn, op="read_x", args={})

    fake_client.disconnect.assert_awaited()
