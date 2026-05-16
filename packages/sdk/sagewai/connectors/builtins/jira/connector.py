# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Jira connector spec."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class JiraConnector(ConnectorSpec):
    """Atlassian Jira project and issue tracking connector."""

    name: str = "jira"
    display_name: str = "Jira"
    category: str = "productivity"
    description: str = (
        "Create, update, and search issues in Atlassian Jira projects."
    )
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="email",
            label="Account Email",
            env_var="JIRA_EMAIL",
            secret=False,
            hint="Atlassian account email",
        ),
        AuthField(
            key="api_token",
            label="API Token",
            env_var="JIRA_API_TOKEN",
            hint="From id.atlassian.com/manage-profile/security/api-tokens",
        ),
        AuthField(
            key="base_url",
            label="Jira Base URL",
            env_var="JIRA_BASE_URL",
            secret=False,
            hint="e.g. https://your-domain.atlassian.net",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.jira.server",
    ]
    docs_url: str = "https://developer.atlassian.com/cloud/jira/platform/rest/v3/"
    agent_description: str = (
        "Search, create, and update Jira issues. Manage sprints, "
        "boards, and project workflows."
    )
    example_prompt: str = "Show me all unresolved bugs assigned to me in the PROJ project."
    supports_webhook: bool = True
    supports_poller: bool = True
