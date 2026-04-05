# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Toolhouse aggregator connector — unlocks 200+ integrations via MCP."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class ToolhouseConnector(ConnectorSpec):
    """Toolhouse aggregator — connects to 200+ tools via a single MCP server."""

    name: str = "toolhouse"
    display_name: str = "Toolhouse"
    category: str = "aggregator"
    description: str = "Connect to 200+ tools and APIs via Toolhouse's MCP server."
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="api_key",
            label="API Key",
            env_var="TOOLHOUSE_API_KEY",
            hint="From app.toolhouse.ai",
        ),
    ]
    mcp_command: list[str] = ["toolhouse", "mcp", "--transport", "stdio"]
    docs_url: str = "https://docs.toolhouse.ai"
