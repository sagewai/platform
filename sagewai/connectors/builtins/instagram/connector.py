# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Instagram Graph API connector."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class InstagramConnector(ConnectorSpec):
    """Instagram Graph API connector for business accounts."""

    name: str = "instagram"
    display_name: str = "Instagram"
    category: str = "communication"
    description: str = (
        "Publish posts, read insights, and manage Instagram Business accounts."
    )
    auth_type: AuthType = AuthType.OAUTH2
    auth_fields: list[AuthField] = [
        AuthField(
            key="client_id",
            label="Client ID",
            env_var="INSTAGRAM_CLIENT_ID",
            secret=False,
        ),
        AuthField(
            key="client_secret",
            label="Client Secret",
            env_var="INSTAGRAM_CLIENT_SECRET",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.instagram.server",
    ]
    oauth_authorize_url: str = "https://api.instagram.com/oauth/authorize"
    oauth_token_url: str = "https://api.instagram.com/oauth/access_token"
    oauth_scopes: list[str] = ["instagram_basic", "instagram_content_publish"]
    docs_url: str = "https://developers.facebook.com/docs/instagram-api"
    supports_webhook: bool = True
    supports_poller: bool = True
