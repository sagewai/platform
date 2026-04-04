# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Email connector MCP Server.

Exposes email operations as MCP-compatible tools via JSON-RPC.
Uses an in-memory store (production: swap for Gmail / Outlook API).

Run via stdio::

    python -m mcp_email
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
SERVER_NAME = "email"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# In-memory email store (production: swap for Gmail / MS Graph API)
# ---------------------------------------------------------------------------


class _EmailStore:
    """Simple in-memory email store for the MCP server."""

    def __init__(self) -> None:
        self.emails: dict[str, dict[str, Any]] = {}

    def send_email(
        self,
        to: list[str],
        subject: str,
        body: str,
        from_addr: str = "me@example.com",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        mid = f"msg_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        email = {
            "id": mid,
            "from": from_addr,
            "to": to,
            "cc": cc or [],
            "bcc": bcc or [],
            "subject": subject,
            "body": body,
            "folder": "sent",
            "status": "read",
            "timestamp": now,
        }
        self.emails[mid] = email
        return email

    def get_email(self, email_id: str) -> dict[str, Any] | None:
        return self.emails.get(email_id)

    def list_emails(
        self, folder: str = "", status: str = ""
    ) -> list[dict[str, Any]]:
        results = list(self.emails.values())
        if folder:
            results = [e for e in results if e["folder"] == folder]
        if status:
            results = [e for e in results if e["status"] == status]
        return results

    def search_emails(self, query: str) -> list[dict[str, Any]]:
        q = query.lower()
        return [
            e
            for e in self.emails.values()
            if q in e["subject"].lower() or q in e["body"].lower()
        ]

    def delete_email(self, email_id: str) -> bool:
        if email_id in self.emails:
            del self.emails[email_id]
            return True
        return False

    def create_draft(
        self,
        to: list[str],
        subject: str,
        body: str,
        from_addr: str = "me@example.com",
    ) -> dict[str, Any]:
        did = f"draft_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        draft = {
            "id": did,
            "from": from_addr,
            "to": to,
            "cc": [],
            "bcc": [],
            "subject": subject,
            "body": body,
            "folder": "drafts",
            "status": "draft",
            "timestamp": now,
        }
        self.emails[did] = draft
        return draft

    def list_drafts(self) -> list[dict[str, Any]]:
        return [e for e in self.emails.values() if e["folder"] == "drafts"]

    def reply_to_email(
        self, email_id: str, body: str, from_addr: str = "me@example.com"
    ) -> dict[str, Any] | None:
        original = self.emails.get(email_id)
        if not original:
            return None
        return self.send_email(
            to=[original["from"]],
            subject=f"Re: {original['subject']}",
            body=body,
            from_addr=from_addr,
        )


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

def _make_store() -> _EmailStore:
    return _EmailStore()


_store = _make_store()


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


TOOLS = [
    {
        "name": "send_email",
        "description": "Send an email",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Recipient addresses",
                },
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body"},
                "cc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "CC recipients",
                },
                "bcc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "BCC recipients",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "get_email",
        "description": "Get an email by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "Email ID"},
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "list_emails",
        "description": "List emails with optional folder/status filters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "Filter by folder (inbox, sent, drafts)",
                },
                "status": {
                    "type": "string",
                    "description": "Filter by status (read, unread, draft)",
                },
            },
        },
    },
    {
        "name": "search_emails",
        "description": "Search emails by subject or body content",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "delete_email",
        "description": "Delete an email",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "Email ID to delete"},
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "create_draft",
        "description": "Create a draft email",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Recipient addresses",
                },
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "list_drafts",
        "description": "List all draft emails",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "reply_to_email",
        "description": "Reply to an existing email",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "Email to reply to"},
                "body": {"type": "string", "description": "Reply body"},
            },
            "required": ["email_id", "body"],
        },
    },
]


async def handle_request(
    raw: dict[str, Any], store: _EmailStore | None = None
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
    request_id: Any, params: dict[str, Any], store: _EmailStore
) -> dict[str, Any]:
    """Dispatch a tools/call request."""
    tool_name = params.get("name", "")
    args = params.get("arguments", {})

    handlers: dict[str, Any] = {
        "send_email": lambda: store.send_email(
            to=args["to"],
            subject=args["subject"],
            body=args["body"],
            cc=args.get("cc"),
            bcc=args.get("bcc"),
        ),
        "get_email": lambda: store.get_email(args["email_id"]),
        "list_emails": lambda: store.list_emails(
            folder=args.get("folder", ""),
            status=args.get("status", ""),
        ),
        "search_emails": lambda: store.search_emails(args["query"]),
        "delete_email": lambda: store.delete_email(args["email_id"]),
        "create_draft": lambda: store.create_draft(
            to=args["to"], subject=args["subject"], body=args["body"]
        ),
        "list_drafts": lambda: store.list_drafts(),
        "reply_to_email": lambda: store.reply_to_email(
            email_id=args["email_id"], body=args["body"]
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

    logger.info("Email MCP server listening on stdio")

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
