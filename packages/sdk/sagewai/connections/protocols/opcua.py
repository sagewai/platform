# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OPC UA (IEC 62541) plugin — Phase A reads only.

First declarative-ops protocol: operators define `operations[]` inside
`protocol_data` with `{name, kind: read, node_id}` per op. Each op
invocation looks up by name in the operations list, opens a session,
reads the node, returns the DataValue dict, and tears down. No
connection pooling (Phase A scope). Method calls and subscriptions
deferred to Phase B.

Auth: anonymous (default) or username/password. Certificate auth is
deferred to Phase B.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, ClassVar, Literal

import click
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.connections.credentials import CredentialsBackendRouter
from sagewai.connections.models import Connection, TestResult
from sagewai.connections.protocols.base import (
    PluginContext,
    get_sensitive_field_paths_for,
)


# ── errors ────────────────────────────────────────────────────────────


class OpcuaError(Exception):
    """Base for all OPC UA plugin errors."""

    code: ClassVar[str] = "opcua_error"


class OpcuaNotInstalledError(OpcuaError):
    """The ``asyncua`` library is not installed."""

    code: ClassVar[str] = "opcua_not_installed"

    def __init__(self) -> None:
        super().__init__(
            "asyncua is not installed. Run `pip install sagewai[opcua]` to enable OPC UA connections."
        )


class OpcuaConnectionError(OpcuaError):
    """Failed to open the underlying connection (transport-level)."""

    code: ClassVar[str] = "opcua_connection_error"


class OpcuaAuthError(OpcuaError):
    """Authentication / session activation failed."""

    code: ClassVar[str] = "opcua_auth_error"


class OpcuaSessionError(OpcuaError):
    """Session-level failure (e.g., closed mid-operation)."""

    code: ClassVar[str] = "opcua_session_error"


class OpcuaReadError(OpcuaError):
    """A read against a specific node failed with a bad status.

    Carries the requested ``node_id`` and the OPC UA status code name
    (e.g., ``"BadNodeIdUnknown"``).
    """

    code: ClassVar[str] = "opcua_read_error"

    def __init__(self, *, node_id: str, status_code: str, message: str | None = None) -> None:
        self.node_id = node_id
        self.status_code = status_code
        text = message or f"read failed with status {status_code}"
        super().__init__(f"opcua read {node_id}: {text}")


class OpcuaUnknownOperationError(OpcuaError):
    """The named operation isn't declared in the connection's operations[]."""

    code: ClassVar[str] = "opcua_unknown_operation"

    def __init__(self, *, name: str) -> None:
        self.name = name
        super().__init__(f"unknown opcua operation: {name!r}")


# ── executor ──────────────────────────────────────────────────────────


def _import_asyncua():
    """Lazy-import asyncua with a clear error message when missing."""
    try:
        import asyncua  # type: ignore[import-not-found]
        import asyncua.ua.uaerrors  # noqa: F401
    except ImportError as exc:
        raise OpcuaNotInstalledError() from exc
    return asyncua


def _lookup_op(operations: list[dict], op_name: str) -> dict:
    """Find the operation with matching `name` in the connection's operations list."""
    for op in operations:
        if op.get("name") == op_name:
            return op
    raise OpcuaUnknownOperationError(name=op_name)


def _apply_auth(client, protocol_data: dict) -> None:
    """Apply username/password auth — both setters are synchronous in asyncua."""
    auth_mode = protocol_data.get("auth_mode", "anonymous")
    if auth_mode == "username":
        username = protocol_data.get("username") or ""
        password = protocol_data.get("password") or ""
        client.set_user(username)
        client.set_password(password)


# Phase A enforces security_mode='None' + security_policy='None' at the
# schema level (see OpcuaProtocolData._validate_security_phase_a). The
# executor therefore never calls asyncua's set_security_string — that
# method is a coroutine in asyncua 1.x, and calling it without `await`
# silently dropped security configuration in earlier drafts of this
# plugin. Phase B will add certificate-path fields and re-introduce a
# proper async _apply_security helper.


def _format_datavalue(node_id: str, dv: Any) -> dict[str, Any]:
    """Convert an asyncua DataValue into the result dict shape."""
    return {
        "value": dv.Value.Value,
        "source_timestamp": dv.SourceTimestamp.isoformat(),
        "server_timestamp": dv.ServerTimestamp.isoformat(),
        "status_code": dv.StatusCode.name,
    }


def _normalize_asyncua_exception(exc: Exception, *, node_id: str) -> None:
    """Map raw asyncua exceptions to our OpcuaError hierarchy.

    Called inside _run_op's try block; always raises (never returns).
    """
    # Lazy import — asyncua may not be installed during some test paths.
    try:
        from asyncua.ua import uaerrors as _ua  # type: ignore[import-not-found]
    except ImportError:
        # If asyncua is gone, just re-raise the original.
        raise exc

    # Auth-class errors.
    auth_classes = []
    for name in ("BadUserAccessDenied", "BadIdentityTokenInvalid", "BadIdentityTokenRejected"):
        cls = getattr(_ua, name, None)
        if cls is not None:
            auth_classes.append(cls)

    # Session-class errors.
    session_classes = []
    for name in ("BadSessionClosed", "BadSessionIdInvalid", "BadServerNotConnected"):
        cls = getattr(_ua, name, None)
        if cls is not None:
            session_classes.append(cls)

    if auth_classes and isinstance(exc, tuple(auth_classes)):
        raise OpcuaAuthError(str(exc)) from exc
    if session_classes and isinstance(exc, tuple(session_classes)):
        raise OpcuaSessionError(str(exc)) from exc

    # Generic UaError → OpcuaReadError with status decoded from the
    # exception class name (best-effort).
    ua_error_base = getattr(_ua, "UaError", None)
    if ua_error_base is not None and isinstance(exc, ua_error_base):
        status_name = type(exc).__name__
        raise OpcuaReadError(node_id=node_id, status_code=status_name, message=str(exc)) from exc

    # Connection-class wrappers (OSError, asyncio.IncompleteReadError, etc.)
    if isinstance(exc, OSError):
        raise OpcuaConnectionError(str(exc)) from exc

    # Anything else: re-raise unchanged so the caller sees the truth.
    raise exc


async def _run_op_with_node(
    connection: Connection,
    *,
    node_id: str,
) -> dict[str, Any]:
    """Read a specific OPC UA node and return the formatted DataValue.

    Internal helper used by both _run_op (declared ops) and test()
    (which reads Server_ServerStatus_State without a declared op).
    """
    asyncua = _import_asyncua()

    data = connection.protocol_data
    endpoint_url = data["endpoint_url"]

    client = asyncua.Client(url=endpoint_url)
    try:
        # Phase A: security_mode + security_policy locked to "None,None"
        # at the schema level; no set_security_string call (deferred to
        # Phase B when cert-path fields land).
        _apply_auth(client, data)

        try:
            await client.connect()
        except OSError as exc:
            raise OpcuaConnectionError(str(exc)) from exc

        # Map asyncua-specific exception classes to our hierarchy.
        try:
            node = client.get_node(node_id)
            dv = await node.read_data_value()
        except OpcuaError:
            raise
        except Exception as exc:
            _normalize_asyncua_exception(exc, node_id=node_id)
            raise  # pragma: no cover — _normalize_asyncua_exception always raises

        # If the DataValue has a non-Good status, treat as a read error.
        status = dv.StatusCode
        is_good = getattr(status, "is_good", None)
        if callable(is_good) and not is_good():
            raise OpcuaReadError(node_id=node_id, status_code=status.name)

        return _format_datavalue(node_id, dv)
    finally:
        try:
            await client.disconnect()
        except Exception:  # pragma: no cover — defensive
            pass


async def _run_op(
    connection: Connection,
    *,
    op: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Look up the declared op by name and read its node_id."""
    operations = connection.protocol_data.get("operations", []) or []
    op_spec = _lookup_op(operations, op)
    return await _run_op_with_node(connection, node_id=op_spec["node_id"])


# ── schemas ───────────────────────────────────────────────────────────


# IEC 62541 NodeId grammar: optionally `ns=<n>;` prefix, then one of
# `i=<int>` (numeric) | `s=<string>` | `g=<guid>` | `b=<base64>`.
_NODE_ID_PATTERN = r"^(ns=\d+;)?[isgb]=.+$"


class OpcuaOperation(BaseModel):
    """One declared read operation on an OPC UA connection."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    kind: Literal["read"] = "read"  # Phase A: read only
    node_id: str = Field(..., pattern=_NODE_ID_PATTERN)


class OpcuaProtocolData(BaseModel):
    """Validation schema for ``protocol_data`` of OPC UA connections."""

    model_config = ConfigDict(extra="forbid")

    endpoint_url: str = Field(..., pattern=r"^opc\.tcp://")
    security_mode: Literal["None", "Sign", "SignAndEncrypt"] = "None"
    security_policy: Literal[
        "None", "Basic256Sha256", "Basic256", "Basic128Rsa15",
        "Aes128_Sha256_RsaOaep", "Aes256_Sha256_RsaPss",
    ] = "None"
    auth_mode: Literal["anonymous", "username"] = "anonymous"
    username: str = ""
    password: str = ""  # sensitive
    operations: list[OpcuaOperation] = Field(default_factory=list)
    sandbox_tier_override: Literal["TRUSTED", "SANDBOXED"] | None = None

    @model_validator(mode="after")
    def _validate_username_when_required(self) -> "OpcuaProtocolData":
        if self.auth_mode == "username" and not self.username:
            raise ValueError("auth_mode='username' requires non-empty username")
        return self

    @model_validator(mode="after")
    def _validate_operations_unique_names(self) -> "OpcuaProtocolData":
        names = [op.name for op in self.operations]
        if len(names) != len(set(names)):
            duplicates = {n for n in names if names.count(n) > 1}
            raise ValueError(f"duplicate operation name(s): {sorted(duplicates)}")
        return self

    @model_validator(mode="after")
    def _validate_security_phase_a(self) -> "OpcuaProtocolData":
        """Phase A: only ``security_mode=None`` + ``security_policy=None``
        is supported. Non-default values require client-certificate paths
        the schema doesn't carry yet (and asyncua's ``set_security_string``
        format is ``"<policy>,<mode>,<client_cert>,<client_key>"``).
        Phase B will lift this restriction by adding ``client_cert_path``
        + ``client_key_path`` fields and a proper async security helper."""
        if self.security_mode != "None" or self.security_policy != "None":
            raise ValueError(
                "OPC UA security configuration (Sign / SignAndEncrypt + "
                "non-None policy) requires certificate paths — deferred "
                "to Phase B. For Phase A set security_mode='None' and "
                "security_policy='None'."
            )
        return self


# ── plugin ────────────────────────────────────────────────────────────


class OpcuaProtocolPlugin:
    """OPC UA plugin — declarative read operations."""

    id: ClassVar[str] = "opcua"
    display_name: ClassVar[str] = "OPC UA"
    sensitive_fields: ClassVar[tuple[str, ...]] = ("password",)

    def protocol_data_schema(self) -> type[BaseModel]:
        return OpcuaProtocolData

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        out = dict(protocol_data)
        if not include_secrets and out.get("password"):
            out["password"] = "***"
        return out

    async def on_create(self, connection: Connection, *, ctx: PluginContext) -> Connection:
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
        """Probe by reading Server_ServerStatus_State (i=2259).

        Standard OPC UA discovery node — every compliant server exposes it.
        Returns ok=True on any successful read regardless of the state value.

        Defensively decrypts ``password`` via ``ctx.creds`` (the
        :class:`CredentialsBackendRouter`) when the caller hands in an
        encrypted record. The admin ``POST /test`` route already
        pre-decrypts before calling this method, so the defensive
        decrypt is a no-op for that path; callers from outside the admin
        route (autopilot health checks, future CLI) get the same plain-
        text contract for ``_run_op_with_node``. Mirrors the CoAP /
        Modbus / MCP plugins' pattern.
        """
        # isinstance check rather than truthy: tests pass MagicMock() as
        # ctx in cleartext-only scenarios; we only attempt decrypt when
        # ctx.creds is a real CredentialsBackendRouter.
        creds = getattr(ctx, "creds", None) if ctx is not None else None
        if isinstance(creds, CredentialsBackendRouter):
            try:
                sensitive_paths = get_sensitive_field_paths_for(self, connection)
                if sensitive_paths:
                    decrypted_pd = creds.decrypt(
                        connection.protocol_data,
                        sensitive_field_paths=sensitive_paths,
                        connection_credentials_backend=connection.credentials_backend,
                    )
                    connection = replace(connection, protocol_data=decrypted_pd)
            except Exception:
                # Pre-decrypted / malformed inputs pass through; the actual
                # read attempt will surface the right error.
                pass

        try:
            result = await _run_op_with_node(connection, node_id="i=2259")
            return TestResult(
                ok=True,
                message=f"opcua state={result.get('value')} status={result.get('status_code')}",
            )
        except OpcuaError as exc:
            return TestResult(ok=False, message=str(exc))

    def extra_routes(self) -> APIRouter:
        return APIRouter()

    def extra_cli(self) -> list[click.Command]:
        return []


__all__ = [
    "OpcuaAuthError",
    "OpcuaConnectionError",
    "OpcuaError",
    "OpcuaNotInstalledError",
    "OpcuaOperation",
    "OpcuaProtocolData",
    "OpcuaProtocolPlugin",
    "OpcuaReadError",
    "OpcuaSessionError",
    "OpcuaUnknownOperationError",
]
