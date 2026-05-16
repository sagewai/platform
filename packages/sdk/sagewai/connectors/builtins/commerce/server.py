# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Commerce MCP Server.

Exposes e-commerce operations as MCP-compatible tools via JSON-RPC.
Uses an in-memory store (production: swap for Shopify / Stripe API).

Run via stdio::

    python -m mcp_commerce
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
SERVER_NAME = "commerce"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# In-memory commerce store
# ---------------------------------------------------------------------------


class _CommerceStore:
    """Simple in-memory commerce store for the MCP server."""

    def __init__(self) -> None:
        self.products: dict[str, dict[str, Any]] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.inventory: dict[str, int] = {}

    def create_product(
        self,
        name: str,
        price: int,
        currency: str = "usd",
        description: str = "",
        category: str = "",
        stock: int = 0,
    ) -> dict[str, Any]:
        pid = f"prod_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        product = {
            "id": pid,
            "name": name,
            "price": price,
            "currency": currency.lower(),
            "description": description,
            "category": category,
            "status": "active",
            "created_at": now,
        }
        self.products[pid] = product
        self.inventory[pid] = stock
        return {**product, "stock": stock}

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        product = self.products.get(product_id)
        if not product:
            return None
        return {**product, "stock": self.inventory.get(product_id, 0)}

    def list_products(self, category: str = "", status: str = "") -> list[dict[str, Any]]:
        results = list(self.products.values())
        if category:
            results = [p for p in results if p["category"] == category]
        if status:
            results = [p for p in results if p["status"] == status]
        return [{**p, "stock": self.inventory.get(p["id"], 0)} for p in results]

    def search_products(self, query: str) -> list[dict[str, Any]]:
        q = query.lower()
        return [
            {**p, "stock": self.inventory.get(p["id"], 0)}
            for p in self.products.values()
            if q in p["name"].lower() or q in p.get("description", "").lower()
        ]

    def update_inventory(self, product_id: str, quantity: int) -> dict[str, Any] | None:
        if product_id not in self.products:
            return None
        self.inventory[product_id] = quantity
        return {"product_id": product_id, "stock": quantity}

    def create_order(
        self,
        items: list[dict[str, Any]],
        customer_email: str = "",
    ) -> dict[str, Any]:
        oid = f"ord_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        total = 0
        order_items = []
        for item in items:
            pid = item["product_id"]
            qty = item.get("quantity", 1)
            product = self.products.get(pid)
            if product:
                line_total = product["price"] * qty
                total += line_total
                order_items.append({
                    "product_id": pid,
                    "product_name": product["name"],
                    "quantity": qty,
                    "unit_price": product["price"],
                    "line_total": line_total,
                })
        order = {
            "id": oid,
            "items": order_items,
            "total": total,
            "currency": "usd",
            "customer_email": customer_email,
            "status": "pending",
            "created_at": now,
        }
        self.orders[oid] = order
        return order

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        return self.orders.get(order_id)

    def list_orders(self, status: str = "") -> list[dict[str, Any]]:
        results = list(self.orders.values())
        if status:
            results = [o for o in results if o["status"] == status]
        return results

    def update_order_status(self, order_id: str, status: str) -> dict[str, Any] | None:
        order = self.orders.get(order_id)
        if not order:
            return None
        order["status"] = status
        return order


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

def _make_store() -> _CommerceStore:
    return _CommerceStore()


_store = _make_store()


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


TOOLS = [
    {
        "name": "create_product",
        "description": "Create a new product",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Product name"},
                "price": {"type": "integer", "description": "Price in smallest currency unit"},
                "currency": {"type": "string", "description": "ISO currency code (default: usd)"},
                "description": {"type": "string", "description": "Product description"},
                "category": {"type": "string", "description": "Product category"},
                "stock": {"type": "integer", "description": "Initial stock quantity"},
            },
            "required": ["name", "price"],
        },
    },
    {
        "name": "get_product",
        "description": "Get a product by ID",
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
        "description": "List products with optional filters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Filter by category"},
                "status": {"type": "string", "description": "Filter by status"},
            },
        },
    },
    {
        "name": "search_products",
        "description": "Search products by name or description",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "update_inventory",
        "description": "Update product inventory quantity",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product ID"},
                "quantity": {"type": "integer", "description": "New stock quantity"},
            },
            "required": ["product_id", "quantity"],
        },
    },
    {
        "name": "create_order",
        "description": "Create a new order",
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
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
            "required": ["items"],
        },
    },
    {
        "name": "get_order",
        "description": "Get an order by ID",
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
        "description": "List orders with optional status filter",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status"},
            },
        },
    },
    {
        "name": "update_order_status",
        "description": "Update order status",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order ID"},
                "status": {"type": "string", "description": "New status (pending/confirmed/shipped/delivered/cancelled)"},
            },
            "required": ["order_id", "status"],
        },
    },
]


async def handle_request(
    raw: dict[str, Any], store: _CommerceStore | None = None
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
    request_id: Any, params: dict[str, Any], store: _CommerceStore
) -> dict[str, Any]:
    """Dispatch a tools/call request."""
    tool_name = params.get("name", "")
    args = params.get("arguments", {})

    handlers: dict[str, Any] = {
        "create_product": lambda: store.create_product(
            name=args["name"],
            price=args["price"],
            currency=args.get("currency", "usd"),
            description=args.get("description", ""),
            category=args.get("category", ""),
            stock=args.get("stock", 0),
        ),
        "get_product": lambda: store.get_product(args["product_id"]),
        "list_products": lambda: store.list_products(
            category=args.get("category", ""),
            status=args.get("status", ""),
        ),
        "search_products": lambda: store.search_products(args["query"]),
        "update_inventory": lambda: store.update_inventory(
            product_id=args["product_id"], quantity=args["quantity"]
        ),
        "create_order": lambda: store.create_order(
            items=args["items"],
            customer_email=args.get("customer_email", ""),
        ),
        "get_order": lambda: store.get_order(args["order_id"]),
        "list_orders": lambda: store.list_orders(status=args.get("status", "")),
        "update_order_status": lambda: store.update_order_status(
            order_id=args["order_id"], status=args["status"]
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

    logger.info("Commerce MCP server listening on stdio")

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
