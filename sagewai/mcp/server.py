# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""MCP Server — Expose agent tools as an MCP-compliant server.

Wraps a list of :class:`ToolSpec` objects and serves them via the
MCP (Model Context Protocol) JSON-RPC interface.

Supports two transports:
- **Stdio**: reads/writes JSON-RPC over stdin/stdout (for CLI tools)
- **HTTP**: a Starlette/FastAPI-compatible ASGI app

Usage::

    from sagewai.mcp.server import McpServer

    server = McpServer(name="my-tools", tools=agent.config.tools)

    # Stdio (blocking — run as main process)
    await server.run_stdio()

    # Or get an ASGI app for HTTP hosting
    app = server.as_asgi_app()
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import TYPE_CHECKING, Any

from sagewai.models.tool import ToolSpec

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent

logger = logging.getLogger(__name__)

SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


def _jsonrpc_response(id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _jsonrpc_error(id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


class McpServer:
    """MCP server that exposes ToolSpecs via JSON-RPC.

    Args:
        name: Server name reported in the MCP handshake.
        tools: List of ToolSpecs to expose as MCP tools.
        version: Server version string.
    """

    def __init__(
        self,
        name: str,
        tools: list[ToolSpec] | None = None,
        version: str = SERVER_VERSION,
    ) -> None:
        self.name = name
        self.version = version
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools or []:
            self.add_tool(tool)

    def add_tool(self, tool: ToolSpec) -> None:
        """Register a tool to be served via MCP."""
        self._tools[tool.name] = tool

    @property
    def tools(self) -> list[ToolSpec]:
        """All registered tools."""
        return list(self._tools.values())

    @classmethod
    def from_agent(cls, agent: "BaseAgent") -> "McpServer":
        """Create an MCP server that exposes an agent's capabilities as tools.

        Generates:
        - A 'chat' tool (always present) -- sends a message to the agent
        - One tool per agent tool -- mirrors the agent's tool registry
        """
        server = cls(name=agent.config.name, tools=[])

        # Chat tool -- always present
        async def _chat_handler(message: str) -> str:
            return await agent.chat(message)

        chat_tool = ToolSpec(
            name="chat",
            description=f"Send a message to the {agent.config.name} agent and get a response.",
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The message to send"},
                },
                "required": ["message"],
            },
            handler=_chat_handler,
        )
        server.add_tool(chat_tool)

        # Mirror agent tools
        for tool in agent.config.tools:
            server.add_tool(tool)

        return server

    # ------------------------------------------------------------------
    # JSON-RPC dispatch
    # ------------------------------------------------------------------

    async def handle_request(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Process a single JSON-RPC request and return the response."""
        request_id = raw.get("id")
        method = raw.get("method", "")
        params = raw.get("params", {})

        handler = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "ping": self._handle_ping,
        }.get(method)

        if handler is None:
            return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")

        try:
            result = await handler(params)
            return _jsonrpc_response(request_id, result)
        except Exception as exc:
            logger.exception("Error handling MCP request %s", method)
            return _jsonrpc_error(request_id, -32000, str(exc))

    async def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": self.name, "version": self.version},
        }

    async def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        tools_list = []
        for tool in self._tools.values():
            entry: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
            }
            if tool.parameters:
                entry["inputSchema"] = tool.parameters
            tools_list.append(entry)
        return {"tools": tools_list}

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        spec = self._tools.get(tool_name)
        if spec is None:
            raise ValueError(f"Unknown tool: {tool_name}")

        if spec.handler is None:
            raise ValueError(f"Tool {tool_name} has no handler")

        result = spec.handler(**arguments)
        if asyncio.iscoroutine(result):
            result = await result

        content = result if isinstance(result, str) else json.dumps(result)
        return {
            "content": [{"type": "text", "text": content}],
        }

    async def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    # ------------------------------------------------------------------
    # Stdio transport
    # ------------------------------------------------------------------

    async def run_stdio(self) -> None:
        """Run the MCP server over stdin/stdout.

        Reads JSON-RPC requests line-by-line from stdin and writes
        responses to stdout.  Blocks until stdin is closed.
        """
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
        writer = asyncio.StreamWriter(
            writer_transport, writer_protocol, None, asyncio.get_event_loop()
        )

        logger.info("MCP server '%s' listening on stdio", self.name)

        while True:
            line = await reader.readline()
            if not line:
                break

            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            response = await self.handle_request(raw)
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()

    # ------------------------------------------------------------------
    # HTTP transport (ASGI)
    # ------------------------------------------------------------------

    def as_asgi_app(self) -> Any:
        """Return a Starlette ASGI app serving this MCP server.

        Requires ``starlette`` or ``fastapi`` to be installed.

        The app exposes:
        - ``POST /`` — JSON-RPC endpoint
        - ``GET /health`` — health check
        """
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        server = self

        async def jsonrpc_endpoint(request: Request) -> JSONResponse:
            body = await request.json()
            response = await server.handle_request(body)
            return JSONResponse(response)

        async def health(request: Request) -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "server": server.name,
                    "version": server.version,
                    "tools": len(server._tools),
                }
            )

        return Starlette(
            routes=[
                Route("/", jsonrpc_endpoint, methods=["POST"]),
                Route("/health", health, methods=["GET"]),
            ],
        )
