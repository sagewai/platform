# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""WhatsApp MCP Server.

Exposes WhatsApp Business API operations as MCP-compatible tools via JSON-RPC.
Uses an in-memory store (production: swap for WhatsApp Cloud API).

Run via stdio::

    python -m sagewai.connectors.builtins.whatsapp.server
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
SERVER_NAME = "whatsapp"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# In-memory WhatsApp store
# ---------------------------------------------------------------------------


class _WhatsAppStore:
    """Simple in-memory WhatsApp store for the MCP server."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.contacts: dict[str, dict[str, Any]] = {}

    def send_message(
        self,
        to: str,
        body: str,
        message_type: str = "text",
    ) -> dict[str, Any]:
        msg_id = f"wamid.{uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        msg = {
            "id": msg_id,
            "to": to,
            "type": message_type,
            "body": body,
            "status": "sent",
            "timestamp": now,
        }
        self.messages.append(msg)
        if to not in self.contacts:
            self.contacts[to] = {"phone": to, "name": "", "added_at": now}
        return msg

    def get_messages(
        self, phone: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        results = self.messages
        if phone:
            results = [m for m in results if m["to"] == phone]
        return results[-limit:]

    def get_contacts(self) -> list[dict[str, Any]]:
        return list(self.contacts.values())


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

_store = _WhatsAppStore()


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


TOOLS = [
    {
        "name": "send_message",
        "description": "Send a WhatsApp message to a phone number",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient phone number (E.164 format)"},
                "body": {"type": "string", "description": "Message body text"},
                "message_type": {"type": "string", "description": "Message type (default: text)"},
            },
            "required": ["to", "body"],
        },
    },
    {
        "name": "get_messages",
        "description": "Get recent messages, optionally filtered by phone number",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Filter by phone number"},
                "limit": {"type": "integer", "description": "Max messages to return (default: 20)"},
            },
        },
    },
    {
        "name": "get_contacts",
        "description": "List all known contacts",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


async def handle_request(
    raw: dict[str, Any], store: _WhatsAppStore | None = None
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
    request_id: Any, params: dict[str, Any], store: _WhatsAppStore
) -> dict[str, Any]:
    """Dispatch a tools/call request."""
    tool_name = params.get("name", "")
    args = params.get("arguments", {})

    handlers: dict[str, Any] = {
        "send_message": lambda: store.send_message(
            to=args["to"],
            body=args["body"],
            message_type=args.get("message_type", "text"),
        ),
        "get_messages": lambda: store.get_messages(
            phone=args.get("phone", ""),
            limit=args.get("limit", 20),
        ),
        "get_contacts": lambda: store.get_contacts(),
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

    logger.info("WhatsApp MCP server listening on stdio")

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
