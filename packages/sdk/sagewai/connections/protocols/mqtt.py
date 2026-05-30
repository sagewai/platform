# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""MQTT (MQTT 3.1.1 / 5.0) plugin — the first subscription-capable protocol.

Implements BOTH the :class:`~sagewai.connections.protocols.base.ProtocolPlugin`
(CRUD / schema / ``test``) and the PR1
:class:`~sagewai.connections.subscriptions.base.SubscriptionPlugin`
(``open_subscription`` / ``close_subscription`` / ``subscription_spec_schema``).

Consume-only in PR2: subscribe to a topic filter, iterate inbound messages,
and push each into the manager-owned buffer via the ``emit`` callback. The
manager owns the buffer, the bounds, and the subscriber-task lifecycle; the
plugin only knows how to connect-and-emit.

PR2 ships ``overflow_policy=drop_oldest`` only; ``pause`` is a deferred
follow-up — it needs a ``SubscriptionPlugin`` resume-signal Protocol
extension validated against a real broker. A ``pause`` spec is rejected at
validation time (the "Phase B feature deferral via schema rejection" canon).

Transport / version: aiomqtt 2.x. ``Client(hostname, port, *, username,
password, identifier, transport, keepalive, tls_context, ...)`` — the
``_build_client`` helper maps ``protocol_data`` onto these kwargs. A
``transport: "tls"`` config maps to ``transport="tcp"`` + a ``tls_context``
(aiomqtt's own ``transport`` Literal is ``tcp | websockets | unix``; TLS is
expressed via the SSL context, not the transport name).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import ssl
from typing import Any, Callable, ClassVar, Literal

import click
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sagewai.connections.models import Connection, TestResult
from sagewai.connections.protocols.base import PluginContext
from sagewai.connections.subscriptions.base import EmitResult
from sagewai.connections.subscriptions.errors import (
    SubscriptionLimitExceededError,
    SubscriptionNotFoundError,
)
from sagewai.connections.subscriptions.manager import get_subscription_manager


# ── errors ────────────────────────────────────────────────────────────


class MqttError(Exception):
    """Base for all MQTT plugin errors."""

    code: ClassVar[str] = "mqtt_error"


class MqttNotInstalledError(MqttError):
    """The ``aiomqtt`` library is not installed."""

    code: ClassVar[str] = "mqtt_not_installed"

    def __init__(self) -> None:
        super().__init__(
            "aiomqtt is not installed. "
            "Run `pip install sagewai[mqtt]` to enable MQTT connections."
        )


class MqttConnectionError(MqttError):
    """Transport-level failure (DNS / TCP) or the broker closed the link."""

    code: ClassVar[str] = "mqtt_connection_error"


class MqttAuthError(MqttError):
    """The broker rejected the credentials (bad username / password)."""

    code: ClassVar[str] = "mqtt_auth_error"


class MqttSubscribeError(MqttError):
    """The SUBSCRIBE was refused (e.g., topic-filter ACL denial)."""

    code: ClassVar[str] = "mqtt_subscribe_error"


class MqttTlsError(MqttError):
    """TLS handshake / certificate verification failed."""

    code: ClassVar[str] = "mqtt_tls_error"


# ── schemas ───────────────────────────────────────────────────────────


class MqttProtocolData(BaseModel):
    """Validation schema for ``protocol_data`` of MQTT connections."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(..., min_length=1)
    port: int = Field(default=1883, ge=1, le=65535)
    transport: Literal["tcp", "tls", "websockets"] = "tcp"
    client_id: str = ""
    mqtt_version: Literal["3.1.1", "5.0"] = "5.0"
    username: str = ""
    password: str = ""  # sensitive
    keepalive_seconds: int = Field(default=60, gt=0)
    tls_ca_cert: str = ""
    # Per-connection bounds — lower the manager's hard caps, never raise them.
    max_events_per_subscription: int | None = Field(default=None, gt=0)
    max_event_bytes: int | None = Field(default=None, gt=0)
    sandbox_tier_override: Literal["TRUSTED", "SANDBOXED"] | None = None


class MqttSubscriptionSpec(BaseModel):
    """Per-subscription spec validated by the manager before opening."""

    model_config = ConfigDict(extra="forbid")

    topic_filter: str = Field(..., min_length=1)
    qos: Literal[0, 1, 2] = 0
    # PR2: drop_oldest only. ``pause`` is a deferred follow-up (it needs a
    # SubscriptionPlugin resume-signal extension validated against a real
    # broker). A ``pause`` spec fails validation here with a clear message.
    overflow_policy: Literal["drop_oldest"] = "drop_oldest"
    # Per-subscription bounds (manager clamps these to its hard caps).
    max_events_per_subscription: int | None = Field(default=None, gt=0)
    max_event_bytes: int | None = Field(default=None, gt=0)


# ── library import + client construction ──────────────────────────────


def _import_aiomqtt():
    """Lazy-import aiomqtt with a clear error message when missing."""
    try:
        import aiomqtt  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MqttNotInstalledError() from exc
    return aiomqtt


def _resolve_protocol_version(aiomqtt, version: str):
    """Map our ``mqtt_version`` string to aiomqtt's ``ProtocolVersion``.

    Returns ``None`` (aiomqtt default) if the enum can't be resolved on the
    installed version — the default (MQTT 5.0) is broker-compatible.
    """
    pv = getattr(aiomqtt, "ProtocolVersion", None)
    if pv is None:
        return None
    if version == "3.1.1":
        return getattr(pv, "V311", None)
    return getattr(pv, "V5", None)


def _build_client(pd: dict[str, Any]):
    """Construct an ``aiomqtt.Client`` from ``protocol_data``.

    aiomqtt 2.x signature (verified at implementation time):
    ``Client(hostname, port, *, username, password, identifier, transport,
    keepalive, tls_context, ...)``.
    """
    aiomqtt = _import_aiomqtt()
    kwargs: dict[str, Any] = {
        "hostname": pd["host"],
        "port": int(pd.get("port", 1883)),
        "keepalive": int(pd.get("keepalive_seconds", 60)),
    }
    if pd.get("username"):
        kwargs["username"] = pd["username"]
    if pd.get("password"):
        kwargs["password"] = pd["password"]
    if pd.get("client_id"):
        kwargs["identifier"] = pd["client_id"]  # aiomqtt 2.x uses `identifier`

    transport = pd.get("transport", "tcp")
    if transport == "websockets":
        kwargs["transport"] = "websockets"
    # aiomqtt's own ``transport`` Literal is tcp|websockets|unix; TLS is
    # expressed via tls_context, not a transport name. transport=="tls"
    # keeps the underlying transport tcp and adds the SSL context below.
    if transport == "tls" or pd.get("tls_ca_cert"):
        ctx = ssl.create_default_context()
        if pd.get("tls_ca_cert"):
            ctx.load_verify_locations(cadata=pd["tls_ca_cert"])
        kwargs["tls_context"] = ctx

    pv = _resolve_protocol_version(aiomqtt, pd.get("mqtt_version", "5.0"))
    if pv is not None:
        kwargs["protocol"] = pv

    return aiomqtt.Client(**kwargs)


def _normalize(exc: Exception) -> MqttError:
    """Map a raw aiomqtt / transport exception onto the MqttError hierarchy."""
    if isinstance(exc, MqttError):
        return exc
    try:
        import aiomqtt
        if isinstance(exc, getattr(aiomqtt, "MqttError", ())):
            msg = str(exc).lower()
            if (
                "not authoris" in msg
                or "not authorized" in msg
                or "bad user" in msg
                or "bad username" in msg
                or "password" in msg
            ):
                return MqttAuthError(str(exc))
            if "tls" in msg or "certificate" in msg or "ssl" in msg:
                return MqttTlsError(str(exc))
            return MqttConnectionError(str(exc))
    except ImportError:
        pass
    if isinstance(exc, ssl.SSLError):
        return MqttTlsError(str(exc))
    if isinstance(exc, OSError):
        return MqttConnectionError(str(exc))
    return MqttError(str(exc))


# ── plugin ────────────────────────────────────────────────────────────


class MqttProtocolPlugin:
    """MQTT plugin — subscribe-and-buffer streaming over the PR1 manager."""

    id: ClassVar[str] = "mqtt"
    display_name: ClassVar[str] = "MQTT"
    sensitive_fields: ClassVar[tuple[str, ...]] = ("password",)

    # ── ProtocolPlugin half ───────────────────────────────────────────

    def protocol_data_schema(self) -> type[BaseModel]:
        return MqttProtocolData

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        out = dict(protocol_data)
        if not include_secrets and out.get("password"):
            out["password"] = "***"
        return out

    async def on_create(
        self, connection: Connection, *, ctx: PluginContext
    ) -> Connection:
        return connection

    async def on_update(
        self, before: Connection, after: Connection, *, ctx: PluginContext
    ) -> Connection:
        return after

    async def on_delete(
        self, connection: Connection, *, ctx: PluginContext
    ) -> None:
        return None

    async def test(
        self, connection: Connection, *, ctx: PluginContext
    ) -> TestResult:
        """Connect + immediately disconnect — confirms a CONNACK, no subscribe.

        The admin ``POST /test`` route pre-decrypts ``protocol_data`` before
        calling this, so the connection arrives with a plaintext password.
        Defensively decrypts via ``ctx.creds`` for callers outside the admin
        route (CLI / autopilot health checks) — mirrors the CoAP / Modbus /
        OPC UA / WebSocket plugins' pattern. Pre-decrypted / malformed inputs
        pass through; the actual connect surfaces the right error.
        """
        connection = self._maybe_decrypt(connection, ctx)
        try:
            client = _build_client(connection.protocol_data)
            async with client:
                pass  # connect + clean disconnect; NO subscribe
            return TestResult(ok=True, message="mqtt CONNACK ok")
        except MqttError as exc:
            return TestResult(ok=False, message=str(exc))
        except Exception as exc:  # normalize unknown aiomqtt / transport errors
            normalized = _normalize(exc)
            return TestResult(ok=False, message=str(normalized))

    def extra_routes(self) -> APIRouter:
        return _mqtt_router

    def extra_cli(self) -> list[click.Command]:
        return []

    def _maybe_decrypt(
        self, connection: Connection, ctx: Any
    ) -> Connection:
        """Decrypt ``password`` via ``ctx.creds`` when it is a real router.

        No-op when ``ctx`` is None / lacks a ``CredentialsBackendRouter``
        (already-plaintext admin-route path, or test mocks). Failures pass
        through silently — the connect surfaces the real error.
        """
        from dataclasses import replace

        from sagewai.connections.credentials import CredentialsBackendRouter
        from sagewai.connections.protocols.base import (
            get_sensitive_field_paths_for,
        )

        creds = getattr(ctx, "creds", None) if ctx is not None else None
        if not isinstance(creds, CredentialsBackendRouter):
            return connection
        try:
            sensitive_paths = get_sensitive_field_paths_for(self, connection)
            if not sensitive_paths:
                return connection
            decrypted_pd = creds.decrypt(
                connection.protocol_data,
                sensitive_field_paths=sensitive_paths,
                connection_credentials_backend=connection.credentials_backend,
            )
            return replace(connection, protocol_data=decrypted_pd)
        except Exception:
            return connection

    # ── SubscriptionPlugin half ───────────────────────────────────────

    def subscription_spec_schema(self) -> type[BaseModel]:
        return MqttSubscriptionSpec

    async def open_subscription(
        self,
        connection: Connection,
        *,
        spec: dict[str, Any],
        emit: Callable[[dict[str, Any]], EmitResult],
        ctx: Any,
    ) -> None:
        """Connect, subscribe to ``topic_filter``, iterate messages → ``emit``.

        Runs as the manager's background task: it loops forever (the broker
        keeps the message iterator open), re-raises ``asyncio.CancelledError``
        so the manager can cancel it cleanly, and normalizes connect / auth
        failures onto the :class:`MqttError` hierarchy (the dead-task reaper
        handles crashes).
        """
        pd = connection.protocol_data
        topic_filter = spec["topic_filter"]
        qos = int(spec.get("qos", 0))
        try:
            client = _build_client(pd)
            async with client:
                await client.subscribe(topic_filter, qos=qos)
                async for message in client.messages:
                    payload = message.payload
                    if isinstance(payload, (bytes, bytearray)):
                        payload = bytes(payload).decode("utf-8", errors="replace")
                    elif not isinstance(payload, str):
                        payload = str(payload)
                    emit(
                        {
                            "topic": str(message.topic),
                            "payload": payload,
                            "qos": int(getattr(message, "qos", qos)),
                            "retain": bool(getattr(message, "retain", False)),
                            "timestamp": _dt.datetime.now(
                                _dt.timezone.utc
                            ).isoformat(),
                        }
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _normalize(exc) from exc

    async def close_subscription(
        self, connection: Connection, *, spec: dict[str, Any]
    ) -> None:
        """No-op: aiomqtt's ``async with`` closes the connection on exit.

        The manager cancels the ``open_subscription`` task, which exits the
        ``async with`` block and tears down the broker link. Nothing extra to
        do here.
        """
        return None


# ── extra_routes (Task 7) ─────────────────────────────────────────────
#
# Subscription management routes mounted at
# ``/api/v1/admin/connections/mqtt/``. They reach the process-wide
# SubscriptionManager via ``get_subscription_manager()`` and resolve
# connections via the injected ConnectionsContext (mirrors the OAuth2 /
# MCP plugin context-injection pattern).

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


_mqtt_router = APIRouter()


class _DrainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_events: int = Field(default=100, gt=0, le=10000)


def _resolve_mqtt_connection(connection_id: str) -> Connection:
    """Resolve + validate an MQTT connection by id, or raise the right HTTP error."""
    ctx = _get_ctx()
    record = ctx.store.get(connection_id)
    if record is None:
        raise HTTPException(404, f"connection {connection_id} not found")
    if record.protocol != "mqtt":
        raise HTTPException(
            400, f"connection {connection_id} is not mqtt (got {record.protocol})"
        )
    return record


@_mqtt_router.post("/{connection_id}/subscribe")
async def _subscribe_route(connection_id: str, body: dict):
    """Open a subscription on an MQTT connection. Body = MqttSubscriptionSpec."""
    record = _resolve_mqtt_connection(connection_id)
    try:
        spec = MqttSubscriptionSpec(**body)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors())
    mgr = get_subscription_manager()
    try:
        sub_id = await mgr.subscribe(
            plugin=MqttProtocolPlugin(),
            connection=record,
            spec=spec.model_dump(),
            ctx=None,
        )
    except SubscriptionLimitExceededError as exc:
        raise HTTPException(409, str(exc))
    return {"subscription_id": sub_id}


@_mqtt_router.post("/subscriptions/{subscription_id}/drain")
async def _drain_route(subscription_id: str, body: _DrainBody | None = None):
    """Drain up to ``max_events`` buffered events; returns a DrainResult."""
    max_events = (body.max_events if body is not None else 100)
    mgr = get_subscription_manager()
    try:
        result = await mgr.drain(subscription_id, max_events)
    except SubscriptionNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return result.model_dump()


@_mqtt_router.delete("/subscriptions/{subscription_id}", status_code=204)
async def _unsubscribe_route(subscription_id: str):
    """Tear down a subscription + cancel its background task."""
    mgr = get_subscription_manager()
    try:
        await mgr.unsubscribe(subscription_id)
    except SubscriptionNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return Response(status_code=204)


@_mqtt_router.get("/subscriptions")
async def _list_subscriptions_route():
    """List stats for every active subscription (process-wide)."""
    mgr = get_subscription_manager()
    return [s.model_dump() for s in mgr.list_subscriptions()]


@_mqtt_router.get("/subscriptions/{subscription_id}")
async def _subscription_stats_route(subscription_id: str):
    """Observability snapshot for one subscription."""
    mgr = get_subscription_manager()
    try:
        return mgr.stats(subscription_id).model_dump()
    except SubscriptionNotFoundError as exc:
        raise HTTPException(404, str(exc))


__all__ = [
    "MqttAuthError",
    "MqttConnectionError",
    "MqttError",
    "MqttNotInstalledError",
    "MqttProtocolData",
    "MqttProtocolPlugin",
    "MqttSubscribeError",
    "MqttSubscriptionSpec",
    "MqttTlsError",
    "_test_inject_context",
]
