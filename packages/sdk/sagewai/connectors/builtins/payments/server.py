# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Payment gateway MCP Server.

Exposes payment operations as MCP-compatible tools via JSON-RPC.
Uses an in-memory store (production: swap for Stripe / PayPal API).

Run via stdio::

    python -m mcp_payments
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
SERVER_NAME = "payments"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# In-memory payment store (production: swap for Stripe API)
# ---------------------------------------------------------------------------


class _PaymentStore:
    """Simple in-memory payment store for the MCP server."""

    def __init__(self) -> None:
        self.payments: dict[str, dict[str, Any]] = {}
        self.customers: dict[str, dict[str, Any]] = {}

    def create_payment_intent(
        self,
        amount: int,
        currency: str = "usd",
        customer_id: str | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        pid = f"pi_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        payment = {
            "id": pid,
            "amount": amount,
            "currency": currency.lower(),
            "customer_id": customer_id,
            "description": description,
            "status": "requires_capture",
            "created_at": now,
            "captured_at": None,
            "refunded_amount": 0,
        }
        self.payments[pid] = payment
        return payment

    def get_payment(self, payment_id: str) -> dict[str, Any] | None:
        return self.payments.get(payment_id)

    def list_payments(
        self, status: str = "", customer_id: str = ""
    ) -> list[dict[str, Any]]:
        results = list(self.payments.values())
        if status:
            results = [p for p in results if p["status"] == status]
        if customer_id:
            results = [p for p in results if p["customer_id"] == customer_id]
        return results

    def capture_payment(self, payment_id: str) -> dict[str, Any] | None:
        payment = self.payments.get(payment_id)
        if not payment:
            return None
        if payment["status"] != "requires_capture":
            return {"error": f"Cannot capture: status is '{payment['status']}'"}
        payment["status"] = "captured"
        payment["captured_at"] = datetime.now(timezone.utc).isoformat()
        return payment

    def refund_payment(
        self, payment_id: str, amount: int | None = None
    ) -> dict[str, Any] | None:
        payment = self.payments.get(payment_id)
        if not payment:
            return None
        if payment["status"] not in ("captured", "partially_refunded"):
            return {"error": f"Cannot refund: status is '{payment['status']}'"}
        refund_amount = amount or payment["amount"]
        max_refundable = payment["amount"] - payment["refunded_amount"]
        if refund_amount > max_refundable:
            return {"error": f"Refund {refund_amount} exceeds max {max_refundable}"}
        payment["refunded_amount"] += refund_amount
        if payment["refunded_amount"] >= payment["amount"]:
            payment["status"] = "refunded"
        else:
            payment["status"] = "partially_refunded"
        return payment

    def create_customer(self, name: str, email: str = "") -> dict[str, Any]:
        cid = f"cus_{uuid4().hex[:12]}"
        customer = {
            "id": cid,
            "name": name,
            "email": email,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.customers[cid] = customer
        return customer

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        return self.customers.get(customer_id)

    def list_customers(self) -> list[dict[str, Any]]:
        return list(self.customers.values())


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

def _make_store() -> _PaymentStore:
    return _PaymentStore()


_store = _make_store()


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


TOOLS = [
    {
        "name": "create_payment_intent",
        "description": "Create a new payment intent",
        "inputSchema": {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "integer",
                    "description": "Amount in smallest currency unit (e.g. cents)",
                },
                "currency": {"type": "string", "description": "ISO currency code (default: usd)"},
                "customer_id": {"type": "string", "description": "Customer ID"},
                "description": {"type": "string", "description": "Payment description"},
            },
            "required": ["amount"],
        },
    },
    {
        "name": "get_payment",
        "description": "Get a payment by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payment_id": {"type": "string", "description": "Payment ID"},
            },
            "required": ["payment_id"],
        },
    },
    {
        "name": "list_payments",
        "description": "List payments with optional filters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status"},
                "customer_id": {"type": "string", "description": "Filter by customer"},
            },
        },
    },
    {
        "name": "capture_payment",
        "description": "Capture an authorized payment",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payment_id": {"type": "string", "description": "Payment ID to capture"},
            },
            "required": ["payment_id"],
        },
    },
    {
        "name": "refund_payment",
        "description": "Refund a captured payment (full or partial)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payment_id": {"type": "string", "description": "Payment ID to refund"},
                "amount": {"type": "integer", "description": "Refund amount (default: full)"},
            },
            "required": ["payment_id"],
        },
    },
    {
        "name": "create_customer",
        "description": "Create a new customer",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Customer name"},
                "email": {"type": "string", "description": "Customer email"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_customer",
        "description": "Get a customer by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "Customer ID"},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "list_customers",
        "description": "List all customers",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


async def handle_request(
    raw: dict[str, Any], store: _PaymentStore | None = None
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
    request_id: Any, params: dict[str, Any], store: _PaymentStore
) -> dict[str, Any]:
    """Dispatch a tools/call request."""
    tool_name = params.get("name", "")
    args = params.get("arguments", {})

    handlers: dict[str, Any] = {
        "create_payment_intent": lambda: store.create_payment_intent(
            amount=args["amount"],
            currency=args.get("currency", "usd"),
            customer_id=args.get("customer_id"),
            description=args.get("description", ""),
        ),
        "get_payment": lambda: store.get_payment(args["payment_id"]),
        "list_payments": lambda: store.list_payments(
            status=args.get("status", ""),
            customer_id=args.get("customer_id", ""),
        ),
        "capture_payment": lambda: store.capture_payment(args["payment_id"]),
        "refund_payment": lambda: store.refund_payment(
            payment_id=args["payment_id"],
            amount=args.get("amount"),
        ),
        "create_customer": lambda: store.create_customer(
            name=args["name"], email=args.get("email", "")
        ),
        "get_customer": lambda: store.get_customer(args["customer_id"]),
        "list_customers": lambda: store.list_customers(),
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

    logger.info("Payments MCP server listening on stdio")

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
