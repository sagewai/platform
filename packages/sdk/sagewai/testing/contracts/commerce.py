# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Contract fixture for the Commerce MCP server."""

from sagewai.testing.contract_test import ServerContract, ToolContract

COMMERCE_CONTRACT = ServerContract(
    server_name="commerce",
    tool_count=9,
    tools={
        "create_product": ToolContract(
            name="create_product",
            required_params=["name", "price"],
            optional_params=["currency", "description", "category", "stock"],
            param_types={
                "name": "string",
                "price": "integer",
                "currency": "string",
                "description": "string",
                "category": "string",
                "stock": "integer",
            },
            response_fields=["product_id", "name", "price"],
        ),
        "get_product": ToolContract(
            name="get_product",
            required_params=["product_id"],
            param_types={"product_id": "string"},
            response_fields=["product_id", "name", "price"],
        ),
        "list_products": ToolContract(
            name="list_products",
            optional_params=["category", "status"],
            param_types={"category": "string", "status": "string"},
        ),
        "search_products": ToolContract(
            name="search_products",
            required_params=["query"],
            param_types={"query": "string"},
        ),
        "update_inventory": ToolContract(
            name="update_inventory",
            required_params=["product_id", "quantity"],
            param_types={"product_id": "string", "quantity": "integer"},
        ),
        "create_order": ToolContract(
            name="create_order",
            required_params=["items"],
            optional_params=["customer_email"],
            param_types={"items": "array", "customer_email": "string"},
            response_fields=["order_id", "items", "status"],
        ),
        "get_order": ToolContract(
            name="get_order",
            required_params=["order_id"],
            param_types={"order_id": "string"},
            response_fields=["order_id", "items", "status"],
        ),
        "list_orders": ToolContract(
            name="list_orders",
            optional_params=["status"],
            param_types={"status": "string"},
        ),
        "update_order_status": ToolContract(
            name="update_order_status",
            required_params=["order_id", "status"],
            param_types={"order_id": "string", "status": "string"},
        ),
    },
)
