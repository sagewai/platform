# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tool executors — one module per CatalogEntry.kind."""
from sagewai.tools.executors import cli, connections, http, mcp, sdk, webhook

_REGISTRY = {
    "sdk":       sdk.run,
    "http":      http.run,
    "mcp":       mcp.run,
    "cli":       cli.run,
    "webhook":   webhook.run,
    "coap":      connections.run,
    "modbus":    connections.run,
    "opcua":     connections.run,
    "websocket": connections.run,
}


def get(kind: str):
    """Return the ``run`` coroutine for the given ``kind``."""
    try:
        return _REGISTRY[kind]
    except KeyError as exc:
        raise ValueError(f"unknown executor kind: {kind!r}") from exc


__all__ = ["get", "sdk", "http", "mcp", "cli", "webhook", "connections"]
