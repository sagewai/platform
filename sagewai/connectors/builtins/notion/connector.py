# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Notion connector spec."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class NotionConnector(ConnectorSpec):
    """Notion workspace connector."""

    name: str = "notion"
    display_name: str = "Notion"
    category: str = "productivity"
    description: str = (
        "Search, create, and update pages and databases in Notion workspaces."
    )
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="api_key",
            label="Integration Token",
            env_var="NOTION_API_KEY",
            hint="From notion.so/my-integrations",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.notion.server",
    ]
    docs_url: str = "https://developers.notion.com/"
    agent_description: str = (
        "Query Notion databases, create and update pages, "
        "and search across a Notion workspace."
    )
    example_prompt: str = "Find all tasks marked 'In Progress' in my project tracker database."
    supports_webhook: bool = True
    supports_poller: bool = True
