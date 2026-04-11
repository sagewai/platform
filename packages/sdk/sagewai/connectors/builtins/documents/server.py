# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Document management MCP Server.

Exposes document CRUD operations as MCP-compatible tools via JSON-RPC.
Uses an in-memory store (production: swap for Google Drive / S3 API).

Run via stdio::

    python -m mcp_documents
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
SERVER_NAME = "documents"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# In-memory document store (production: swap for Google Drive / S3)
# ---------------------------------------------------------------------------


class _DocumentStore:
    """Simple in-memory document store for the MCP server."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.folders: dict[str, dict[str, Any]] = {}

    def create_document(
        self,
        title: str,
        content: str = "",
        folder: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        did = f"doc_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "id": did,
            "title": title,
            "content": content,
            "folder": folder,
            "tags": tags or [],
            "created_at": now,
            "updated_at": now,
        }
        self.documents[did] = doc
        return doc

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        return self.documents.get(document_id)

    def update_document(
        self,
        document_id: str,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any] | None:
        doc = self.documents.get(document_id)
        if not doc:
            return None
        if title is not None:
            doc["title"] = title
        if content is not None:
            doc["content"] = content
        if tags is not None:
            doc["tags"] = tags
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        return doc

    def delete_document(self, document_id: str) -> bool:
        if document_id in self.documents:
            del self.documents[document_id]
            return True
        return False

    def list_documents(
        self, folder: str = "", tag: str = ""
    ) -> list[dict[str, Any]]:
        results = list(self.documents.values())
        if folder:
            results = [d for d in results if d["folder"] == folder]
        if tag:
            results = [d for d in results if tag in d["tags"]]
        return results

    def search_documents(self, query: str) -> list[dict[str, Any]]:
        q = query.lower()
        return [
            d
            for d in self.documents.values()
            if q in d["title"].lower() or q in d["content"].lower()
        ]

    def create_folder(
        self, name: str, parent_id: str | None = None
    ) -> dict[str, Any]:
        fid = f"folder_{uuid4().hex[:12]}"
        folder = {
            "id": fid,
            "name": name,
            "parent_id": parent_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.folders[fid] = folder
        return folder

    def list_folders(self) -> list[dict[str, Any]]:
        return list(self.folders.values())


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

def _make_store() -> _DocumentStore:
    return _DocumentStore()


_store = _make_store()


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


TOOLS = [
    {
        "name": "create_document",
        "description": "Create a new document",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Document title"},
                "content": {"type": "string", "description": "Document content"},
                "folder": {"type": "string", "description": "Folder ID"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "get_document",
        "description": "Get a document by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Document ID"},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "update_document",
        "description": "Update a document's title, content, or tags",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Document ID"},
                "title": {"type": "string", "description": "New title"},
                "content": {"type": "string", "description": "New content"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New tags",
                },
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "delete_document",
        "description": "Delete a document",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Document ID"},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "list_documents",
        "description": "List documents with optional folder/tag filters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Filter by folder ID"},
                "tag": {"type": "string", "description": "Filter by tag"},
            },
        },
    },
    {
        "name": "search_documents",
        "description": "Search documents by title or content",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_folder",
        "description": "Create a new folder",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Folder name"},
                "parent_id": {"type": "string", "description": "Parent folder ID"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_folders",
        "description": "List all folders",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


async def handle_request(
    raw: dict[str, Any], store: _DocumentStore | None = None
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
    request_id: Any, params: dict[str, Any], store: _DocumentStore
) -> dict[str, Any]:
    """Dispatch a tools/call request."""
    tool_name = params.get("name", "")
    args = params.get("arguments", {})

    handlers: dict[str, Any] = {
        "create_document": lambda: store.create_document(
            title=args["title"],
            content=args.get("content", ""),
            folder=args.get("folder"),
            tags=args.get("tags"),
        ),
        "get_document": lambda: store.get_document(args["document_id"]),
        "update_document": lambda: store.update_document(
            document_id=args["document_id"],
            title=args.get("title"),
            content=args.get("content"),
            tags=args.get("tags"),
        ),
        "delete_document": lambda: store.delete_document(args["document_id"]),
        "list_documents": lambda: store.list_documents(
            folder=args.get("folder", ""),
            tag=args.get("tag", ""),
        ),
        "search_documents": lambda: store.search_documents(args["query"]),
        "create_folder": lambda: store.create_folder(
            name=args["name"], parent_id=args.get("parent_id")
        ),
        "list_folders": lambda: store.list_folders(),
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

    logger.info("Documents MCP server listening on stdio")

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
