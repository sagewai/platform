# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""X (Twitter) connector."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class XConnector(ConnectorSpec):
    """X (Twitter) connector for posting and reading tweets."""

    name: str = "x"
    display_name: str = "X (Twitter)"
    category: str = "communication"
    description: str = "Post tweets, read timelines, and manage X/Twitter accounts."
    auth_type: AuthType = AuthType.OAUTH2
    auth_fields: list[AuthField] = [
        AuthField(
            key="client_id",
            label="Client ID",
            env_var="X_CLIENT_ID",
            secret=False,
        ),
        AuthField(
            key="client_secret",
            label="Client Secret",
            env_var="X_CLIENT_SECRET",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.x.server",
    ]
    oauth_authorize_url: str = "https://twitter.com/i/oauth2/authorize"
    oauth_token_url: str = "https://api.twitter.com/2/oauth2/token"
    oauth_scopes: list[str] = ["tweet.read", "tweet.write", "users.read"]
    docs_url: str = "https://developer.x.com/en/docs"
    supports_webhook: bool = True
    supports_poller: bool = True
