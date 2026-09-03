# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
from __future__ import annotations

from dataclasses import replace
from typing import Any

from sagewai.connections.protocols.mcp import McpProtocolPlugin
from sagewai.connections.store_ops import store_list
from sagewai.mcp.servers import get_server
from sagewai.work.harness_tools import McpConnectionResolver


class _McpProtocolSession:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def list_tools(self):
        return await self._client.list_tools()

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._client.call_tool(name, arguments)

    async def close(self) -> None:
        await self._client.__aexit__(None, None, None)


def mcp_connection_resolver(
    *,
    project_id: str | None,
    connection_store: Any,
    credentials: Any,
) -> McpConnectionResolver:
    async def resolve(server_id: str) -> _McpProtocolSession:
        get_server(server_id)
        plugin = McpProtocolPlugin()
        connections = await store_list(connection_store, project_id, protocol="mcp")
        connection = next(
            (
                item
                for item in connections
                if item.protocol_data.get("server_ref") == server_id
            ),
            None,
        )
        if connection is None:
            raise KeyError(server_id)
        protocol_data = connection.protocol_data
        if credentials is not None:
            protocol_data = credentials.decrypt(
                protocol_data,
                sensitive_field_paths=plugin.sensitive_field_paths_for(connection),
                connection_credentials_backend=connection.credentials_backend,
            )
            connection = replace(connection, protocol_data=protocol_data)
        client = plugin.client_for(connection)
        await client.__aenter__()
        return _McpProtocolSession(client)

    return resolve


__all__ = ["mcp_connection_resolver"]
