# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Contract fixture for the Payments MCP server."""

from sagewai.testing.contract_test import ServerContract, ToolContract

PAYMENTS_CONTRACT = ServerContract(
    server_name="payments",
    tool_count=8,
    tools={
        "create_payment_intent": ToolContract(
            name="create_payment_intent",
            required_params=["amount"],
            optional_params=["currency", "customer_id", "description"],
            param_types={
                "amount": "integer",
                "currency": "string",
                "customer_id": "string",
                "description": "string",
            },
            response_fields=["payment_id", "amount", "currency", "status"],
        ),
        "get_payment": ToolContract(
            name="get_payment",
            required_params=["payment_id"],
            param_types={"payment_id": "string"},
            response_fields=["payment_id", "amount", "currency", "status"],
        ),
        "list_payments": ToolContract(
            name="list_payments",
            optional_params=["status", "customer_id"],
            param_types={"status": "string", "customer_id": "string"},
        ),
        "capture_payment": ToolContract(
            name="capture_payment",
            required_params=["payment_id"],
            param_types={"payment_id": "string"},
        ),
        "refund_payment": ToolContract(
            name="refund_payment",
            required_params=["payment_id"],
            optional_params=["amount"],
            param_types={"payment_id": "string", "amount": "integer"},
        ),
        "create_customer": ToolContract(
            name="create_customer",
            required_params=["name"],
            optional_params=["email"],
            param_types={"name": "string", "email": "string"},
            response_fields=["customer_id", "name"],
        ),
        "get_customer": ToolContract(
            name="get_customer",
            required_params=["customer_id"],
            param_types={"customer_id": "string"},
            response_fields=["customer_id", "name"],
        ),
        "list_customers": ToolContract(
            name="list_customers",
        ),
    },
)
