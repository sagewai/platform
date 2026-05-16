# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Composio aggregator connector — unlocks 200+ integrations via MCP."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class ComposioConnector(ConnectorSpec):
    """Composio aggregator — connects to 200+ tools via a single MCP server."""

    name: str = "composio"
    display_name: str = "Composio"
    category: str = "aggregator"
    description: str = "Connect to 200+ tools and APIs via Composio's MCP server."
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="api_key",
            label="API Key",
            env_var="COMPOSIO_API_KEY",
            hint="From app.composio.dev",
        ),
    ]
    mcp_command: list[str] = ["composio", "serve", "--transport", "stdio"]
    docs_url: str = "https://docs.composio.dev"
