# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""X (Twitter) MCP Server.

Exposes X/Twitter operations as MCP-compatible tools via JSON-RPC.
Uses an in-memory store (production: swap for X API v2).

Run via stdio::

    python -m sagewai.connectors.builtins.x.server
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
SERVER_NAME = "x"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# In-memory X store
# ---------------------------------------------------------------------------


class _XStore:
    """Simple in-memory X/Twitter store for the MCP server."""

    def __init__(self) -> None:
        self.tweets: list[dict[str, Any]] = []
        self.timeline: list[dict[str, Any]] = []

    def post_tweet(self, text: str, reply_to: str = "") -> dict[str, Any]:
        tweet_id = f"tw_{uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        tweet = {
            "id": tweet_id,
            "text": text,
            "created_at": now,
            "reply_to": reply_to or None,
            "metrics": {"likes": 0, "retweets": 0, "replies": 0},
        }
        self.tweets.append(tweet)
        return tweet

    def get_timeline(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.tweets[-limit:]

    def get_tweet(self, tweet_id: str) -> dict[str, Any] | None:
        for t in self.tweets:
            if t["id"] == tweet_id:
                return t
        return None

    def delete_tweet(self, tweet_id: str) -> bool:
        for i, t in enumerate(self.tweets):
            if t["id"] == tweet_id:
                self.tweets.pop(i)
                return True
        return False


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

_store = _XStore()


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


TOOLS = [
    {
        "name": "post_tweet",
        "description": "Post a new tweet",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Tweet text (max 280 characters)"},
                "reply_to": {"type": "string", "description": "Tweet ID to reply to"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "get_timeline",
        "description": "Get recent tweets from the timeline",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max tweets to return (default: 20)"},
            },
        },
    },
    {
        "name": "get_tweet",
        "description": "Get a specific tweet by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tweet_id": {"type": "string", "description": "Tweet ID"},
            },
            "required": ["tweet_id"],
        },
    },
    {
        "name": "delete_tweet",
        "description": "Delete a tweet by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tweet_id": {"type": "string", "description": "Tweet ID to delete"},
            },
            "required": ["tweet_id"],
        },
    },
]


async def handle_request(
    raw: dict[str, Any], store: _XStore | None = None
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
    request_id: Any, params: dict[str, Any], store: _XStore
) -> dict[str, Any]:
    """Dispatch a tools/call request."""
    tool_name = params.get("name", "")
    args = params.get("arguments", {})

    handlers: dict[str, Any] = {
        "post_tweet": lambda: store.post_tweet(
            text=args["text"],
            reply_to=args.get("reply_to", ""),
        ),
        "get_timeline": lambda: store.get_timeline(
            limit=args.get("limit", 20),
        ),
        "get_tweet": lambda: store.get_tweet(args["tweet_id"]),
        "delete_tweet": lambda: store.delete_tweet(args["tweet_id"]),
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

    logger.info("X MCP server listening on stdio")

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
