# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Shared executor for the Phase A new-protocol kinds.

One executor, four kinds. Dispatches by ``_kind`` into the matching
protocol plugin's ``_run_op`` callable. PR1-4 wire ``coap`` + ``modbus``
+ ``opcua`` + ``websocket``. Phase A is complete. Phase B will introduce
subscription/streaming protocols via a new abstraction. The
``_runners()`` callable rebuilds the runner map on each call so test
patches of e.g. ``_coap_run_op`` take effect.

The executor is invoked by the tool registry when a catalog entry has
``kind: coap | modbus | opcua | websocket``. Catalog entries reference
a connection by ``exec.<kind>.connection_ref`` (the connection's
``display_name`` in the project scope).

Credentials decrypt happens HERE — once, uniformly — before dispatch.
The store hands back encrypted ciphertext (``fernet:gAAAAA...`` for
``local``, ``{"$env": ...}`` markers for ``env``, etc.); the executor
resolves the plugin's sensitive field paths and uses the credentials
router to decrypt them in-place, then passes a clone of the connection
with plaintext ``protocol_data`` to the protocol's runner. Each runner's
contract is therefore simple: it consumes plaintext only.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Awaitable, Callable, Mapping

from sagewai.connections.credentials import CredentialsBackendRouter
from sagewai.connections.protocols import get_protocol
from sagewai.connections.protocols.base import get_sensitive_field_paths_for
from sagewai.connections.protocols.coap import _run_op as _coap_run_op
from sagewai.connections.protocols.grpc import _run_op as _grpc_run_op
from sagewai.connections.protocols.modbus import _run_op as _modbus_run_op
from sagewai.connections.protocols.mqtt import MqttProtocolPlugin
from sagewai.connections.protocols.opcua import _run_op as _opcua_run_op
from sagewai.connections.protocols.websocket import _run_op as _websocket_run_op
from sagewai.connections.store import ConnectionStore
from sagewai.connections.subscriptions.manager import get_subscription_manager


# Module-level kind dispatch. Tests patch the per-kind runner names
# (``_coap_run_op`` etc.) directly, so the dict is built dynamically
# in :func:`_runners` rather than captured once at import.
def _runners() -> dict[str, Callable[..., Awaitable[Any]]]:
    """Return the kind → runner map.

    Looking up via module globals at call time (rather than freezing
    a dict at import) lets tests patch the runner name on this module.
    Phase A is complete after PR4: all four new-protocol kinds are
    wired. Phase B will likely require a different abstraction
    (subscription/streaming) and therefore a new executor module.
    """
    return {
        "coap": _coap_run_op,
        "modbus": _modbus_run_op,
        "opcua": _opcua_run_op,
        "websocket": _websocket_run_op,
        "grpc": _grpc_run_op,
    }


def _build_default_router() -> CredentialsBackendRouter:
    """Construct a router via the platform's connections-context bootstrap.

    Lazy import: the bootstrap reads AdminStateFile to discover the
    platform-default credentials backend. Test paths that don't want to
    touch the admin state pass an explicit ``router`` kwarg instead.
    """
    from sagewai.admin.state_file import AdminStateFile, default_admin_state_path
    from sagewai.connections.bootstrap import build_connections_context

    ctx = build_connections_context(AdminStateFile(default_admin_state_path()))
    return ctx.router


def _resolve_connection(store, kind, project_id, connection_ref):
    """Resolve a connection by ``display_name`` within the project scope.

    Shared by the stateless ``_run_op`` path and the stateful MQTT
    subscribe path. Raises ``ValueError`` when the ref doesn't resolve.
    """
    matches = [
        c
        for c in store.list(project_id=project_id, protocol=kind)
        if c.display_name == connection_ref
    ]
    if not matches:
        raise ValueError(
            f"connection {connection_ref!r} not found for kind={kind!r} "
            f"in project {project_id!r}"
        )
    return matches[0]


async def _mqtt_dispatch(payload: Mapping[str, Any], *, store: ConnectionStore) -> Any:
    """Stateful MQTT dispatch — routes to the process-wide SubscriptionManager.

    Unlike the four Phase A kinds, MQTT does NOT use the ``_run_op``
    decrypt-then-call path: subscriptions are long-lived background tasks
    the manager owns. ``subscribe`` resolves the connection + hands the
    MQTT plugin to the manager; ``drain`` / ``unsubscribe`` operate on a
    subscription id alone (no connection lookup, no decrypt). The
    SubscriptionManager calls the plugin's ``open_subscription`` as its
    background task, where credentials decryption is the executor's concern
    — but PR2's MQTT subscribe path leaves decryption to the plugin's own
    defensive decrypt against ``ctx.creds`` (None here; in-process agents +
    admin pass plaintext or a real router as the foundation matures).
    """
    exec_block = payload["exec"]["mqtt"]
    operation = exec_block["operation"]
    args = dict(exec_block.get("args", {}))
    project_id = payload.get("project_id")
    mgr = get_subscription_manager()

    if operation == "subscribe":
        connection = _resolve_connection(
            store, "mqtt", project_id, exec_block["connection_ref"]
        )
        sub_id = await mgr.subscribe(
            plugin=MqttProtocolPlugin(), connection=connection, spec=args, ctx=None
        )
        return {"subscription_id": sub_id}
    if operation == "drain":
        result = await mgr.drain(
            args["subscription_id"], int(args.get("max_events", 100))
        )
        return result.model_dump()
    if operation == "unsubscribe":
        await mgr.unsubscribe(args["subscription_id"])
        return {"ok": True}
    raise ValueError(f"unknown mqtt operation: {operation!r}")


async def run(
    payload: Mapping[str, Any],
    *,
    store: ConnectionStore,
    router: CredentialsBackendRouter | None = None,
) -> Any:
    """Dispatch a tool-catalog payload to the matching connection.

    Payload shape:
        {
            "_kind":       "coap" | "modbus" | "opcua" | "websocket",
            "exec":        {"<kind>": {"connection_ref": "...", "operation": "...", "args": {...}}},
            "project_id":  "...",
            **caller_kwargs,  # merged into args; lowest priority
        }

    ``router`` lets tests inject a router instance; production paths leave
    it ``None`` and the executor builds one via the platform bootstrap.
    """
    kind = payload.get("_kind")

    # MQTT is STATEFUL — it routes subscribe/drain/unsubscribe to the
    # process-wide SubscriptionManager rather than the stateless
    # decrypt-then-call runner path the four Phase A kinds use.
    if kind == "mqtt":
        return await _mqtt_dispatch(payload, store=store)

    runners = _runners()
    if kind not in runners:
        raise ValueError(f"kind {kind!r} is not yet wired in the connections executor")

    exec_block = payload.get("exec", {}).get(kind, {})
    connection_ref = exec_block.get("connection_ref")
    operation = exec_block.get("operation")
    base_args = dict(exec_block.get("args", {}))

    project_id = payload.get("project_id")

    # Lookup by display_name in project scope.
    connection = _resolve_connection(store, kind, project_id, connection_ref)

    # Decrypt sensitive fields BEFORE handing the connection to the runner.
    # The runner's contract is "plaintext only" — no protocol-specific
    # decrypt logic in any of the four Phase A runners.
    if router is None:
        router = _build_default_router()
    plugin = get_protocol(kind)
    sensitive_paths = get_sensitive_field_paths_for(plugin, connection)
    if sensitive_paths:
        # The router is idempotent for non-encrypted leaves, so already-plaintext
        # or mixed-state records pass through cleanly. A backend health failure
        # propagates as-is — callers see the underlying CredentialsError.
        decrypted_pd = router.decrypt(
            connection.protocol_data,
            sensitive_field_paths=sensitive_paths,
            connection_credentials_backend=connection.credentials_backend,
        )
        connection = replace(connection, protocol_data=decrypted_pd)

    # Merge caller-side kwargs into args (caller-side wins on conflict).
    caller_kwargs = {
        k: v
        for k, v in payload.items()
        if k not in {"_kind", "exec", "project_id"}
    }
    base_args.update(caller_kwargs)

    runner = runners[kind]
    return await runner(connection, op=operation, args=base_args)


__all__ = ["run"]
