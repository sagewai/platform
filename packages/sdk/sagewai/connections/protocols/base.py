# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Plugin contract + service-locator for protocol plugins.

The :class:`ProtocolPlugin` Protocol defines the contract every plugin
implements. Plugins are stateless singletons held in the registry; the
per-request state (``project_id``, ``request``, ``store``, ``creds``)
flows through :class:`PluginContext`.

PR2 ships the contract + 5 plugin implementations. PR3 wires the
``CredentialsBackendRouter`` into ``PluginContext.creds`` (today: None).
PR4 mounts the plugins' ``extra_routes()`` and ``extra_cli()`` outputs
onto the live admin and CLI surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, runtime_checkable

import click
from fastapi import APIRouter, Request
from pydantic import BaseModel

from sagewai.connections.models import Connection, TestResult
from sagewai.connections.store import ConnectionStore


@dataclass(frozen=True)
class PluginContext:
    """Service locator passed to every plugin method.

    ``store``: the generic :class:`ConnectionStore` instance plugins use
        for persistence operations beyond what their methods receive
        directly (e.g., looking up siblings for re-authorize scope unions).
    ``creds``: PR3's ``CredentialsBackendRouter`` instance. ``None`` in
        PR2 — plugins that don't yet need credentials encryption skip it;
        plugins that do (oauth2's token persistence) still work because
        PR2's oauth2 plugin delegates to ``sagewai.oauth.vault`` which
        manages its own Sealed.Crypto layer.
    ``project_id``: the active project scope (`X-Project-ID` value or
        CLI ``--project`` flag).
    ``request``: the FastAPI ``Request`` for admin paths; ``None`` for
        CLI- and executor-driven calls.
    """

    store: ConnectionStore
    creds: Any | None  # PR3: CredentialsBackendRouter
    project_id: str | None
    request: Request | None


@runtime_checkable
class ProtocolPlugin(Protocol):
    """Contract every protocol plugin implements.

    Plugins are stateless singletons. Per-request state arrives via
    :class:`PluginContext`. Plugin discovery is code-driven: the static
    ``PROTOCOLS`` tuple in :mod:`sagewai.connections.protocols.__init__`
    lists supported ids.
    """

    # ── identity ──────────────────────────────────────────────────────

    id: ClassVar[str]                         # e.g., "http", "oauth2", "mcp"
    display_name: ClassVar[str]               # e.g., "HTTP / REST"
    sensitive_fields: ClassVar[tuple[str, ...]]  # JSON-pointer-ish paths into protocol_data

    # ── schema ────────────────────────────────────────────────────────

    def protocol_data_schema(self) -> type[BaseModel]:
        """Pydantic v2 model class for validating writes to ``protocol_data``."""

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        """Return the masked (default) or decrypted shape.

        Default behavior strips entries at ``sensitive_fields`` paths;
        plugins override for fancier rendering.
        """

    # ── lifecycle hooks ───────────────────────────────────────────────

    async def on_create(
        self, connection: Connection, *, ctx: PluginContext
    ) -> Connection:
        """Hook called after generic CRUD persists a new record.

        oauth2 returns the authorize URL alongside via a separate
        admin-route call; this hook may mutate-then-persist via
        ``ctx.store.update`` if the plugin needs to record post-create
        state (e.g., a generated redirect URI derived from request host).
        Return the (possibly updated) connection.
        """

    async def on_update(
        self,
        before: Connection,
        after: Connection,
        *,
        ctx: PluginContext,
    ) -> Connection:
        """Hook called after generic CRUD applies a PATCH.

        Use to react to status transitions, scope additions, or
        backend-switch events. Return the (possibly further updated)
        connection.
        """

    async def on_delete(
        self, connection: Connection, *, ctx: PluginContext
    ) -> None:
        """Hook called BEFORE generic CRUD removes the record.

        Plugins use this to call vendor revoke endpoints, tear down
        transient sessions, etc. Failures here should raise — generic
        CRUD won't delete if the plugin's tear-down fails.
        """

    # ── test ──────────────────────────────────────────────────────────

    async def test(
        self, connection: Connection, *, ctx: PluginContext
    ) -> TestResult:
        """Health-check the connection against the live service.

        Returns a :class:`TestResult`. Should not mutate state.
        """

    # ── route + CLI extensions (mounted by PR4) ───────────────────────

    def extra_routes(self) -> APIRouter:
        """Return a FastAPI APIRouter the generic router mounts at
        ``/api/v1/admin/connections/<plugin.id>/...``.

        Empty router (``APIRouter()``) is fine for plugins with no
        custom routes (http, sdk).
        """

    def extra_cli(self) -> list[click.Command]:
        """Return Click commands the generic CLI mounts as a sub-group
        ``sagewai connections <plugin.id> ...``.

        Empty list is fine for plugins with no custom commands.
        """


def get_sensitive_field_paths_for(
    plugin: "ProtocolPlugin", connection: Connection
) -> tuple[str, ...]:
    """Resolve sensitive field paths for a connection.

    Returns the result of ``plugin.sensitive_field_paths_for(connection)``
    if the plugin implements it; otherwise falls back to the static
    ``plugin.sensitive_fields`` ClassVar. This indirection lets plugins
    declare per-record sensitive paths (the MCP plugin uses this to derive
    sensitive paths from each connection's resolved registry entry) while
    other plugins continue using their static ClassVar.
    """
    fn = getattr(plugin, "sensitive_field_paths_for", None)
    if callable(fn):
        return fn(connection)
    return plugin.sensitive_fields


__all__ = [
    "PluginContext",
    "ProtocolPlugin",
    "TestResult",
    "get_sensitive_field_paths_for",
]
