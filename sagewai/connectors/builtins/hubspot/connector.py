# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""HubSpot connector spec."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class HubSpotConnector(ConnectorSpec):
    """HubSpot CRM connector."""

    name: str = "hubspot"
    display_name: str = "HubSpot"
    category: str = "crm"
    description: str = (
        "Manage contacts, deals, companies, and tickets in HubSpot CRM."
    )
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="access_token",
            label="Private App Access Token",
            env_var="HUBSPOT_ACCESS_TOKEN",
            hint="From app.hubspot.com > Settings > Integrations > Private Apps",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.hubspot.server",
    ]
    docs_url: str = "https://developers.hubspot.com/docs/api/overview"
    agent_description: str = (
        "Search and manage HubSpot CRM records including contacts, "
        "companies, deals, and support tickets."
    )
    example_prompt: str = "List all deals in the pipeline that closed this quarter."
    supports_webhook: bool = True
    supports_poller: bool = True
