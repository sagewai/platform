# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""CoAP (Constrained Application Protocol, RFC 7252) plugin.

Phase A PR1 of new protocols rollout. Provides four builtin operations
(GET / POST / PUT / DELETE) over the CoAP request/response model. DTLS
pre-shared-key auth is supported when the connection's ``base_uri`` uses
the ``coaps://`` scheme and credentials populate ``psk_identity`` +
``psk_key``.

The plugin is per-call: each operation opens an aiocoap Context, sends
one request, awaits one response, and tears the context down. No
connection pooling (Phase A scope).
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, ClassVar, Literal, Mapping
from urllib.parse import urlencode

import click
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.connections.credentials import CredentialsBackendRouter
from sagewai.connections.models import Connection, TestResult
from sagewai.connections.protocols.base import PluginContext


# ── errors ────────────────────────────────────────────────────────────


class CoapError(Exception):
    """Base for all CoAP plugin errors."""

    code: ClassVar[str] = "coap_error"


class CoapNotInstalledError(CoapError):
    """The ``aiocoap`` library is not installed.

    Raised at first call to any plugin method that needs the library.
    The message tells operators how to install the optional extra.
    """

    code: ClassVar[str] = "coap_not_installed"

    def __init__(self) -> None:
        super().__init__(
            "aiocoap is not installed. Run `pip install sagewai[coap]` to enable CoAP connections."
        )


class CoapTimeoutError(CoapError):
    """A CoAP request didn't complete within the configured timeout."""

    code: ClassVar[str] = "coap_timeout"


class CoapProtocolError(CoapError):
    """The server returned a non-2.xx response code.

    Carries the CoAP response code (e.g., ``"4.04"``) and the payload
    bytes for diagnostic purposes.
    """

    code: ClassVar[str] = "coap_protocol_error"

    def __init__(self, *, coap_code: str, payload: bytes) -> None:
        self.coap_code = coap_code
        self.payload = payload
        super().__init__(f"coap response {coap_code}: {payload[:80]!r}")


class CoapDtlsError(CoapError):
    """DTLS handshake or PSK negotiation failed."""

    code: ClassVar[str] = "coap_dtls_error"


class CoapConnectionError(CoapError):
    """Failed to open the underlying UDP / DTLS context.

    Raised when ``aiocoap.Context.create_client_context()`` fails for a
    plain ``coap://`` endpoint (DTLS-specific failures raise
    :class:`CoapDtlsError` instead).
    """

    code: ClassVar[str] = "coap_connection_error"


# ── schema ────────────────────────────────────────────────────────────


class CoapProtocolData(BaseModel):
    """Validation schema for ``protocol_data`` of CoAP connections.

    ``use_dtls`` is derivable from the scheme (``coaps://`` ⇒ True) — the
    field exists for explicit documentation in stored records but it must
    stay consistent with the scheme. The model auto-fills it when omitted
    and rejects mismatched combinations (``coaps://`` + ``use_dtls=False``
    or ``coap://`` + ``use_dtls=True``).
    """

    model_config = ConfigDict(extra="forbid")

    base_uri: str = Field(..., pattern=r"^coaps?://")
    use_dtls: bool | None = None
    psk_identity: str = ""
    psk_key: str = ""  # sensitive
    default_timeout_seconds: float = Field(default=10.0, gt=0)
    sandbox_tier_override: Literal["TRUSTED", "SANDBOXED"] | None = None

    @model_validator(mode="after")
    def _check_use_dtls_matches_scheme(self) -> "CoapProtocolData":
        expected = self.base_uri.startswith("coaps://")
        if self.use_dtls is None:
            # Auto-derive when caller omitted the field.
            object.__setattr__(self, "use_dtls", expected)
        elif self.use_dtls != expected:
            raise ValueError(
                f"use_dtls={self.use_dtls!r} is inconsistent with scheme: "
                f"base_uri starts with {'coaps://' if expected else 'coap://'}, "
                f"so use_dtls must be {expected}. Either change the scheme or "
                f"set use_dtls to {expected} (the form auto-derives this)."
            )
        return self


# ── executor helpers ──────────────────────────────────────────────────


_VALID_OPS: tuple[str, ...] = ("get", "post", "put", "delete")


def _import_aiocoap():
    """Lazy-import aiocoap with a clear error message when missing.

    Imports ``aiocoap.numbers.codes`` as a defensive smoke-test of the
    submodule layout — some older aiocoap pre-releases shipped without it
    and we want to fail fast at install/import time rather than mid-request.
    """
    try:
        import aiocoap  # type: ignore[import-not-found]
        import aiocoap.numbers.codes  # noqa: F401  (smoke-test the submodule)
    except ImportError as exc:
        raise CoapNotInstalledError() from exc
    return aiocoap


def _build_uri(base_uri: str, path: str, query: Mapping[str, str] | None) -> str:
    """Concatenate base_uri + path, append URL-encoded query if any.

    Contract: ``path`` should be a path-only string (e.g. ``"/sensors"`` or
    ``"/things/123"``) without a query component. CoAP catalog entries
    pass ``query`` via the dedicated ``args["query"]`` dict — embedding
    ``?`` into ``path`` produces a malformed double-``?`` URI.
    """
    base = base_uri.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    uri = base + path
    if query:
        uri = uri + "?" + urlencode(query)
    return uri


def _content_format_id(name: str | None) -> int | None:
    """Map common content-format strings to CoAP integer ids per RFC 7252."""
    if name is None:
        return None
    mapping = {
        "text/plain": 0,
        "application/link-format": 40,
        "application/xml": 41,
        "application/octet-stream": 42,
        "application/json": 50,
        "application/cbor": 60,
    }
    return mapping.get(name.lower())


def _coerce_payload(payload: Any) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    raise TypeError(f"unsupported payload type: {type(payload).__name__}")


async def _run_op(
    connection: Connection,
    *,
    op: str,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Dispatch a CoAP request and parse the response.

    Raises:
        ValueError: ``op`` is not one of get/post/put/delete.
        CoapNotInstalledError: aiocoap is not installed.
        CoapTimeoutError: the request did not complete within
            ``protocol_data.default_timeout_seconds``.
        CoapProtocolError: the server returned a non-2.xx response.
        CoapDtlsError: DTLS handshake failed.
        CoapConnectionError: plain coap:// context creation failed.
    """
    if op not in _VALID_OPS:
        raise ValueError(f"unknown coap operation: {op!r}")

    aiocoap = _import_aiocoap()

    data = connection.protocol_data
    base_uri = data["base_uri"]
    path = args.get("path", "/")
    query = args.get("query")
    timeout = float(data.get("default_timeout_seconds", 10.0))
    uri = _build_uri(base_uri, path, query)

    method_codes = {
        "get": aiocoap.GET,
        "post": aiocoap.POST,
        "put": aiocoap.PUT,
        "delete": aiocoap.DELETE,
    }
    code = method_codes[op]

    request_kwargs: dict[str, Any] = {"code": code, "uri": uri}
    if op in {"post", "put"}:
        request_kwargs["payload"] = _coerce_payload(args.get("payload"))

    psk_identity = data.get("psk_identity") or ""
    psk_key = data.get("psk_key") or ""

    try:
        context = await aiocoap.Context.create_client_context()
    except OSError as exc:
        if base_uri.startswith("coaps://"):
            raise CoapDtlsError(str(exc)) from exc
        raise CoapConnectionError(str(exc)) from exc

    # Everything after the context is open MUST live inside the try/finally
    # that owns ``context.shutdown()`` — otherwise PSK / message setup
    # failures leak the UDP socket and transport tasks.
    try:
        if base_uri.startswith("coaps://") and psk_identity and psk_key:
            try:
                from aiocoap.credentials import CredentialsMap

                cm = CredentialsMap()
                is_hex = bool(psk_key) and all(
                    c in "0123456789abcdefABCDEF" for c in psk_key
                )
                psk_block: dict[str, Any] = {"ascii": psk_identity}
                if is_hex:
                    psk_block["hex"] = psk_key
                else:
                    psk_block = {"ascii": psk_identity, "key-ascii": psk_key}
                cm.load_from_dict(
                    {
                        uri + "/*": {
                            "dtls": {"psk": psk_block},
                        }
                    }
                )
                context.client_credentials = cm
            except Exception as exc:
                raise CoapDtlsError(f"psk setup failed: {exc}") from exc

        request = aiocoap.Message(**request_kwargs)
        if op in {"post", "put"}:
            cf = _content_format_id(args.get("content_format"))
            if cf is not None:
                request.opt.content_format = cf

        try:
            response = await asyncio.wait_for(
                context.request(request).response, timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            raise CoapTimeoutError(
                f"coap {op} {uri} timed out after {timeout}s"
            ) from exc
    finally:
        await context.shutdown()

    code_str: str = response.code.dotted
    payload: bytes = response.payload or b""
    content_format = getattr(response.opt, "content_format", None)

    if not code_str.startswith("2."):
        raise CoapProtocolError(coap_code=code_str, payload=payload)

    result: dict[str, Any] = {
        "code": code_str,
        "payload": payload,
        "content_format": content_format,
    }
    if op == "post":
        loc = getattr(response.opt, "location_path", None)
        if loc:
            result["location"] = "/" + "/".join(loc)
    if op == "delete":
        result.pop("payload", None)
        result.pop("content_format", None)
    return result


# ── plugin ────────────────────────────────────────────────────────────


class CoapProtocolPlugin:
    """CoAP plugin — builtin GET/POST/PUT/DELETE over RFC 7252."""

    id: ClassVar[str] = "coap"
    display_name: ClassVar[str] = "CoAP"
    sensitive_fields: ClassVar[tuple[str, ...]] = ("psk_key",)

    def protocol_data_schema(self) -> type[BaseModel]:
        return CoapProtocolData

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        out = dict(protocol_data)
        if not include_secrets and "psk_key" in out and out["psk_key"]:
            out["psk_key"] = "***"
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
        """Discovery probe per RFC 7252 §7.2 — GET /.well-known/core.

        Mirrors the MCP plugin's pattern: defensively decrypt via
        ``ctx.creds`` (the :class:`CredentialsBackendRouter`) if the
        record's sensitive fields look encrypted. The admin ``POST /test``
        route already pre-decrypts before calling this method, so the
        defensive decrypt is a no-op for that path; callers that hand in
        an encrypted record (executor + future CLI) get the same
        plaintext-contract for ``_run_op``.
        """
        # isinstance check rather than truthy: tests pass MagicMock() as
        # ctx; we only want to attempt decrypt when ctx.creds is a real
        # CredentialsBackendRouter.
        creds = getattr(ctx, "creds", None) if ctx is not None else None
        if isinstance(creds, CredentialsBackendRouter):
            try:
                decrypted_pd = creds.decrypt(
                    connection.protocol_data,
                    sensitive_field_paths=self.sensitive_fields,
                    connection_credentials_backend=connection.credentials_backend,
                )
                connection = replace(connection, protocol_data=decrypted_pd)
            except Exception:
                # Pre-decrypted / malformed inputs pass through.
                pass

        try:
            result = await _run_op(
                connection, op="get", args={"path": "/.well-known/core"}
            )
        except CoapError as exc:
            return TestResult(ok=False, message=str(exc))
        except OSError as exc:
            return TestResult(ok=False, message=str(exc))
        return TestResult(
            ok=True,
            message=f"coap discovery returned {result['code']}",
        )

    def extra_routes(self) -> APIRouter:
        return APIRouter()

    def extra_cli(self) -> list[click.Command]:
        return []


__all__ = [
    "CoapConnectionError",
    "CoapDtlsError",
    "CoapError",
    "CoapNotInstalledError",
    "CoapProtocolData",
    "CoapProtocolError",
    "CoapProtocolPlugin",
    "CoapTimeoutError",
]
