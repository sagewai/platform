# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Knowledge Graph connector spec."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class KnowledgeGraphConnector(ConnectorSpec):
    """NebulaGraph knowledge graph connector."""

    name: str = "knowledge_graph"
    display_name: str = "Knowledge Graph (NebulaGraph)"
    category: str = "data"
    description: str = "Query and manage knowledge graphs via NebulaGraph."
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="host",
            label="Host",
            env_var="NEBULA_HOST",
            secret=False,
            hint="e.g. localhost",
        ),
        AuthField(
            key="port",
            label="Port",
            env_var="NEBULA_PORT",
            secret=False,
            hint="e.g. 9669",
        ),
        AuthField(
            key="user",
            label="Username",
            env_var="NEBULA_USER",
            secret=False,
        ),
        AuthField(
            key="password",
            label="Password",
            env_var="NEBULA_PASSWORD",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.knowledge_graph.server",
    ]
    docs_url: str = "https://docs.nebula-graph.io"
