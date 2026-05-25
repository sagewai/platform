# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Modbus executor + op dispatch tests."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.connections.models import Connection
from sagewai.connections.protocols.modbus import (
    ModbusConnectionError,
    ModbusNotInstalledError,
    ModbusProtocolError,
    ModbusProtocolPlugin,
    ModbusTimeoutError,
    _run_op,
)


def _conn(protocol_data: dict) -> Connection:
    now = datetime.now(timezone.utc).isoformat()
    return Connection(
        id="conn-modbus-test",
        protocol="modbus",
        project_id="proj-test",
        display_name="test-plc",
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


def _make_fake_client(*, response: MagicMock | None = None):
    """Build an AsyncModbusTcpClient mock that supports async dispatch.

    Each pymodbus client method gets its OWN AsyncMock so per-method
    ``assert_awaited()`` calls in write-op tests can catch a typo in
    ``_OP_TO_CLIENT_METHOD`` that would otherwise silently route to the
    wrong method (e.g., write_single_coil mistakenly mapped to read_coils).
    """
    fake = MagicMock()
    fake.connect = AsyncMock(return_value=True)
    # pymodbus 3.13.0 has a sync close() method.
    fake.close = MagicMock(return_value=None)
    fake.connected = True
    # One AsyncMock per method so callers can distinguish them.
    fake.read_coils = AsyncMock(return_value=response)
    fake.read_discrete_inputs = AsyncMock(return_value=response)
    fake.read_holding_registers = AsyncMock(return_value=response)
    fake.read_input_registers = AsyncMock(return_value=response)
    fake.write_coil = AsyncMock(return_value=response)
    fake.write_register = AsyncMock(return_value=response)
    fake.write_coils = AsyncMock(return_value=response)
    fake.write_registers = AsyncMock(return_value=response)
    return fake


def _make_resp(*, bits=None, registers=None, is_error=False, function_code=3, exception_code=2):
    """Build a fake pymodbus response object."""
    r = MagicMock()
    r.isError = MagicMock(return_value=is_error)
    if bits is not None:
        r.bits = bits
    if registers is not None:
        r.registers = registers
    if is_error:
        r.function_code = function_code | 0x80
        r.exception_code = exception_code
    return r


# ── read ops ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_coils_returns_bool_list():
    conn = _conn({"host": "192.168.1.50", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp(bits=[True, False, True, False])
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await _run_op(conn, op="read_coils", args={"address": 0, "count": 4})

    assert result == [True, False, True, False]
    client.read_coils.assert_awaited()


@pytest.mark.asyncio
async def test_read_holding_registers_returns_int_list():
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp(registers=[100, 200, 300])
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await _run_op(conn, op="read_holding_registers", args={"address": 100, "count": 3})

    assert result == [100, 200, 300]


@pytest.mark.asyncio
async def test_read_discrete_inputs_returns_bool_list():
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp(bits=[False, True])
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await _run_op(conn, op="read_discrete_inputs", args={"address": 0, "count": 2})

    assert result == [False, True]


@pytest.mark.asyncio
async def test_read_input_registers_returns_int_list():
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp(registers=[42])
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await _run_op(conn, op="read_input_registers", args={"address": 50, "count": 1})

    assert result == [42]


# ── write ops ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_single_coil_returns_ok():
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp()  # default = not error, no payload needed for writes
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await _run_op(
            conn, op="write_single_coil", args={"address": 10, "value": True}
        )

    assert result == {"ok": True}
    # Guard against a typo in _OP_TO_CLIENT_METHOD silently routing to the wrong method.
    client.write_coil.assert_awaited()


@pytest.mark.asyncio
async def test_write_single_register_returns_ok():
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp()
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await _run_op(
            conn, op="write_single_register", args={"address": 100, "value": 4242}
        )

    assert result == {"ok": True}
    client.write_register.assert_awaited()


@pytest.mark.asyncio
async def test_write_multiple_coils_returns_ok():
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp()
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await _run_op(
            conn,
            op="write_multiple_coils",
            args={"address": 0, "values": [True, True, False, True]},
        )

    assert result == {"ok": True}
    client.write_coils.assert_awaited()


@pytest.mark.asyncio
async def test_write_multiple_registers_returns_ok():
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp()
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await _run_op(
            conn,
            op="write_multiple_registers",
            args={"address": 200, "values": [1, 2, 3, 4]},
        )

    assert result == {"ok": True}
    client.write_registers.assert_awaited()


# ── unit_id override ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unit_id_arg_overrides_connection_default():
    """Per-call unit_id arg should be used when provided; otherwise the
    connection's protocol_data.unit_id is the default."""
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp(registers=[0])
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        await _run_op(
            conn,
            op="read_holding_registers",
            args={"address": 0, "count": 1, "unit_id": 17},
        )

    # The rpc call should have received device_id=17 (pymodbus 3.13.0 uses
    # `device_id` kwarg; older 3.x used `slave`, even older used `unit`).
    _, kwargs = client.read_holding_registers.await_args
    assert kwargs.get("device_id") == 17


@pytest.mark.asyncio
async def test_unit_id_default_used_when_not_provided():
    conn = _conn({"host": "x", "port": 502, "unit_id": 5, "default_timeout_seconds": 3.0})

    resp = _make_resp(registers=[0])
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        await _run_op(conn, op="read_holding_registers", args={"address": 0, "count": 1})

    _, kwargs = client.read_holding_registers.await_args
    assert kwargs.get("device_id") == 5


# ── error paths ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_modbus_exception_response_raises_protocol_error():
    """fc=3 + ex=2 → ModbusProtocolError(function_code=3, exception_code=2)."""
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp(is_error=True, function_code=3, exception_code=2)
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        with pytest.raises(ModbusProtocolError) as exc_info:
            await _run_op(conn, op="read_holding_registers", args={"address": 999, "count": 1})

    assert exc_info.value.function_code == 3
    assert exc_info.value.exception_code == 2


@pytest.mark.asyncio
async def test_connection_refused_raises_connection_error():
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    client = _make_fake_client()
    client.connect = AsyncMock(return_value=False)  # pymodbus signals failed connect via False
    client.connected = False

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        with pytest.raises(ModbusConnectionError):
            await _run_op(conn, op="read_coils", args={"address": 0, "count": 1})


@pytest.mark.asyncio
async def test_timeout_raises_timeout_error():
    """Pymodbus raises asyncio.TimeoutError on RPC timeout; convert to ModbusTimeoutError."""
    import asyncio

    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 0.5})

    client = _make_fake_client()
    client.read_coils = AsyncMock(side_effect=asyncio.TimeoutError("timed out"))

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        with pytest.raises(ModbusTimeoutError):
            await _run_op(conn, op="read_coils", args={"address": 0, "count": 1})


@pytest.mark.asyncio
async def test_unknown_op_raises_value_error():
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})
    with pytest.raises(ValueError, match="unknown modbus operation"):
        await _run_op(conn, op="not_a_real_op", args={})


@pytest.mark.asyncio
async def test_missing_pymodbus_raises_not_installed_error():
    """Force the lazy import to fail."""
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    with patch(
        "sagewai.connections.protocols.modbus._import_pymodbus",
        side_effect=ModbusNotInstalledError(),
    ):
        with pytest.raises(ModbusNotInstalledError):
            await _run_op(conn, op="read_coils", args={"address": 0, "count": 1})


@pytest.mark.asyncio
async def test_client_closed_on_success():
    """Resource cleanup: client.close() must be called even after a success."""
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp(registers=[1])
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        await _run_op(conn, op="read_holding_registers", args={"address": 0, "count": 1})

    client.close.assert_called()


@pytest.mark.asyncio
async def test_client_closed_on_error():
    """Resource cleanup: client.close() must be called even when the rpc errors."""
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp(is_error=True, function_code=3, exception_code=2)
    client = _make_fake_client(response=resp)

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        with pytest.raises(ModbusProtocolError):
            await _run_op(conn, op="read_holding_registers", args={"address": 0, "count": 1})

    client.close.assert_called()


# ── test() endpoint ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_endpoint_reads_holding_register_0():
    """test() does read_holding_registers(address=0, count=1, unit_id=default)."""
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp(registers=[0])
    client = _make_fake_client(response=resp)

    plugin = ModbusProtocolPlugin()
    ctx = MagicMock()
    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await plugin.test(conn, ctx=ctx)

    assert result.ok is True
    client.read_holding_registers.assert_awaited()


@pytest.mark.asyncio
async def test_test_endpoint_accepts_illegal_data_address_as_ok():
    """Exception code 2 means 'connection works but address unmapped' — counts as pass."""
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp(is_error=True, function_code=3, exception_code=2)
    client = _make_fake_client(response=resp)

    plugin = ModbusProtocolPlugin()
    ctx = MagicMock()
    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await plugin.test(conn, ctx=ctx)

    assert result.ok is True
    assert "illegal data address" in (result.message or "").lower() or "ex=2" in (result.message or "")


@pytest.mark.asyncio
async def test_test_endpoint_rejects_other_exception_codes():
    """Exception code != 2 = real failure."""
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    resp = _make_resp(is_error=True, function_code=3, exception_code=4)  # Server Device Failure
    client = _make_fake_client(response=resp)

    plugin = ModbusProtocolPlugin()
    ctx = MagicMock()
    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await plugin.test(conn, ctx=ctx)

    assert result.ok is False


@pytest.mark.asyncio
async def test_test_endpoint_failure_returns_not_ok():
    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})

    client = _make_fake_client()
    client.connect = AsyncMock(return_value=False)
    client.connected = False

    plugin = ModbusProtocolPlugin()
    ctx = MagicMock()
    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await plugin.test(conn, ctx=ctx)

    assert result.ok is False


# ── pymodbus exception normalization ──────────────────────────────────


@pytest.mark.asyncio
async def test_pymodbus_connection_exception_normalized_to_modbus_connection_error():
    """ConnectionException from pymodbus must surface as ModbusConnectionError, not raw pymodbus error."""
    from pymodbus.exceptions import ConnectionException

    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})
    client = _make_fake_client()
    client.read_holding_registers = AsyncMock(side_effect=ConnectionException("transport closed"))

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        with pytest.raises(ModbusConnectionError, match="transport closed"):
            await _run_op(conn, op="read_holding_registers", args={"address": 0, "count": 1})

    # Resource cleanup still runs.
    client.close.assert_called()


@pytest.mark.asyncio
async def test_pymodbus_io_exception_normalized_to_modbus_connection_error():
    """ModbusIOException from pymodbus must surface as ModbusConnectionError (transport-level IO)."""
    from pymodbus.exceptions import ModbusIOException

    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})
    client = _make_fake_client()
    client.read_holding_registers = AsyncMock(side_effect=ModbusIOException("io failure"))

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        with pytest.raises(ModbusConnectionError, match="io failure"):
            await _run_op(conn, op="read_holding_registers", args={"address": 0, "count": 1})

    client.close.assert_called()


@pytest.mark.asyncio
async def test_generic_pymodbus_exception_normalized_to_modbus_error():
    """Other ModbusException subclasses must surface as ModbusError (catch-all)."""
    from pymodbus.exceptions import ModbusException

    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})
    client = _make_fake_client()
    client.read_holding_registers = AsyncMock(side_effect=ModbusException("generic modbus failure"))

    # Import here so the test fails before the fix even names the exception correctly.
    from sagewai.connections.protocols.modbus import ModbusError as _ModbusError

    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        with pytest.raises(_ModbusError, match="generic modbus failure"):
            await _run_op(conn, op="read_holding_registers", args={"address": 0, "count": 1})

    client.close.assert_called()


@pytest.mark.asyncio
async def test_test_endpoint_normalizes_pymodbus_exceptions():
    """plugin.test() should return TestResult(ok=False) for pymodbus exceptions, not propagate them."""
    from pymodbus.exceptions import ConnectionException

    conn = _conn({"host": "x", "port": 502, "unit_id": 1, "default_timeout_seconds": 3.0})
    client = _make_fake_client()
    client.read_holding_registers = AsyncMock(side_effect=ConnectionException("nope"))

    plugin = ModbusProtocolPlugin()
    ctx = MagicMock()
    with patch("pymodbus.client.AsyncModbusTcpClient", return_value=client):
        result = await plugin.test(conn, ctx=ctx)

    assert result.ok is False
    assert "nope" in (result.message or "").lower() or "connection" in (result.message or "").lower()
