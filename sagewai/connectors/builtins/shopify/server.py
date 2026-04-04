# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Shopify MCP Server.

Exposes Shopify Admin API operations as MCP-compatible tools via JSON-RPC.
Uses an in-memory store (production: swap for Shopify Admin API).

Run via stdio::

    python -m sagewai.connectors.builtins.shopify.server
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "shopify"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# In-memory Shopify store
# ---------------------------------------------------------------------------


class _ShopifyStore:
    """Simple in-memory Shopify store for the MCP server."""

    def __init__(self) -> None:
        self.products: dict[str, dict[str, Any]] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.customers: dict[str, dict[str, Any]] = {}
        self.inventory: dict[str, int] = {}

    def create_product(
        self,
        title: str,
        price: str,
        description: str = "",
        vendor: str = "",
        product_type: str = "",
        status: str = "active",
    ) -> dict[str, Any]:
        pid = f"gid://shopify/Product/{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        product = {
            "id": pid,
            "title": title,
            "price": price,
            "description": description,
            "vendor": vendor,
            "product_type": product_type,
            "status": status,
            "created_at": now,
        }
        self.products[pid] = product
        self.inventory[pid] = 0
        return product

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        product = self.products.get(product_id)
        if not product:
            return None
        return {**product, "inventory_quantity": self.inventory.get(product_id, 0)}

    def list_products(
        self, status: str = "", product_type: str = ""
    ) -> list[dict[str, Any]]:
        results = list(self.products.values())
        if status:
            results = [p for p in results if p["status"] == status]
        if product_type:
            results = [p for p in results if p["product_type"] == product_type]
        return [
            {**p, "inventory_quantity": self.inventory.get(p["id"], 0)}
            for p in results
        ]

    def create_order(
        self,
        line_items: list[dict[str, Any]],
        customer_email: str = "",
    ) -> dict[str, Any]:
        oid = f"gid://shopify/Order/{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        order = {
            "id": oid,
            "line_items": line_items,
            "customer_email": customer_email,
            "financial_status": "pending",
            "fulfillment_status": None,
            "created_at": now,
        }
        self.orders[oid] = order
        return order

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        return self.orders.get(order_id)

    def list_orders(self, status: str = "") -> list[dict[str, Any]]:
        results = list(self.orders.values())
        if status:
            results = [o for o in results if o["financial_status"] == status]
        return results

    def update_inventory(
        self, product_id: str, quantity: int
    ) -> dict[str, Any] | None:
        if product_id not in self.products:
            return None
        self.inventory[product_id] = quantity
        return {"product_id": product_id, "inventory_quantity": quantity}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

_store = _ShopifyStore()


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


TOOLS = [
    {
        "name": "create_product",
        "description": "Create a new Shopify product",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Product title"},
                "price": {"type": "string", "description": "Product price (e.g. '19.99')"},
                "description": {"type": "string", "description": "Product description"},
                "vendor": {"type": "string", "description": "Product vendor"},
                "product_type": {"type": "string", "description": "Product type/category"},
                "status": {"type": "string", "description": "Product status (active/draft/archived)"},
            },
            "required": ["title", "price"],
        },
    },
    {
        "name": "get_product",
        "description": "Get a Shopify product by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product ID"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "list_products",
        "description": "List Shopify products with optional filters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status"},
                "product_type": {"type": "string", "description": "Filter by product type"},
            },
        },
    },
    {
        "name": "create_order",
        "description": "Create a new Shopify order",
        "inputSchema": {
            "type": "object",
            "properties": {
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "quantity": {"type": "integer"},
                        },
                        "required": ["product_id"],
                    },
                    "description": "Order line items",
                },
                "customer_email": {"type": "string", "description": "Customer email"},
            },
            "required": ["line_items"],
        },
    },
    {
        "name": "get_order",
        "description": "Get a Shopify order by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order ID"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "list_orders",
        "description": "List Shopify orders with optional status filter",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by financial status"},
            },
        },
    },
    {
        "name": "update_inventory",
        "description": "Update product inventory quantity",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product ID"},
                "quantity": {"type": "integer", "description": "New inventory quantity"},
            },
            "required": ["product_id", "quantity"],
        },
    },
]


async def handle_request(
    raw: dict[str, Any], store: _ShopifyStore | None = None
) -> dict[str, Any]:
    """Process a single MCP JSON-RPC request."""
    s = store or _store
    request_id = raw.get("id")
    method = raw.get("method", "")
    params = raw.get("params", {})

    if method == "initialize":
        return _jsonrpc_response(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "ping":
        return _jsonrpc_response(request_id, {})

    if method == "tools/list":
        return _jsonrpc_response(request_id, {"tools": TOOLS})

    if method == "tools/call":
        return await _handle_tool_call(request_id, params, s)

    return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")


async def _handle_tool_call(
    request_id: Any, params: dict[str, Any], store: _ShopifyStore
) -> dict[str, Any]:
    """Dispatch a tools/call request."""
    tool_name = params.get("name", "")
    args = params.get("arguments", {})

    handlers: dict[str, Any] = {
        "create_product": lambda: store.create_product(
            title=args["title"],
            price=args["price"],
            description=args.get("description", ""),
            vendor=args.get("vendor", ""),
            product_type=args.get("product_type", ""),
            status=args.get("status", "active"),
        ),
        "get_product": lambda: store.get_product(args["product_id"]),
        "list_products": lambda: store.list_products(
            status=args.get("status", ""),
            product_type=args.get("product_type", ""),
        ),
        "create_order": lambda: store.create_order(
            line_items=args["line_items"],
            customer_email=args.get("customer_email", ""),
        ),
        "get_order": lambda: store.get_order(args["order_id"]),
        "list_orders": lambda: store.list_orders(status=args.get("status", "")),
        "update_inventory": lambda: store.update_inventory(
            product_id=args["product_id"], quantity=args["quantity"]
        ),
    }

    handler = handlers.get(tool_name)
    if not handler:
        return _jsonrpc_error(request_id, -32602, f"Unknown tool: {tool_name}")

    try:
        result = handler()
        content = json.dumps(result, default=str)
        return _jsonrpc_response(
            request_id, {"content": [{"type": "text", "text": content}]}
        )
    except Exception as exc:
        logger.exception("Tool call error: %s", tool_name)
        return _jsonrpc_error(request_id, -32000, str(exc))


async def run_stdio() -> None:
    """Run the MCP server over stdin/stdout."""
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    writer_transport, writer_protocol = (
        await asyncio.get_event_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
    )
    writer = asyncio.StreamWriter(
        writer_transport, writer_protocol, None, asyncio.get_event_loop()
    )

    logger.info("Shopify MCP server listening on stdio")

    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = await handle_request(raw)
        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()


if __name__ == "__main__":
    asyncio.run(run_stdio())
