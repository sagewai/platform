# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Modbus protocol plugin tests (schema + errors + public_view)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.connections.protocols.modbus import (
    ModbusConnectionError,
    ModbusError,
    ModbusNotInstalledError,
    ModbusProtocolData,
    ModbusProtocolError,
    ModbusProtocolPlugin,
    ModbusTimeoutError,
)


# ── errors ────────────────────────────────────────────────────────────


def test_error_hierarchy():
    assert issubclass(ModbusNotInstalledError, ModbusError)
    assert issubclass(ModbusTimeoutError, ModbusError)
    assert issubclass(ModbusProtocolError, ModbusError)
    assert issubclass(ModbusConnectionError, ModbusError)


def test_error_codes_stable():
    assert ModbusError.code == "modbus_error"
    assert ModbusNotInstalledError.code == "modbus_not_installed"
    assert ModbusTimeoutError.code == "modbus_timeout"
    assert ModbusProtocolError.code == "modbus_protocol_error"
    assert ModbusConnectionError.code == "modbus_connection_error"


def test_protocol_error_carries_function_and_exception_codes():
    err = ModbusProtocolError(function_code=3, exception_code=2, message="Illegal Data Address")
    assert err.function_code == 3
    assert err.exception_code == 2
    assert "Illegal Data Address" in str(err)
    assert "fc=3" in str(err) or "function=3" in str(err)
    assert "ex=2" in str(err) or "exception=2" in str(err)


# ── schema ────────────────────────────────────────────────────────────


def test_schema_minimal_valid():
    data = ModbusProtocolData(host="192.168.1.50")
    assert data.host == "192.168.1.50"
    assert data.port == 502
    assert data.transport == "tcp"
    assert data.unit_id == 1
    assert data.default_timeout_seconds == 3.0
    assert data.sandbox_tier_override is None


def test_schema_explicit_all_fields():
    data = ModbusProtocolData(
        host="plc.example.com",
        port=1502,
        transport="tcp",
        unit_id=17,
        default_timeout_seconds=5.0,
    )
    assert data.host == "plc.example.com"
    assert data.port == 1502
    assert data.unit_id == 17
    assert data.default_timeout_seconds == 5.0


def test_schema_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ModbusProtocolData(host="x", unknown_field=True)


def test_schema_rejects_invalid_port():
    with pytest.raises(ValidationError):
        ModbusProtocolData(host="x", port=0)
    with pytest.raises(ValidationError):
        ModbusProtocolData(host="x", port=70000)


def test_schema_rejects_invalid_unit_id():
    """Modbus unit_id is uint8, range 0-247 per spec."""
    with pytest.raises(ValidationError):
        ModbusProtocolData(host="x", unit_id=-1)
    with pytest.raises(ValidationError):
        ModbusProtocolData(host="x", unit_id=248)


def test_schema_transport_locked_to_tcp_in_phase_a():
    """Phase A only ships TCP transport. RTU-over-TCP and serial deferred."""
    with pytest.raises(ValidationError):
        ModbusProtocolData(host="x", transport="serial")
    with pytest.raises(ValidationError):
        ModbusProtocolData(host="x", transport="rtu_over_tcp")


def test_schema_default_timeout_must_be_positive():
    with pytest.raises(ValidationError):
        ModbusProtocolData(host="x", default_timeout_seconds=0)
    with pytest.raises(ValidationError):
        ModbusProtocolData(host="x", default_timeout_seconds=-1)


def test_schema_sandbox_tier_override_only_allows_relaxation():
    """Default is UNTRUSTED; override may relax to TRUSTED or SANDBOXED."""
    ok = ModbusProtocolData(host="x", sandbox_tier_override="SANDBOXED")
    assert ok.sandbox_tier_override == "SANDBOXED"
    ok2 = ModbusProtocolData(host="x", sandbox_tier_override="TRUSTED")
    assert ok2.sandbox_tier_override == "TRUSTED"
    # The schema accepts UNTRUSTED too (redundant but harmless — same as default).
    with pytest.raises(ValidationError):
        # But junk values are rejected.
        ModbusProtocolData(host="x", sandbox_tier_override="bogus")


# ── plugin identity ───────────────────────────────────────────────────


def test_plugin_identity():
    p = ModbusProtocolPlugin()
    assert p.id == "modbus"
    assert p.display_name == "Modbus"
    assert p.sensitive_fields == ()


def test_plugin_schema_returns_pydantic_model():
    p = ModbusProtocolPlugin()
    assert p.protocol_data_schema() is ModbusProtocolData


# ── public_view ───────────────────────────────────────────────────────


def test_public_view_pass_through():
    """Modbus has no sensitive fields; public_view returns input unchanged."""
    p = ModbusProtocolPlugin()
    data = {
        "host": "192.168.1.50",
        "port": 502,
        "transport": "tcp",
        "unit_id": 1,
    }
    out = p.public_view(data)
    assert out == data
    out2 = p.public_view(data, include_secrets=True)
    assert out2 == data


# ── registration ──────────────────────────────────────────────────────


def test_plugin_registered_in_PROTOCOLS():
    from sagewai.connections.protocols import PROTOCOLS, get_protocol

    ids = {p.id for p in PROTOCOLS}
    assert "modbus" in ids
    plugin = get_protocol("modbus")
    assert isinstance(plugin, ModbusProtocolPlugin)


def test_plugin_runtime_checkable_protocol():
    from sagewai.connections.protocols.base import ProtocolPlugin

    plugin = ModbusProtocolPlugin()
    assert isinstance(plugin, ProtocolPlugin)


# ── on_create diagnostic warnings ─────────────────────────────────────


def _conn_for_on_create(protocol_data: dict):
    """Build a minimal Connection for on_create() warning tests."""
    from datetime import datetime, timezone

    from sagewai.connections.models import Connection

    now = datetime.now(timezone.utc).isoformat()
    return Connection(
        id="conn-modbus-on-create",
        protocol="modbus",
        project_id="proj-test",
        display_name="warn-target",
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


@pytest.mark.asyncio
async def test_on_create_warns_on_nonstandard_port(caplog):
    """port != 502 is worth flagging — the standard Modbus/TCP port is 502."""
    from unittest.mock import MagicMock

    plugin = ModbusProtocolPlugin()
    conn = _conn_for_on_create({"host": "x", "port": 1502, "unit_id": 1})
    ctx = MagicMock()
    with caplog.at_level("WARNING", logger="sagewai.connections.protocols.modbus"):
        result = await plugin.on_create(conn, ctx=ctx)
    assert result is conn
    assert any("non-default port" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_on_create_warns_on_unit_id_0(caplog):
    """unit_id == 0 is broadcast on some devices — confirm before silently accepting."""
    from unittest.mock import MagicMock

    plugin = ModbusProtocolPlugin()
    conn = _conn_for_on_create({"host": "x", "port": 502, "unit_id": 0})
    ctx = MagicMock()
    with caplog.at_level("WARNING", logger="sagewai.connections.protocols.modbus"):
        result = await plugin.on_create(conn, ctx=ctx)
    assert result is conn
    assert any("unit_id 0" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_on_create_silent_on_default_values(caplog):
    """No warning fires when port=502 and unit_id is a normal value."""
    from unittest.mock import MagicMock

    plugin = ModbusProtocolPlugin()
    conn = _conn_for_on_create({"host": "x", "port": 502, "unit_id": 1})
    ctx = MagicMock()
    with caplog.at_level("WARNING", logger="sagewai.connections.protocols.modbus"):
        result = await plugin.on_create(conn, ctx=ctx)
    assert result is conn
    assert not any("non-default port" in r.message for r in caplog.records)
    assert not any("unit_id 0" in r.message for r in caplog.records)
