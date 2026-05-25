# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Modbus/TCP plugin (Modbus Application Protocol V1.1b3).

Phase A PR2 of new protocols rollout. Provides 8 builtin operations
corresponding to the standard Modbus function codes 0x01–0x10. No auth
(standard Modbus/TCP doesn't define any — operators are expected to
firewall/VPN-gate access).

The plugin is per-call: each operation opens an AsyncModbusTcpClient,
sends one request, awaits one response, and closes. No connection
pooling (Phase A scope). Phase B may add a long-lived pool alongside
the MCP/OPC UA pool work.
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar, Literal, Mapping

import click
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from sagewai.connections.models import Connection, TestResult
from sagewai.connections.protocols.base import PluginContext

logger = logging.getLogger(__name__)


# ── errors ────────────────────────────────────────────────────────────


class ModbusError(Exception):
    """Base for all Modbus plugin errors."""

    code: ClassVar[str] = "modbus_error"


class ModbusNotInstalledError(ModbusError):
    """The ``pymodbus`` library is not installed."""

    code: ClassVar[str] = "modbus_not_installed"

    def __init__(self) -> None:
        super().__init__(
            "pymodbus is not installed. Run `pip install sagewai[modbus]` to enable Modbus connections."
        )


class ModbusConnectionError(ModbusError):
    """Failed to open the underlying TCP connection."""

    code: ClassVar[str] = "modbus_connection_error"


class ModbusTimeoutError(ModbusError):
    """A Modbus request didn't complete within the configured timeout."""

    code: ClassVar[str] = "modbus_timeout"


class ModbusProtocolError(ModbusError):
    """Server returned a Modbus exception response (fc | 0x80, exception code).

    Carries the requested function code and the exception code (1–11 per
    spec) for diagnostic purposes. Exception code 2 ("Illegal Data
    Address") is treated as "connection works" by the test() probe.
    """

    code: ClassVar[str] = "modbus_protocol_error"

    EXCEPTION_NAMES: ClassVar[dict[int, str]] = {
        1: "Illegal Function",
        2: "Illegal Data Address",
        3: "Illegal Data Value",
        4: "Server Device Failure",
        5: "Acknowledge",
        6: "Server Device Busy",
        7: "Negative Acknowledge",
        8: "Memory Parity Error",
        10: "Gateway Path Unavailable",
        11: "Gateway Target Device Failed to Respond",
    }

    def __init__(self, *, function_code: int, exception_code: int, message: str | None = None) -> None:
        self.function_code = function_code
        self.exception_code = exception_code
        name = self.EXCEPTION_NAMES.get(exception_code, f"Unknown ({exception_code})")
        self.exception_name = name
        text = message or name
        super().__init__(f"modbus fc={function_code} ex={exception_code} ({name}): {text}")


# ── schema ────────────────────────────────────────────────────────────


class ModbusProtocolData(BaseModel):
    """Validation schema for ``protocol_data`` of Modbus connections."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(..., min_length=1)
    port: int = Field(default=502, ge=1, le=65535)
    transport: Literal["tcp"] = "tcp"  # Phase A: TCP only
    unit_id: int = Field(default=1, ge=0, le=247)
    default_timeout_seconds: float = Field(default=3.0, gt=0)
    sandbox_tier_override: Literal["TRUSTED", "SANDBOXED", "UNTRUSTED"] | None = None


# ── executor ──────────────────────────────────────────────────────────


_VALID_OPS: tuple[str, ...] = (
    "read_coils",
    "read_discrete_inputs",
    "read_holding_registers",
    "read_input_registers",
    "write_single_coil",
    "write_single_register",
    "write_multiple_coils",
    "write_multiple_registers",
)


# Map plugin op names to pymodbus client method names. Most are identical;
# write_single_* and write_multiple_* differ.
_OP_TO_CLIENT_METHOD: dict[str, str] = {
    "read_coils": "read_coils",
    "read_discrete_inputs": "read_discrete_inputs",
    "read_holding_registers": "read_holding_registers",
    "read_input_registers": "read_input_registers",
    "write_single_coil": "write_coil",
    "write_single_register": "write_register",
    "write_multiple_coils": "write_coils",
    "write_multiple_registers": "write_registers",
}


# Modbus function code (FC) for each plugin op. Used to build informative
# ModbusProtocolError messages (the response's function_code already has
# the 0x80 exception bit set, but the plain FC is friendlier).
_OP_TO_FC: dict[str, int] = {
    "read_coils": 1,
    "read_discrete_inputs": 2,
    "read_holding_registers": 3,
    "read_input_registers": 4,
    "write_single_coil": 5,
    "write_single_register": 6,
    "write_multiple_coils": 15,
    "write_multiple_registers": 16,
}


def _import_pymodbus():
    """Lazy-import pymodbus with a clear error message when missing."""
    try:
        from pymodbus.client import AsyncModbusTcpClient  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ModbusNotInstalledError() from exc
    return AsyncModbusTcpClient


def _resolve_unit_id(args: Mapping[str, Any], protocol_data: Mapping[str, Any]) -> int:
    """Per-call unit_id arg overrides the connection default."""
    if "unit_id" in args and args["unit_id"] is not None:
        return int(args["unit_id"])
    return int(protocol_data.get("unit_id", 1))


def _call_args_for(op: str, args: Mapping[str, Any], unit_id: int) -> tuple[tuple, dict[str, Any]]:
    """Build (positional, kwargs) for the pymodbus client method.

    pymodbus 3.13.x signatures:
      - read ops: ``address`` (positional), ``count`` (kwarg), ``device_id`` (kwarg)
      - write_single_coil/register: ``address``, ``value`` (positional), ``device_id`` (kwarg)
      - write_multiple_coils/registers: ``address``, ``values`` (positional), ``device_id`` (kwarg)

    The unit/slave/device_id kwarg has changed across pymodbus versions:
    3.x early releases used ``unit``; mid-3.x used ``slave``; 3.13.0+ uses
    ``device_id``. We target 3.13+ (the pinned library) and use
    ``device_id``.
    """
    address = args["address"]
    if op in {"read_coils", "read_discrete_inputs", "read_holding_registers", "read_input_registers"}:
        return (address,), {"count": args["count"], "device_id": unit_id}
    if op in {"write_single_coil", "write_single_register"}:
        return (address, args["value"]), {"device_id": unit_id}
    if op in {"write_multiple_coils", "write_multiple_registers"}:
        return (address, args["values"]), {"device_id": unit_id}
    # Defensive — _VALID_OPS check above should have caught this.
    raise ValueError(f"unhandled modbus op: {op!r}")  # pragma: no cover


async def _run_op(
    connection: Connection,
    *,
    op: str,
    args: Mapping[str, Any],
) -> Any:
    """Dispatch a Modbus request and parse the response.

    Raises:
        ValueError: ``op`` is not one of the 8 builtin op names.
        ModbusNotInstalledError: pymodbus is not installed.
        ModbusConnectionError: TCP connection failed.
        ModbusTimeoutError: the rpc didn't complete within
            ``protocol_data.default_timeout_seconds``.
        ModbusProtocolError: the server returned an exception response.
    """
    import asyncio

    if op not in _VALID_OPS:
        raise ValueError(f"unknown modbus operation: {op!r}")

    AsyncModbusTcpClient = _import_pymodbus()

    data = connection.protocol_data
    host = data["host"]
    port = int(data.get("port", 502))
    timeout = float(data.get("default_timeout_seconds", 3.0))
    unit_id = _resolve_unit_id(args, data)

    # Lazy-import the pymodbus exception module so we can normalize any
    # ConnectionException / ModbusIOException / ModbusException subclass
    # into our own ModbusError hierarchy. Without this, raw pymodbus
    # exceptions propagate through ``_run_op`` past ``test()`` (which only
    # catches ``ModbusError``) and the shared executor (which doesn't
    # catch them at all), surfacing as opaque 500s / mission traceback.
    try:
        import pymodbus.exceptions as _pme  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover — defensive; _import_pymodbus already ran
        _pme = None

    client = AsyncModbusTcpClient(host=host, port=port, timeout=timeout)
    try:
        connected = await client.connect()
        if not connected or not getattr(client, "connected", True):
            raise ModbusConnectionError(f"failed to connect to {host}:{port}")

        method_name = _OP_TO_CLIENT_METHOD[op]
        method = getattr(client, method_name)
        pos, kw = _call_args_for(op, args, unit_id)

        try:
            response = await method(*pos, **kw)
        except asyncio.TimeoutError as exc:
            raise ModbusTimeoutError(
                f"modbus {op} address={args.get('address')} timed out after {timeout}s"
            ) from exc
        except Exception as exc:
            # Normalize known pymodbus exceptions into our hierarchy.
            # Transport-level failures (ConnectionException, ModbusIOException)
            # surface as ModbusConnectionError; everything else under the
            # ModbusException umbrella becomes a generic ModbusError.
            if _pme is not None:
                if isinstance(exc, (_pme.ConnectionException, _pme.ModbusIOException)):
                    raise ModbusConnectionError(str(exc)) from exc
                if isinstance(exc, _pme.ModbusException):
                    raise ModbusError(str(exc)) from exc
            raise

        if response is None:
            raise ModbusProtocolError(
                function_code=_OP_TO_FC[op],
                exception_code=0,
                message="empty response (transport-level failure)",
            )

        if response.isError():
            fc = getattr(response, "function_code", _OP_TO_FC[op] | 0x80) & 0x7F
            ex = getattr(response, "exception_code", 0)
            raise ModbusProtocolError(function_code=fc, exception_code=ex)

        # Successful response — extract payload by op category.
        if op in {"read_coils", "read_discrete_inputs"}:
            # response.bits may include padding to byte boundary; trim to count.
            count = int(args["count"])
            return list(bool(b) for b in response.bits[:count])
        if op in {"read_holding_registers", "read_input_registers"}:
            return list(int(r) for r in response.registers)
        # All write ops return {"ok": True} on non-error response.
        return {"ok": True}
    finally:
        # pymodbus 3.13.0's AsyncModbusTcpClient.close() is sync; older
        # versions had async close(). Handle both via the awaitable probe.
        close = getattr(client, "close", None)
        if close is not None:
            try:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            except Exception:  # pragma: no cover — defensive
                pass


# ── plugin ────────────────────────────────────────────────────────────


class ModbusProtocolPlugin:
    """Modbus/TCP plugin — 8 builtin function codes."""

    id: ClassVar[str] = "modbus"
    display_name: ClassVar[str] = "Modbus"
    sensitive_fields: ClassVar[tuple[str, ...]] = ()

    def protocol_data_schema(self) -> type[BaseModel]:
        return ModbusProtocolData

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        # No sensitive fields — return as-is.
        return dict(protocol_data)

    async def on_create(self, connection: Connection, *, ctx: PluginContext) -> Connection:
        """Pass-through with diagnostic warnings for non-default values worth flagging.

        Two configurations get logged at WARNING level because they're
        common foot-guns even though both are valid Modbus configurations:

        - ``port != 502`` — the IANA-assigned standard Modbus/TCP port
          is 502; deviations are usually a misconfiguration or a gateway
          tunneling decision the operator should consciously make.
        - ``unit_id == 0`` — some devices treat unit_id 0 as a broadcast
          address; on TCP it's usually a "don't care" but operators
          should confirm against the device manual.
        """
        pd = connection.protocol_data
        port = pd.get("port", 502)
        unit_id = pd.get("unit_id", 1)
        if port != 502:
            logger.warning(
                "modbus connection %s uses non-default port %d (standard is 502)",
                connection.display_name,
                port,
            )
        if unit_id == 0:
            logger.warning(
                "modbus connection %s uses unit_id 0 "
                "(some devices use this as broadcast — confirm with device manual)",
                connection.display_name,
            )
        return connection

    async def on_update(
        self, before: Connection, after: Connection, *, ctx: PluginContext
    ) -> Connection:
        return after

    async def on_delete(self, connection: Connection, *, ctx: PluginContext) -> None:
        return None

    async def test(
        self, connection: Connection, *, ctx: PluginContext
    ) -> TestResult:
        """Probe by reading holding register 0; accept exception 2 as pass.

        Exception code 2 ("Illegal Data Address") indicates the TCP +
        Modbus stack worked end-to-end and the device responded — the
        device just doesn't have a register mapped at address 0. That's
        a normal probe result for many PLCs.

        Modbus has no sensitive fields, so no defensive decrypt is
        needed — the connection's protocol_data is already plaintext.
        """
        try:
            await _run_op(
                connection,
                op="read_holding_registers",
                args={"address": 0, "count": 1},
            )
            return TestResult(ok=True, message="modbus connection ok")
        except ModbusProtocolError as exc:
            if exc.exception_code == 2:
                return TestResult(
                    ok=True,
                    message="modbus connection ok (server returned ex=2 Illegal Data Address)",
                )
            return TestResult(ok=False, message=str(exc))
        except ModbusError as exc:
            return TestResult(ok=False, message=str(exc))

    def extra_routes(self) -> APIRouter:
        return APIRouter()

    def extra_cli(self) -> list[click.Command]:
        return []


__all__ = [
    "ModbusConnectionError",
    "ModbusError",
    "ModbusNotInstalledError",
    "ModbusProtocolData",
    "ModbusProtocolError",
    "ModbusProtocolPlugin",
    "ModbusTimeoutError",
]
