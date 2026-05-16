# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Slack MCP Server.

Exposes Slack workspace operations as MCP-compatible tools via JSON-RPC.
Uses an in-memory store (production: swap for Slack Web API).

Run via stdio::

    python -m mcp_slack
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
SERVER_NAME = "slack"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# In-memory Slack store
# ---------------------------------------------------------------------------


class _SlackStore:
    """Simple in-memory Slack store for the MCP server."""

    def __init__(self) -> None:
        self.channels: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, dict[str, Any]] = {}
        self.users: dict[str, dict[str, Any]] = {}

    def create_channel(
        self, name: str, topic: str = "", is_private: bool = False
    ) -> dict[str, Any]:
        cid = f"ch_{uuid4().hex[:12]}"
        channel = {
            "id": cid,
            "name": name,
            "topic": topic,
            "is_private": is_private,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.channels[cid] = channel
        return channel

    def get_channel(self, channel_id: str) -> dict[str, Any] | None:
        return self.channels.get(channel_id)

    def list_channels(self) -> list[dict[str, Any]]:
        return list(self.channels.values())

    def send_message(
        self,
        channel_id: str,
        text: str,
        user_id: str = "",
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        mid = f"msg_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        msg = {
            "id": mid,
            "channel_id": channel_id,
            "text": text,
            "user_id": user_id,
            "thread_ts": thread_ts,
            "ts": now,
        }
        self.messages[mid] = msg
        return msg

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        return self.messages.get(message_id)

    def list_messages(self, channel_id: str = "") -> list[dict[str, Any]]:
        results = list(self.messages.values())
        if channel_id:
            results = [m for m in results if m["channel_id"] == channel_id]
        return results

    def search_messages(self, query: str) -> list[dict[str, Any]]:
        q = query.lower()
        return [m for m in self.messages.values() if q in m["text"].lower()]

    def delete_message(self, message_id: str) -> bool:
        return self.messages.pop(message_id, None) is not None

    def add_user(self, name: str, email: str = "") -> dict[str, Any]:
        uid = f"usr_{uuid4().hex[:12]}"
        user = {
            "id": uid,
            "name": name,
            "email": email,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.users[uid] = user
        return user

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        return self.users.get(user_id)

    def list_users(self) -> list[dict[str, Any]]:
        return list(self.users.values())


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

def _make_store() -> _SlackStore:
    return _SlackStore()


_store = _make_store()


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


TOOLS = [
    {
        "name": "create_channel",
        "description": "Create a new Slack channel",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Channel name"},
                "topic": {"type": "string", "description": "Channel topic"},
                "is_private": {"type": "boolean", "description": "Private channel"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_channel",
        "description": "Get channel info by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Channel ID"},
            },
            "required": ["channel_id"],
        },
    },
    {
        "name": "list_channels",
        "description": "List all channels",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "send_message",
        "description": "Send a message to a channel",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Channel ID"},
                "text": {"type": "string", "description": "Message text"},
                "user_id": {"type": "string", "description": "Sender user ID"},
                "thread_ts": {"type": "string", "description": "Thread timestamp for replies"},
            },
            "required": ["channel_id", "text"],
        },
    },
    {
        "name": "get_message",
        "description": "Get a message by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Message ID"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "list_messages",
        "description": "List messages in a channel",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Filter by channel"},
            },
        },
    },
    {
        "name": "search_messages",
        "description": "Search messages by text content",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "delete_message",
        "description": "Delete a message",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Message ID"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "add_user",
        "description": "Add a user to the workspace",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "User display name"},
                "email": {"type": "string", "description": "User email"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_user",
        "description": "Get user info by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User ID"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "list_users",
        "description": "List all workspace users",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


async def handle_request(
    raw: dict[str, Any], store: _SlackStore | None = None
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
    request_id: Any, params: dict[str, Any], store: _SlackStore
) -> dict[str, Any]:
    """Dispatch a tools/call request."""
    tool_name = params.get("name", "")
    args = params.get("arguments", {})

    handlers: dict[str, Any] = {
        "create_channel": lambda: store.create_channel(
            name=args["name"],
            topic=args.get("topic", ""),
            is_private=args.get("is_private", False),
        ),
        "get_channel": lambda: store.get_channel(args["channel_id"]),
        "list_channels": lambda: store.list_channels(),
        "send_message": lambda: store.send_message(
            channel_id=args["channel_id"],
            text=args["text"],
            user_id=args.get("user_id", ""),
            thread_ts=args.get("thread_ts"),
        ),
        "get_message": lambda: store.get_message(args["message_id"]),
        "list_messages": lambda: store.list_messages(
            channel_id=args.get("channel_id", "")
        ),
        "search_messages": lambda: store.search_messages(args["query"]),
        "delete_message": lambda: store.delete_message(args["message_id"]),
        "add_user": lambda: store.add_user(
            name=args["name"], email=args.get("email", "")
        ),
        "get_user": lambda: store.get_user(args["user_id"]),
        "list_users": lambda: store.list_users(),
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

    logger.info("Slack MCP server listening on stdio")

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
