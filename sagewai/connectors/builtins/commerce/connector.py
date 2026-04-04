# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Commerce connector spec."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class CommerceConnector(ConnectorSpec):
    """Shopify commerce connector."""

    name: str = "commerce"
    display_name: str = "Shopify (Commerce)"
    category: str = "commerce"
    description: str = "Manage products, orders, and inventory via Shopify."
    auth_type: AuthType = AuthType.API_KEY
    auth_fields: list[AuthField] = [
        AuthField(
            key="shop_domain",
            label="Shop Domain",
            env_var="SHOPIFY_SHOP_DOMAIN",
            secret=False,
            hint="e.g. mystore.myshopify.com",
        ),
        AuthField(
            key="access_token",
            label="Access Token",
            env_var="SHOPIFY_ACCESS_TOKEN",
        ),
    ]
    mcp_command: list[str] = [
        "python3",
        "-m",
        "sagewai.connectors.builtins.commerce.server",
    ]
    docs_url: str = "https://shopify.dev/docs"
    supports_webhook: bool = True
