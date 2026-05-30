# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""gRPC unary plugin (reflection-based generic client).

Calls arbitrary reflection-discovered unary methods: JSON request →
(via server reflection + dynamic protobuf) → unary RPC → JSON response.
Implements ``ProtocolPlugin`` only (no streaming — server-streaming is a
separate spec on the ``SubscriptionPlugin`` foundation).

The "generic gRPC client" pattern: query the server's Server Reflection
service for the target method's service descriptors → build a
``google.protobuf.descriptor_pool.DescriptorPool`` → resolve the method's
input/output message classes → marshal the agent's JSON request to a
dynamic protobuf message via ``json_format.ParseDict`` → invoke
``channel.unary_unary(...)`` → unmarshal the response back to JSON. The
agent never sees protobuf; the plugin does descriptor-driven marshalling.

Schema is supplied entirely by **server reflection** — no operator
``.proto`` upload. Servers with reflection DISABLED are a first-class
error (``GrpcMethodError``), not a crash — that is the #1 real-world
gotcha.

Per-target descriptor-pool cache (process-lifetime, LRU-bounded), mirroring
the MCP capability-cache canon (cache on first use, no background refresh,
no TTL).

Auth: server-side TLS channel credentials + a per-call metadata token.
mTLS client certificates + gRPC call-credentials plugins are deferred.

``grpcio`` + ``grpcio-reflection`` live behind the ``sagewai[grpc]`` extra
and are lazy-imported with a clear ``GrpcNotInstalledError``.
"""
from __future__ import annotations

import asyncio
import collections
import threading
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
from sagewai.connections.subscriptions.errors import (
    SubscriptionLimitExceededError,
    SubscriptionNotFoundError,
)
from sagewai.connections.subscriptions.manager import get_subscription_manager


# ── errors ────────────────────────────────────────────────────────────


class GrpcError(Exception):
    """Base for all gRPC plugin errors."""

    code: ClassVar[str] = "grpc_error"


class GrpcNotInstalledError(GrpcError):
    """The ``grpcio``/``grpcio-reflection`` libraries are not installed."""

    code: ClassVar[str] = "grpc_not_installed"

    def __init__(self) -> None:
        super().__init__(
            "grpcio-reflection is not installed. "
            "Run `pip install sagewai[grpc]` to enable gRPC connections."
        )


class GrpcConnectionError(GrpcError):
    """Transport-level failure (``UNAVAILABLE``, DNS/TCP/TLS, channel-not-ready)."""

    code: ClassVar[str] = "grpc_connection_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: str | None = None,
        details: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.details = details
        super().__init__(message)


class GrpcAuthError(GrpcError):
    """``UNAUTHENTICATED`` / ``PERMISSION_DENIED``."""

    code: ClassVar[str] = "grpc_auth_error"


class GrpcMethodError(GrpcError):
    """``UNIMPLEMENTED`` / ``NOT_FOUND``, method not in reflection, or
    reflection disabled."""

    code: ClassVar[str] = "grpc_method_error"


class GrpcMarshalError(GrpcError):
    """JSON→protobuf request build or response decode failure."""

    code: ClassVar[str] = "grpc_marshal_error"


class GrpcDeadlineError(GrpcError):
    """``DEADLINE_EXCEEDED``."""

    code: ClassVar[str] = "grpc_deadline_exceeded"


class GrpcCallError(GrpcError):
    """Any other non-OK gRPC status (carries the status name + server details)."""

    code: ClassVar[str] = "grpc_call_error"

    def __init__(self, *, status_code: str, details: str) -> None:
        self.status_code = status_code
        self.details = details
        super().__init__(f"grpc call failed: {status_code}: {details}")


# ── schema ────────────────────────────────────────────────────────────


class GrpcProtocolData(BaseModel):
    """Validation schema for ``protocol_data`` of gRPC connections."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(..., min_length=1)  # "host:port"
    tls: Literal["insecure", "tls", "tls_ca"] = "tls"
    tls_ca_cert: str = ""  # PEM, required when tls == "tls_ca"
    auth_mode: Literal["none", "metadata_token"] = "none"
    auth_metadata_key: str = "authorization"
    auth_token: str = ""  # sensitive
    auth_token_prefix: str = "Bearer "
    default_timeout_seconds: float = Field(default=30.0, gt=0)
    sandbox_tier_override: Literal["TRUSTED", "SANDBOXED"] | None = None

    @model_validator(mode="after")
    def _validate(self) -> "GrpcProtocolData":
        if self.tls == "tls_ca" and not self.tls_ca_cert:
            raise ValueError("tls='tls_ca' requires a non-empty tls_ca_cert (PEM)")
        if self.auth_mode == "metadata_token" and not self.auth_metadata_key:
            raise ValueError(
                "auth_mode='metadata_token' requires a non-empty auth_metadata_key"
            )
        return self


class GrpcStreamSpec(BaseModel):
    """Per-subscription spec for a gRPC **server-streaming** method.

    Validated by the ``SubscriptionManager`` before opening the stream
    (mirrors ``MqttSubscriptionSpec``). ``method`` is the server-streaming
    RPC (``package.Service/StreamMethod``); ``request`` is the single request
    message (JSON, marshalled to protobuf via #376's ``_marshal_request``);
    each response message is emitted as JSON into the manager-owned buffer.

    ``drop_oldest`` only — ``pause`` is schema-rejected/deferred (it needs a
    ``SubscriptionPlugin`` resume-signal extension, shared with MQTT).
    """

    model_config = ConfigDict(extra="forbid")

    method: str = Field(..., min_length=1)  # "package.Service/StreamMethod"
    request: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)
    overflow_policy: Literal["drop_oldest"] = "drop_oldest"  # pause deferred


# ── lazy import ───────────────────────────────────────────────────────


def _import_grpc():
    """Lazy-import grpcio + grpcio-reflection with a clear error when missing."""
    try:
        import grpc  # noqa: F401
        from grpc_reflection.v1alpha import (  # noqa: F401
            reflection_pb2,
            reflection_pb2_grpc,
        )
    except ImportError as exc:
        raise GrpcNotInstalledError() from exc
    import grpc

    return grpc


# ── descriptor-pool cache ─────────────────────────────────────────────


class _DescriptorPoolCache:
    """Per-target ``DescriptorPool`` cache, LRU-bounded.

    Process-lifetime (cleared on restart); schema change → restart to
    refresh. Mirrors the MCP capability-cache canon: cache on first use,
    no background refresh, no TTL. Keyed by ``target`` only (one pool per
    server, all its services). The ``max_targets`` cap evicts the
    least-recently-used pool so target sprawl can't grow memory unbounded.
    """

    def __init__(self, *, max_targets: int = 64) -> None:
        self._max = max_targets
        self._pools: "collections.OrderedDict[str, Any]" = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, target: str):
        with self._lock:
            pool = self._pools.get(target)
            if pool is not None:
                self._pools.move_to_end(target)
            return pool

    def build_pool_from_fdps(self, target: str, fdp_bytes_list: list[bytes]):
        """Build a fresh ``DescriptorPool`` from a list of serialized
        ``FileDescriptorProto`` bytes and cache it under ``target``.

        Dependencies must be added before dependents. Reflection usually
        returns them in dependency order, but we re-pass the list until no
        further progress to tolerate out-of-order returns.
        """
        from google.protobuf import descriptor_pb2, descriptor_pool

        pool = descriptor_pool.DescriptorPool()
        pending = [
            descriptor_pb2.FileDescriptorProto.FromString(b) for b in fdp_bytes_list
        ]
        # De-dup by file name — reflection can return the same dep twice when
        # multiple requested symbols share a transitive dependency.
        seen_names: set[str] = set()
        deduped: list = []
        for fdp in pending:
            if fdp.name not in seen_names:
                seen_names.add(fdp.name)
                deduped.append(fdp)
        pending = deduped

        added: set[str] = set()
        progress = True
        while pending and progress:
            progress = False
            still: list = []
            for fdp in pending:
                if all(dep in added for dep in fdp.dependency):
                    pool.Add(fdp)
                    added.add(fdp.name)
                    progress = True
                else:
                    still.append(fdp)
            pending = still
        for fdp in pending:  # leftover (missing external dep) — add best-effort
            # Narrow to TypeError/KeyError (the protobuf descriptor-pool
            # "missing dependency" failure shapes). A genuinely different
            # error should not be silently swallowed; the downstream
            # FindServiceByName surfaces an unresolved file as GrpcMethodError.
            try:
                pool.Add(fdp)
            except (TypeError, KeyError):  # pragma: no cover — missing external dep
                pass

        with self._lock:
            self._pools[target] = pool
            self._pools.move_to_end(target)
            while len(self._pools) > self._max:
                self._pools.popitem(last=False)  # evict LRU
        return pool


_POOL_CACHE = _DescriptorPoolCache()


# ── JSON ↔ protobuf marshalling ───────────────────────────────────────


def _marshal_request(request_json: dict, request_cls):
    """JSON dict → dynamic protobuf request message.

    Unknown / typo fields raise ``GrpcMarshalError`` with the precise field
    name (``json_format.ParseDict`` does not silently drop them).
    """
    from google.protobuf import json_format

    try:
        return json_format.ParseDict(request_json, request_cls())
    except Exception as exc:
        raise GrpcMarshalError(f"failed to build request message: {exc}") from exc


def _unmarshal_response(response_msg) -> dict:
    """Protobuf response message → JSON dict.

    Proto field names preserved; enums as string names; well-known types
    (Timestamp/Duration/Struct) via protobuf's canonical proto3 JSON
    mapping; bytes fields as base64 strings.
    """
    from google.protobuf import json_format

    try:
        return json_format.MessageToDict(
            response_msg,
            preserving_proto_field_name=True,
            use_integers_for_enums=False,
        )
    except Exception as exc:
        raise GrpcMarshalError(f"failed to decode response: {exc}") from exc


# ── method-path helpers ───────────────────────────────────────────────


def _split_method(method: str) -> tuple[str, str]:
    """``'pkg.Svc/Method'`` (or ``'/pkg.Svc/Method'``) → ``('pkg.Svc', 'Method')``."""
    m = method[1:] if method.startswith("/") else method
    if "/" not in m:
        raise GrpcMethodError(
            f"method must be 'package.Service/Method', got {method!r}"
        )
    svc, meth = m.rsplit("/", 1)
    return svc, meth


def _normalize_method_path(method: str) -> str:
    """Normalize to the gRPC wire path ``/package.Service/Method``."""
    svc, meth = _split_method(method)
    return f"/{svc}/{meth}"


# ── channel + metadata ────────────────────────────────────────────────


def _open_channel(pd: dict):
    """Build a grpc.aio channel from ``protocol_data``."""
    grpc = _import_grpc()
    target = pd["target"]
    tls = pd.get("tls", "tls")
    if tls == "insecure":
        return grpc.aio.insecure_channel(target)
    if tls == "tls_ca":
        creds = grpc.ssl_channel_credentials(
            root_certificates=pd["tls_ca_cert"].encode("utf-8")
        )
    else:  # "tls" — system trust store
        creds = grpc.ssl_channel_credentials()
    return grpc.aio.secure_channel(target, creds)


async def _aclose(channel) -> None:
    """Close a grpc.aio channel, tolerating mocks whose ``close()`` returns a
    non-awaitable (the unit tests patch ``_open_channel`` with a ``MagicMock``)."""
    result = channel.close()
    if hasattr(result, "__await__"):
        await result


def _call_metadata(pd: dict, extra: dict | None) -> list[tuple[str, str]]:
    """Build the per-call metadata list (auth token + caller-supplied extra).

    gRPC metadata keys must be lowercase.
    """
    md: list[tuple[str, str]] = []
    if pd.get("auth_mode") == "metadata_token" and pd.get("auth_token"):
        key = pd.get("auth_metadata_key", "authorization")
        prefix = pd.get("auth_token_prefix", "")
        md.append((key.lower(), prefix + pd["auth_token"]))
    for k, v in (extra or {}).items():
        md.append((k.lower(), str(v)))
    return md


# ── status-code normalization (library-exception-normalization canon) ──


def _raise_normalized(exc, *, context: str):
    """Map ``grpc.aio.AioRpcError`` → the ``GrpcError`` hierarchy, preserving
    the gRPC status code. Always raises."""
    grpc = _import_grpc()
    code = exc.code()
    name = code.name if code is not None else "UNKNOWN"
    details = exc.details() or ""
    SC = grpc.StatusCode
    if code == SC.UNAVAILABLE:
        raise GrpcConnectionError(
            f"{context}: {name}: {details}", status_code=name, details=details
        ) from exc
    if code in (SC.UNAUTHENTICATED, SC.PERMISSION_DENIED):
        raise GrpcAuthError(f"{context}: {name}: {details}") from exc
    if code in (SC.UNIMPLEMENTED, SC.NOT_FOUND):
        raise GrpcMethodError(f"{context}: {name}: {details}") from exc
    if code == SC.DEADLINE_EXCEEDED:
        raise GrpcDeadlineError(f"{context}: {name}: {details}") from exc
    raise GrpcCallError(status_code=name, details=details) from exc


# ── server reflection ─────────────────────────────────────────────────


async def _fetch_descriptors_for_symbol(channel, symbol: str) -> list[bytes]:
    """Drive the bidi reflection stream to fetch the ``FileDescriptorProto``s
    for ``symbol`` (a fully-qualified service name) plus its transitive deps.

    Returns the serialized ``FileDescriptorProto`` bytes. Raises
    ``GrpcMethodError`` on a reflection ``error_response`` (the usual
    reflection-disabled / unknown-service case).
    """
    grpc = _import_grpc()
    from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc

    stub = reflection_pb2_grpc.ServerReflectionStub(channel)

    async def _requests():
        yield reflection_pb2.ServerReflectionRequest(file_containing_symbol=symbol)

    fdps: list[bytes] = []
    try:
        async for resp in stub.ServerReflectionInfo(_requests()):
            which = resp.WhichOneof("message_response")
            if which == "file_descriptor_response":
                fdps.extend(resp.file_descriptor_response.file_descriptor_proto)
            elif which == "error_response":
                raise GrpcMethodError(
                    f"reflection error for {symbol!r}: "
                    f"{resp.error_response.error_message} "
                    "(is server reflection enabled, and does the service exist?)"
                )
            break  # one symbol → one response
    except grpc.aio.AioRpcError as exc:
        _raise_normalized(exc, context=f"reflection for {symbol!r}")
    if not fdps:
        raise GrpcMethodError(
            f"no descriptors returned for {symbol!r} "
            "(server reflection is not enabled; reflection-based gRPC requires it)"
        )
    return fdps


async def _list_services(channel) -> list[str]:
    """List the fully-qualified service names the server exposes via reflection."""
    grpc = _import_grpc()
    from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc

    stub = reflection_pb2_grpc.ServerReflectionStub(channel)

    async def _requests():
        yield reflection_pb2.ServerReflectionRequest(list_services="")

    out: list[str] = []
    try:
        async for resp in stub.ServerReflectionInfo(_requests()):
            which = resp.WhichOneof("message_response")
            if which == "list_services_response":
                out = [s.name for s in resp.list_services_response.service]
            elif which == "error_response":
                raise GrpcMethodError(
                    f"reflection list_services failed: "
                    f"{resp.error_response.error_message} "
                    "(is server reflection enabled?)"
                )
            break
    except grpc.aio.AioRpcError as exc:
        _raise_normalized(exc, context="reflection list_services")
    return out


# ── the unary call ────────────────────────────────────────────────────


async def _do_unary_call(
    channel, method_path, request_msg, request_cls, response_cls, metadata, timeout
):
    """Invoke one unary RPC and return the protobuf response message."""
    grpc = _import_grpc()
    callable_ = channel.unary_unary(
        method_path,
        request_serializer=request_cls.SerializeToString,
        response_deserializer=response_cls.FromString,
    )
    try:
        return await callable_(request_msg, metadata=metadata, timeout=timeout)
    except grpc.aio.AioRpcError as exc:
        _raise_normalized(exc, context=f"call {method_path}")


# ── the `call` runner ─────────────────────────────────────────────────


async def _run_op(connection: Connection, *, op: str, args: dict) -> Any:
    """Execute a gRPC operation. The only op is ``call``.

    The executor decrypts ``auth_token`` before this runner is invoked —
    the runner consumes plaintext ``protocol_data`` only.
    """
    from google.protobuf import message_factory

    if op != "call":
        raise ValueError(f"unknown grpc operation: {op!r}")

    pd = connection.protocol_data
    method = args["method"]
    svc, meth = _split_method(method)
    method_path = _normalize_method_path(method)
    timeout = float(args.get("timeout_seconds") or pd.get("default_timeout_seconds", 30.0))
    metadata = _call_metadata(pd, args.get("metadata"))

    channel = _open_channel(pd)
    try:
        pool = _POOL_CACHE.get(pd["target"])
        if pool is None:
            fdps = await _fetch_descriptors_for_symbol(channel, svc)
            pool = _POOL_CACHE.build_pool_from_fdps(pd["target"], fdps)
        try:
            method_desc = pool.FindServiceByName(svc).FindMethodByName(meth)
        except GrpcError:
            raise
        except Exception as exc:
            raise GrpcMethodError(
                f"method {method!r} not found in server reflection"
            ) from exc
        request_cls = message_factory.GetMessageClass(method_desc.input_type)
        response_cls = message_factory.GetMessageClass(method_desc.output_type)
        request_msg = _marshal_request(args.get("request") or {}, request_cls)
        response_msg = await _do_unary_call(
            channel, method_path, request_msg, request_cls, response_cls, metadata, timeout
        )
        return _unmarshal_response(response_msg)
    finally:
        await _aclose(channel)


# ── defensive decrypt (for test() called outside the admin route) ─────


def _maybe_decrypt(plugin, connection: Connection, ctx) -> Connection:
    """Decrypt the connection's sensitive fields when ``ctx.creds`` is a real
    router. No-op for pre-decrypted / malformed inputs."""
    creds = getattr(ctx, "creds", None) if ctx is not None else None
    if not isinstance(creds, CredentialsBackendRouter):
        return connection
    try:
        sensitive_paths = get_sensitive_field_paths_for(plugin, connection)
        if sensitive_paths:
            decrypted_pd = creds.decrypt(
                connection.protocol_data,
                sensitive_field_paths=sensitive_paths,
                connection_credentials_backend=connection.credentials_backend,
            )
            connection = replace(connection, protocol_data=decrypted_pd)
    except Exception:
        # Pre-decrypted / malformed inputs pass through; the actual reflection
        # probe will surface the right error.
        pass
    return connection


# ── plugin ────────────────────────────────────────────────────────────


class GrpcProtocolPlugin:
    """gRPC unary plugin — reflection-based generic client (request/response)."""

    id: ClassVar[str] = "grpc"
    display_name: ClassVar[str] = "gRPC"
    sensitive_fields: ClassVar[tuple[str, ...]] = ("auth_token",)

    def protocol_data_schema(self) -> type[BaseModel]:
        return GrpcProtocolData

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        out = dict(protocol_data)
        if not include_secrets and out.get("auth_token"):
            out["auth_token"] = "***"
        return out

    async def on_create(self, connection: Connection, *, ctx: PluginContext) -> Connection:
        return connection

    async def on_update(
        self, before: Connection, after: Connection, *, ctx: PluginContext
    ) -> Connection:
        return after

    async def on_delete(self, connection: Connection, *, ctx: PluginContext) -> None:
        return None

    async def test(self, connection: Connection, *, ctx: PluginContext) -> TestResult:
        """Open the channel and probe reflection by listing the server's
        services. Proves channel + TLS + reflection without invoking a real
        method.

        Defensively decrypts ``auth_token`` via ``ctx.creds`` when it is a
        real :class:`CredentialsBackendRouter` (the admin ``POST /test`` route
        pre-decrypts, so this is a no-op there; other callers get the same
        plaintext contract). Mirrors the CoAP / Modbus / OPC UA / WebSocket
        plugins' pattern.
        """
        connection = _maybe_decrypt(self, connection, ctx)
        channel = _open_channel(connection.protocol_data)
        try:
            services = await _list_services(channel)
            return TestResult(
                ok=True, message=f"grpc reflection ok: {len(services)} services"
            )
        except GrpcError as exc:
            return TestResult(ok=False, message=str(exc))
        except Exception as exc:
            return TestResult(ok=False, message=str(exc))
        finally:
            await _aclose(channel)

    def extra_routes(self) -> APIRouter:
        return _grpc_router  # server-streaming subscription-management routes

    def extra_cli(self) -> list[click.Command]:
        return []

    # ── SubscriptionPlugin half (server-streaming) ────────────────────
    #
    # gRPC server-streaming (1 request → N responses) fits the
    # ``SubscriptionPlugin`` foundation (#374/#375): subscribe with a request
    # → buffer of response items → drain. Reuses #376's reflection +
    # marshalling wholesale; the ONLY new gRPC verb is ``channel.unary_stream``
    # (vs ``unary_unary`` for the unary ``call`` op). The unary ``call`` op is
    # 100% unchanged. Dual-Protocol shape mirrors ``MqttProtocolPlugin``.
    # Client-streaming + bidirectional remain out of scope (need a new "agent
    # pushes a request stream" abstraction).

    def subscription_spec_schema(self) -> type[BaseModel]:
        return GrpcStreamSpec

    async def open_subscription(
        self,
        connection: Connection,
        *,
        spec: dict[str, Any],
        emit,
        ctx: Any,
    ) -> None:
        """Open a server-streaming RPC and emit each response message as JSON.

        Runs as the manager's background task: it iterates the
        ``unary_stream`` callable until the server closes the stream or the
        manager cancels the task. Reflection + marshalling are #376's helpers
        (cached descriptor pool, dynamic request/response message classes).

        Defensively decrypts ``auth_token`` via ``ctx.creds`` (the MQTT
        pattern — non-admin callers may pass an encrypted record). Re-raises
        ``asyncio.CancelledError`` so the manager can cancel cleanly, normalizes
        grpc errors onto the ``GrpcError`` hierarchy via ``_raise_normalized``,
        and closes the channel in ``finally`` via ``_aclose``.
        """
        from google.protobuf import message_factory

        connection = _maybe_decrypt(self, connection, ctx)
        pd = connection.protocol_data
        method = spec["method"]
        svc, meth = _split_method(method)
        method_path = _normalize_method_path(method)
        metadata = _call_metadata(pd, spec.get("metadata"))

        channel = _open_channel(pd)
        try:
            pool = _POOL_CACHE.get(pd["target"])
            if pool is None:
                fdps = await _fetch_descriptors_for_symbol(channel, svc)
                pool = _POOL_CACHE.build_pool_from_fdps(pd["target"], fdps)
            try:
                method_desc = pool.FindServiceByName(svc).FindMethodByName(meth)
            except GrpcError:
                raise
            except Exception as exc:
                raise GrpcMethodError(
                    f"method {method!r} not found in server reflection"
                ) from exc
            request_cls = message_factory.GetMessageClass(method_desc.input_type)
            response_cls = message_factory.GetMessageClass(method_desc.output_type)
            request_msg = _marshal_request(spec.get("request") or {}, request_cls)
            grpc = _import_grpc()
            callable_ = channel.unary_stream(
                method_path,
                request_serializer=request_cls.SerializeToString,
                response_deserializer=response_cls.FromString,
            )
            try:
                # No timeout — a server-streaming RPC runs until the server
                # closes it or the manager cancels this task. (The connection's
                # default_timeout_seconds is a unary concern; streams are
                # open-ended.)
                async for response in callable_(request_msg, metadata=metadata):
                    emit(_unmarshal_response(response))
            except asyncio.CancelledError:
                raise
            except grpc.aio.AioRpcError as exc:
                _raise_normalized(exc, context=f"stream {method_path}")
        except asyncio.CancelledError:
            raise
        finally:
            await _aclose(channel)

    async def close_subscription(
        self, connection: Connection, *, spec: dict[str, Any]
    ) -> None:
        """No-op: the manager cancels the ``open_subscription`` task, whose
        ``finally`` closes the channel. Nothing extra to do here (mirrors
        the MQTT plugin)."""
        return None


# ── extra_routes (server-streaming subscription management) ────────────
#
# Subscription-management routes mounted at
# ``/api/v1/admin/connections/grpc/``. They reach the process-wide
# SubscriptionManager via ``get_subscription_manager()`` and resolve
# connections via the injected ConnectionsContext (mirrors the MQTT / OAuth2
# / MCP plugin context-injection pattern). Dedicated ``/grpc/`` prefix — no
# catch-all collision with the generic CRUD routes.

_INJECTED_CTX = None


def _test_inject_context(ctx) -> None:
    """Test/CLI hook: set the ConnectionsContext route bodies use. ``None`` clears."""
    global _INJECTED_CTX
    _INJECTED_CTX = ctx


def _get_ctx():
    """Return the active ConnectionsContext (injected, or built fresh)."""
    if _INJECTED_CTX is not None:
        return _INJECTED_CTX
    from sagewai.admin.state_file import AdminStateFile, default_admin_state_path
    from sagewai.connections.bootstrap import build_connections_context

    return build_connections_context(AdminStateFile(default_admin_state_path()))


_grpc_router = APIRouter()


class _DrainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_events: int = Field(default=100, gt=0, le=10000)


def _resolve_grpc_connection(connection_id: str) -> Connection:
    """Resolve + validate a gRPC connection by id, or raise the right HTTP error."""
    from fastapi import HTTPException

    ctx = _get_ctx()
    record = ctx.store.get(connection_id)
    if record is None:
        raise HTTPException(404, f"connection {connection_id} not found")
    if record.protocol != "grpc":
        raise HTTPException(
            400, f"connection {connection_id} is not grpc (got {record.protocol})"
        )
    return record


@_grpc_router.post("/{connection_id}/subscribe")
async def _subscribe_route(connection_id: str, body: dict):
    """Open a server-streaming subscription. Body = GrpcStreamSpec."""
    from fastapi import HTTPException
    from pydantic import ValidationError

    record = _resolve_grpc_connection(connection_id)
    try:
        spec = GrpcStreamSpec(**body)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors())
    mgr = get_subscription_manager()
    # Build a decrypt context carrying the process-global credentials router
    # so the subscriber task can decrypt ``auth_token`` for
    # ``auth_mode: metadata_token`` (issue #378). Long-lived subscription, so
    # ``request=None`` — only ``creds`` is consumed by ``_maybe_decrypt``.
    plugin_ctx = _get_ctx().make_plugin_context(
        project_id=record.project_id, request=None
    )
    try:
        sub_id = await mgr.subscribe(
            plugin=GrpcProtocolPlugin(),
            connection=record,
            spec=spec.model_dump(),
            ctx=plugin_ctx,
        )
    except SubscriptionLimitExceededError as exc:
        raise HTTPException(409, str(exc))
    return {"subscription_id": sub_id}


@_grpc_router.post("/subscriptions/{subscription_id}/drain")
async def _drain_route(subscription_id: str, body: _DrainBody | None = None):
    """Drain up to ``max_events`` buffered stream items; returns a DrainResult."""
    from fastapi import HTTPException

    max_events = body.max_events if body is not None else 100
    mgr = get_subscription_manager()
    try:
        result = await mgr.drain(subscription_id, max_events)
    except SubscriptionNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return result.model_dump()


@_grpc_router.delete("/subscriptions/{subscription_id}", status_code=204)
async def _unsubscribe_route(subscription_id: str):
    """Tear down a subscription + cancel its background streaming task."""
    from fastapi import HTTPException, Response

    mgr = get_subscription_manager()
    try:
        await mgr.unsubscribe(subscription_id)
    except SubscriptionNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return Response(status_code=204)


@_grpc_router.get("/subscriptions")
async def _list_subscriptions_route():
    """List stats for every active subscription (process-wide)."""
    mgr = get_subscription_manager()
    return [s.model_dump() for s in mgr.list_subscriptions()]


@_grpc_router.get("/subscriptions/{subscription_id}")
async def _subscription_stats_route(subscription_id: str):
    """Observability snapshot for one subscription."""
    from fastapi import HTTPException

    mgr = get_subscription_manager()
    try:
        return mgr.stats(subscription_id).model_dump()
    except SubscriptionNotFoundError as exc:
        raise HTTPException(404, str(exc))


__all__ = [
    "GrpcAuthError",
    "GrpcCallError",
    "GrpcConnectionError",
    "GrpcDeadlineError",
    "GrpcError",
    "GrpcMarshalError",
    "GrpcMethodError",
    "GrpcNotInstalledError",
    "GrpcProtocolData",
    "GrpcProtocolPlugin",
    "GrpcStreamSpec",
    "_DescriptorPoolCache",
    "_call_metadata",
    "_marshal_request",
    "_normalize_method_path",
    "_run_op",
    "_split_method",
    "_test_inject_context",
    "_unmarshal_response",
]
