# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""WebSocket (RFC 6455) plugin — Phase A one-shot send-and-receive only.

Operators declare ``operations[]`` in ``protocol_data`` with
``{name, message_template, response_match?, timeout_seconds?}`` per op.
Each invocation opens a handshake to ``url``, sends the rendered
message, awaits one response frame within the configured timeout,
optionally extracts via JSONPath, closes. No persistent dialogues,
no streaming — those are Phase B.

Auth: HTTP-handshake header. ``auth_header_value`` is sensitive and
encrypted via the connection's credentials backend; the executor
decrypts before adding it to the handshake headers.

Phase A constraints (enforced at schema time):

- No persistent connections (one frame request, one frame response).
- No subprotocol negotiation as a first-class field (operators may
  set ``Sec-WebSocket-Protocol`` via the generic ``headers`` map).
- No binary frame templating (``message_template`` is text-only;
  binary response frames are decoded best-effort as UTF-8).
"""
from __future__ import annotations

import asyncio
import re
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


class WebsocketError(Exception):
    """Base for all WebSocket plugin errors."""

    code: ClassVar[str] = "websocket_error"


class WebsocketNotInstalledError(WebsocketError):
    """The ``websockets`` library is not installed."""

    code: ClassVar[str] = "websocket_not_installed"

    def __init__(self) -> None:
        super().__init__(
            "websockets is not installed. "
            "Run `pip install sagewai[websocket]` to enable WebSocket connections."
        )


class WebsocketConnectionError(WebsocketError):
    """Transport-level failure (DNS / TCP / TLS) or connection closed mid-op."""

    code: ClassVar[str] = "websocket_connection_error"


class WebsocketHandshakeError(WebsocketError):
    """The WebSocket upgrade handshake failed (101 not received)."""

    code: ClassVar[str] = "websocket_handshake_error"


class WebsocketAuthError(WebsocketError):
    """The handshake returned 401 or 403."""

    code: ClassVar[str] = "websocket_auth_error"


class WebsocketTimeoutError(WebsocketError):
    """The response frame didn't arrive within the configured timeout."""

    code: ClassVar[str] = "websocket_timeout"


class WebsocketTemplateError(WebsocketError):
    """``message_template`` references placeholder keys not supplied by the caller."""

    code: ClassVar[str] = "websocket_template_error"

    def __init__(self, *, missing_keys: list[str]) -> None:
        self.missing_keys = missing_keys
        super().__init__(f"missing template keys: {sorted(missing_keys)}")


class WebsocketResponseError(WebsocketError):
    """Response frame didn't match ``response_match`` or wasn't valid JSON."""

    code: ClassVar[str] = "websocket_response_error"

    def __init__(self, message: str, *, frame: str | bytes | None = None) -> None:
        self.frame = frame
        super().__init__(message)


class WebsocketUnknownOperationError(WebsocketError):
    """The named operation isn't declared in the connection's operations[]."""

    code: ClassVar[str] = "websocket_unknown_operation"

    def __init__(self, *, name: str) -> None:
        self.name = name
        super().__init__(f"unknown websocket operation: {name!r}")


# ── schemas ───────────────────────────────────────────────────────────


class WebsocketOperation(BaseModel):
    """One declared send-and-receive operation on a WebSocket connection."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    message_template: str = Field(..., min_length=1)
    response_match: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)


class WebsocketProtocolData(BaseModel):
    """Validation schema for ``protocol_data`` of WebSocket connections."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., pattern=r"^wss?://")
    headers: dict[str, str] = Field(default_factory=dict)
    auth_header_name: str = "Authorization"
    auth_header_value: str = ""  # sensitive
    default_timeout_seconds: float = Field(default=30.0, gt=0)
    operations: list[WebsocketOperation] = Field(default_factory=list)
    sandbox_tier_override: Literal["TRUSTED", "SANDBOXED"] | None = None

    @model_validator(mode="after")
    def _validate_operations_unique_names(self) -> "WebsocketProtocolData":
        names = [op.name for op in self.operations]
        if len(names) != len(set(names)):
            duplicates = {n for n in names if names.count(n) > 1}
            raise ValueError(f"duplicate operation name(s): {sorted(duplicates)}")
        return self


# ── executor ──────────────────────────────────────────────────────────


def _import_websockets():
    """Lazy-import websockets with a clear error message when missing."""
    try:
        import websockets  # type: ignore[import-not-found]
        import websockets.exceptions  # noqa: F401
        from websockets.asyncio.client import connect  # noqa: F401
    except ImportError as exc:
        raise WebsocketNotInstalledError() from exc
    return websockets


def _import_jsonpath():
    """Lazy-import jsonpath_ng. Required only when response_match is set."""
    try:
        from jsonpath_ng import parse  # type: ignore[import-not-found]
    except ImportError as exc:
        raise WebsocketResponseError(
            "jsonpath-ng is required for response_match. "
            "Run `pip install sagewai[websocket]`."
        ) from exc
    return parse


# ``message_template`` placeholders are simple ``{identifier}`` tokens that
# substitute kwargs in order. We use a regex-driven substitution rather than
# Python's ``str.format_map`` because the latter chokes on format specifiers
# inside the surrounding text (e.g. literal JSON ``"key": "value"`` contains
# a colon that ``format_map`` would treat as the start of a format spec).
# Identifiers follow the usual Python rule: ASCII letter or underscore,
# followed by letters/digits/underscores.
_TEMPLATE_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _render_template(template: str, kwargs: dict[str, Any]) -> str:
    missing: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in kwargs:
            return str(kwargs[name])
        if name not in missing:
            missing.append(name)
        return match.group(0)  # leave the placeholder visible

    rendered = _TEMPLATE_PLACEHOLDER.sub(_sub, template)
    if missing:
        raise WebsocketTemplateError(missing_keys=missing)
    return rendered


def _lookup_op(operations: list[dict], op_name: str) -> dict:
    for op in operations:
        if op.get("name") == op_name:
            return op
    raise WebsocketUnknownOperationError(name=op_name)


def _build_headers(protocol_data: dict) -> dict[str, str]:
    headers = dict(protocol_data.get("headers") or {})
    auth_value = protocol_data.get("auth_header_value") or ""
    if auth_value:
        auth_name = protocol_data.get("auth_header_name") or "Authorization"
        headers[auth_name] = auth_value
    return headers


def _extract_jsonpath(response_match: str, frame: str | bytes) -> Any:
    import json

    parse = _import_jsonpath()

    if isinstance(frame, bytes):
        try:
            frame = frame.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WebsocketResponseError("non-JSON frame (binary)", frame=frame) from exc

    try:
        body = json.loads(frame)
    except json.JSONDecodeError as exc:
        raise WebsocketResponseError("non-JSON frame", frame=frame) from exc

    expr = parse(response_match)
    matches = [m.value for m in expr.find(body)]
    if not matches:
        raise WebsocketResponseError(
            f"response_match {response_match!r} did not match", frame=frame
        )
    return matches[0] if len(matches) == 1 else matches


def _get_ws_exception_classes() -> dict[str, tuple[type, ...]]:
    """Look up websockets-library exception classes by category.

    The websockets library has reshuffled and deprecated several
    exception class names across versions (notably ``InvalidStatusCode``
    → ``InvalidStatus`` in 14.0, ``AbortHandshake`` deprecated in 14.0).
    We probe via ``getattr`` so the normalizer keeps working on both
    legacy and modern releases, and we swallow ``DeprecationWarning``
    when probing names we know are deprecated-but-still-importable.
    """
    import warnings

    try:
        from websockets import exceptions as _wse
    except ImportError:
        return {"status": (), "handshake": (), "closed": ()}

    def _probe(*names: str) -> tuple[type, ...]:
        out: list[type] = []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            for name in names:
                cls = getattr(_wse, name, None)
                if cls is not None:
                    out.append(cls)
        return tuple(out)

    return {
        "status": _probe("InvalidStatus", "InvalidStatusCode"),
        "handshake": _probe("InvalidHandshake", "AbortHandshake", "InvalidHeader", "InvalidUpgrade"),
        "closed": _probe("ConnectionClosed", "ConnectionClosedError", "ConnectionClosedOK"),
    }


def _normalize_websockets_exception(exc: Exception) -> None:
    """Map raw websockets-library exceptions to our hierarchy. Always raises."""
    classes = _get_ws_exception_classes()

    # Auth-class (handshake returned 401/403).
    if classes["status"] and isinstance(exc, classes["status"]):
        status = getattr(exc, "status_code", None)
        if status is None:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
        if status in (401, 403):
            raise WebsocketAuthError(str(exc)) from exc
        raise WebsocketHandshakeError(str(exc)) from exc

    # Handshake-class (non-status invalid handshake).
    if classes["handshake"] and isinstance(exc, classes["handshake"]):
        raise WebsocketHandshakeError(str(exc)) from exc

    # Connection-closed mid-operation.
    if classes["closed"] and isinstance(exc, classes["closed"]):
        raise WebsocketConnectionError(str(exc)) from exc

    # Transport-level (DNS, TCP, TLS).
    if isinstance(exc, OSError):
        raise WebsocketConnectionError(str(exc)) from exc

    # Anything else: re-raise unchanged so the caller sees the truth.
    raise exc


def _open_handshake(connection: Connection):
    """Open the WebSocket handshake. Returns an async context manager.

    The ``websockets.asyncio.client.connect`` callable is a class whose
    instances are async context managers; this helper is intentionally
    a plain ``def`` so callers can do ``async with _open_handshake(c)``.
    """
    _import_websockets()  # raises WebsocketNotInstalledError if missing
    from websockets.asyncio.client import connect

    data = connection.protocol_data
    url = data["url"]
    headers = _build_headers(data)

    return connect(url, additional_headers=headers)


async def _run_op(
    connection: Connection,
    *,
    op: str,
    args: dict[str, Any],
) -> Any:
    """Send one rendered message, await one response frame, optionally extract via JSONPath."""
    operations = connection.protocol_data.get("operations", []) or []
    op_spec = _lookup_op(operations, op)

    template = op_spec["message_template"]
    rendered = _render_template(template, dict(args))

    timeout = op_spec.get("timeout_seconds") or float(
        connection.protocol_data.get("default_timeout_seconds", 30.0)
    )

    response_match = op_spec.get("response_match")

    try:
        cm = _open_handshake(connection)
        async with cm as ws:
            await ws.send(rendered)
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise WebsocketTimeoutError(
                    f"websocket {op} timed out after {timeout}s"
                ) from exc
    except WebsocketError:
        raise
    except Exception as exc:
        _normalize_websockets_exception(exc)
        raise  # pragma: no cover — _normalize_websockets_exception always raises

    if response_match:
        return _extract_jsonpath(response_match, frame)
    if isinstance(frame, bytes):
        return {"frame": frame.decode("utf-8", errors="replace")}
    return {"frame": frame}


# ── plugin ────────────────────────────────────────────────────────────


class WebsocketProtocolPlugin:
    """WebSocket plugin — declarative one-shot send-and-receive."""

    id: ClassVar[str] = "websocket"
    display_name: ClassVar[str] = "WebSocket"
    sensitive_fields: ClassVar[tuple[str, ...]] = ("auth_header_value",)

    def protocol_data_schema(self) -> type[BaseModel]:
        return WebsocketProtocolData

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        out = dict(protocol_data)
        if not include_secrets and out.get("auth_header_value"):
            out["auth_header_value"] = "***"
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
        """Open the WebSocket handshake and close immediately.

        Defensively decrypts ``auth_header_value`` via ``ctx.creds`` when
        ``ctx.creds`` is a real :class:`CredentialsBackendRouter`. The admin
        ``POST /test`` route already pre-decrypts before calling this method,
        so the defensive decrypt is a no-op for that path; callers from
        outside the admin route (autopilot health checks, future CLI) get
        the same plaintext contract for the handshake. Mirrors the CoAP /
        Modbus / OPC UA plugins' pattern.
        """
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
                # handshake will surface the right error.
                pass

        try:
            cm = _open_handshake(connection)
            async with cm:
                pass  # immediately close
            return TestResult(ok=True, message="websocket handshake complete")
        except WebsocketError as exc:
            return TestResult(ok=False, message=str(exc))
        except Exception as exc:
            try:
                _normalize_websockets_exception(exc)
            except WebsocketError as nexc:
                return TestResult(ok=False, message=str(nexc))
            return TestResult(ok=False, message=str(exc))

    def extra_routes(self) -> APIRouter:
        return APIRouter()

    def extra_cli(self) -> list[click.Command]:
        return []


__all__ = [
    "WebsocketAuthError",
    "WebsocketConnectionError",
    "WebsocketError",
    "WebsocketHandshakeError",
    "WebsocketNotInstalledError",
    "WebsocketOperation",
    "WebsocketProtocolData",
    "WebsocketProtocolPlugin",
    "WebsocketResponseError",
    "WebsocketTemplateError",
    "WebsocketTimeoutError",
    "WebsocketUnknownOperationError",
    "_run_op",
]
