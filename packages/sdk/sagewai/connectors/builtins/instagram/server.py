# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Instagram MCP Server.

Exposes Instagram Graph API operations as MCP-compatible tools via JSON-RPC.
Uses an in-memory store (production: swap for Instagram Graph API).

Run via stdio::

    python -m sagewai.connectors.builtins.instagram.server
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
SERVER_NAME = "instagram"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# In-memory Instagram store
# ---------------------------------------------------------------------------


class _InstagramStore:
    """Simple in-memory Instagram store for the MCP server."""

    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.insights: dict[str, dict[str, Any]] = {}

    def publish_post(
        self,
        caption: str,
        image_url: str = "",
        media_type: str = "IMAGE",
    ) -> dict[str, Any]:
        post_id = f"ig_{uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        post = {
            "id": post_id,
            "caption": caption,
            "image_url": image_url,
            "media_type": media_type,
            "timestamp": now,
            "metrics": {"likes": 0, "comments": 0, "impressions": 0},
        }
        self.posts.append(post)
        return post

    def get_posts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.posts[-limit:]

    def get_post(self, post_id: str) -> dict[str, Any] | None:
        for p in self.posts:
            if p["id"] == post_id:
                return p
        return None

    def get_insights(self) -> dict[str, Any]:
        return {
            "total_posts": len(self.posts),
            "total_likes": sum(p["metrics"]["likes"] for p in self.posts),
            "total_comments": sum(p["metrics"]["comments"] for p in self.posts),
        }


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

_store = _InstagramStore()


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


TOOLS = [
    {
        "name": "publish_post",
        "description": "Publish a new Instagram post",
        "inputSchema": {
            "type": "object",
            "properties": {
                "caption": {"type": "string", "description": "Post caption"},
                "image_url": {"type": "string", "description": "Image URL to publish"},
                "media_type": {
                    "type": "string",
                    "description": "Media type (IMAGE, VIDEO, CAROUSEL)",
                },
            },
            "required": ["caption"],
        },
    },
    {
        "name": "get_posts",
        "description": "Get recent Instagram posts",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max posts to return (default: 20)"},
            },
        },
    },
    {
        "name": "get_post",
        "description": "Get a specific Instagram post by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string", "description": "Post ID"},
            },
            "required": ["post_id"],
        },
    },
    {
        "name": "get_insights",
        "description": "Get account insights and metrics",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


async def handle_request(
    raw: dict[str, Any], store: _InstagramStore | None = None
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
    request_id: Any, params: dict[str, Any], store: _InstagramStore
) -> dict[str, Any]:
    """Dispatch a tools/call request."""
    tool_name = params.get("name", "")
    args = params.get("arguments", {})

    handlers: dict[str, Any] = {
        "publish_post": lambda: store.publish_post(
            caption=args["caption"],
            image_url=args.get("image_url", ""),
            media_type=args.get("media_type", "IMAGE"),
        ),
        "get_posts": lambda: store.get_posts(limit=args.get("limit", 20)),
        "get_post": lambda: store.get_post(args["post_id"]),
        "get_insights": lambda: store.get_insights(),
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

    logger.info("Instagram MCP server listening on stdio")

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
