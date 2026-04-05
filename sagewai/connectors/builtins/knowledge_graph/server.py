# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Knowledge Graph MCP Server.

Exposes knowledge graph CRUD operations as MCP-compatible tools via JSON-RPC.
Uses an in-memory graph store (can be swapped for NebulaGraph in production).

Run via stdio::

    python -m mcp_knowledge_graph

Or use as an ASGI app for HTTP hosting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "knowledge-graph"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# In-memory graph store (production: swap for NebulaGraph)
# ---------------------------------------------------------------------------


class _GraphStore:
    """Simple in-memory graph store for the MCP server."""

    def __init__(self) -> None:
        self.entities: dict[str, dict[str, Any]] = {}
        self.relations: list[dict[str, str]] = []

    def add_entity(self, name: str, entity_type: str = "", properties: dict[str, Any] | None = None) -> dict[str, Any]:
        self.entities[name] = {
            "name": name,
            "type": entity_type,
            "properties": properties or {},
        }
        return self.entities[name]

    def get_entity(self, name: str) -> dict[str, Any] | None:
        return self.entities.get(name)

    def list_entities(self, entity_type: str = "") -> list[dict[str, Any]]:
        entities = list(self.entities.values())
        if entity_type:
            entities = [e for e in entities if e["type"] == entity_type]
        return entities

    def delete_entity(self, name: str) -> bool:
        if name not in self.entities:
            return False
        del self.entities[name]
        self.relations = [
            r for r in self.relations
            if r["source"] != name and r["target"] != name
        ]
        return True

    def add_relation(self, source: str, relation: str, target: str) -> dict[str, str]:
        if source not in self.entities:
            self.add_entity(source)
        if target not in self.entities:
            self.add_entity(target)
        rel = {"source": source, "relation": relation, "target": target}
        self.relations.append(rel)
        return rel

    def get_relations(self, entity: str) -> list[dict[str, str]]:
        return [
            r for r in self.relations
            if r["source"] == entity or r["target"] == entity
        ]

    def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Search entities by name or property values."""
        query_lower = query.lower()
        results: list[dict[str, Any]] = []
        for entity in self.entities.values():
            if query_lower in entity["name"].lower():
                results.append(entity)
                continue
            for v in entity["properties"].values():
                if query_lower in str(v).lower():
                    results.append(entity)
                    break
        return results[:max_results]

    def get_neighbors(self, entity: str, max_depth: int = 2) -> dict[str, Any]:
        """Get entity and its neighborhood via BFS traversal."""
        if entity not in self.entities:
            return {"entity": None, "neighbors": [], "relations": []}

        visited: set[str] = set()
        neighbor_entities: list[dict[str, Any]] = []
        neighbor_relations: list[dict[str, str]] = []

        queue = [(entity, 0)]
        while queue:
            current, depth = queue.pop(0)
            if current in visited or depth > max_depth:
                continue
            visited.add(current)

            if current != entity and current in self.entities:
                neighbor_entities.append(self.entities[current])

            for rel in self.relations:
                if rel["source"] == current and rel["target"] not in visited:
                    neighbor_relations.append(rel)
                    queue.append((rel["target"], depth + 1))
                elif rel["target"] == current and rel["source"] not in visited:
                    neighbor_relations.append(rel)
                    queue.append((rel["source"], depth + 1))

        return {
            "entity": self.entities.get(entity),
            "neighbors": neighbor_entities,
            "relations": neighbor_relations,
        }


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

def _make_store() -> _GraphStore:
    return _GraphStore()


# Global graph store instance
_graph = _make_store()


def _jsonrpc_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


# Tool definitions
TOOLS = [
    {
        "name": "add_entity",
        "description": "Add an entity to the knowledge graph",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Entity name"},
                "entity_type": {"type": "string", "description": "Entity type (e.g. person, org, concept)"},
                "properties": {"type": "object", "description": "Additional properties"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_entity",
        "description": "Get an entity from the knowledge graph by name",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Entity name to look up"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_entities",
        "description": "List all entities, optionally filtered by type",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string", "description": "Filter by entity type"},
            },
        },
    },
    {
        "name": "delete_entity",
        "description": "Delete an entity and its relations from the knowledge graph",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Entity name to delete"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "add_relation",
        "description": "Add a relationship between two entities",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source entity name"},
                "relation": {"type": "string", "description": "Relationship type"},
                "target": {"type": "string", "description": "Target entity name"},
            },
            "required": ["source", "relation", "target"],
        },
    },
    {
        "name": "get_relations",
        "description": "Get all relations for an entity",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Entity name"},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "search",
        "description": "Search entities by name or property values",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Maximum results (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_neighbors",
        "description": "Get an entity and its neighborhood via graph traversal",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Entity name"},
                "max_depth": {"type": "integer", "description": "Max traversal depth (default 2)"},
            },
            "required": ["entity"],
        },
    },
]


async def handle_request(raw: dict[str, Any], graph: _GraphStore | None = None) -> dict[str, Any]:
    """Process a single MCP JSON-RPC request."""
    store = graph or _graph
    request_id = raw.get("id")
    method = raw.get("method", "")
    params = raw.get("params", {})

    if method == "initialize":
        return _jsonrpc_response(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "ping":
        return _jsonrpc_response(request_id, {})

    if method == "tools/list":
        return _jsonrpc_response(request_id, {"tools": TOOLS})

    if method == "tools/call":
        return await _handle_tool_call(request_id, params, store)

    return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")


async def _handle_tool_call(
    request_id: Any, params: dict[str, Any], store: _GraphStore
) -> dict[str, Any]:
    """Dispatch a tools/call request to the appropriate handler."""
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    handlers = {
        "add_entity": lambda: store.add_entity(
            name=arguments["name"],
            entity_type=arguments.get("entity_type", ""),
            properties=arguments.get("properties"),
        ),
        "get_entity": lambda: store.get_entity(arguments["name"]),
        "list_entities": lambda: store.list_entities(arguments.get("entity_type", "")),
        "delete_entity": lambda: store.delete_entity(arguments["name"]),
        "add_relation": lambda: store.add_relation(
            source=arguments["source"],
            relation=arguments["relation"],
            target=arguments["target"],
        ),
        "get_relations": lambda: store.get_relations(arguments["entity"]),
        "search": lambda: store.search(
            query=arguments["query"],
            max_results=arguments.get("max_results", 10),
        ),
        "get_neighbors": lambda: store.get_neighbors(
            entity=arguments["entity"],
            max_depth=arguments.get("max_depth", 2),
        ),
    }

    handler = handlers.get(tool_name)
    if not handler:
        return _jsonrpc_error(request_id, -32602, f"Unknown tool: {tool_name}")

    try:
        result = handler()
        content = json.dumps(result, default=str)
        return _jsonrpc_response(request_id, {
            "content": [{"type": "text", "text": content}],
        })
    except Exception as exc:
        logger.exception("Tool call error: %s", tool_name)
        return _jsonrpc_error(request_id, -32000, str(exc))


async def run_stdio() -> None:
    """Run the MCP server over stdin/stdout."""
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(
        writer_transport, writer_protocol, None, asyncio.get_event_loop()
    )

    logger.info("Knowledge Graph MCP server listening on stdio")

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
