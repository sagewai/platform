# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Shopify connector — product/order/inventory management."""

from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


class ShopifyConnector(ConnectorSpec):
    """Shopify Admin API connector for e-commerce management."""

    name: str = "shopify"
    display_name: str = "Shopify"
    category: str = "commerce"
    description: str = (
        "Manage products, orders, customers, and inventory via Shopify Admin API."
    )
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
        "sagewai.connectors.builtins.shopify.server",
    ]
    docs_url: str = "https://shopify.dev/docs/admin-api"
    supports_webhook: bool = True
