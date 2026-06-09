# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Bootstrap the platform's connection-handling triplet.

Both admin routes and CLI construct fresh `ConnectionsContext` instances
via :func:`build_connections_context`. The context holds the store +
router (shared across requests, cheap to construct) and exposes a
``make_plugin_context(project_id, request)`` factory that returns a
fresh :class:`PluginContext` per request.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sagewai.admin.state_file import AdminStateFile
from sagewai.connections.credentials import CredentialsBackendRouter
from sagewai.connections.protocols import DEFAULT_KEY_FOR, PROTOCOLS
from sagewai.connections.protocols.base import PluginContext
from sagewai.connections.store import ConnectionStore, _default_store_path


@dataclass(frozen=True)
class ConnectionsContext:
    """The platform's per-process connection-handling triplet."""

    store: Any
    router: CredentialsBackendRouter
    tenant_safe: bool = False

    def make_plugin_context(
        self, *, project_id: str | None, request: Any | None
    ) -> PluginContext:
        """Construct a fresh PluginContext for one request."""
        return PluginContext(
            store=self.store,
            creds=self.router,
            project_id=project_id,
            request=request,
        )


def build_connections_context(
    sf: AdminStateFile, *, store: Any | None = None, tenant_safe: bool = False
) -> ConnectionsContext:
    """Construct the platform's connection-handling triplet.

    Reads the platform default credentials backend from
    :class:`AdminStateFile` (PR3's ``get_default_credentials_backend``).
    Wires the store with the full plugin-registry's allowed protocols
    and per-protocol default-key extractors.
    """
    if store is None:
        store = ConnectionStore(
            _default_store_path(),
            allowed_protocols=tuple(p.id for p in PROTOCOLS),
            default_key_for=DEFAULT_KEY_FOR,
        )
    router = CredentialsBackendRouter(
        default_backend=sf.get_default_credentials_backend(),
    )
    return ConnectionsContext(store=store, router=router, tenant_safe=tenant_safe)


__all__ = ["ConnectionsContext", "build_connections_context"]
