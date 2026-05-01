# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for MCP server hosting."""

from __future__ import annotations

import json
from typing import Any

import pytest

from sagewai.mcp.server import McpServer
from sagewai.models.tool import ToolSpec

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_request(method: str, params: dict[str, Any] | None = None, id: int = 1) -> dict:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": id, "method": method}
    if params:
        msg["params"] = params
    return msg


def _sync_add(a: int, b: int) -> str:
    return str(a + b)


async def _async_search(query: str) -> str:
    return f"Results for: {query}"


def _make_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="add",
            description="Add two numbers",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
            handler=_sync_add,
        ),
        ToolSpec(
            name="search",
            description="Search for something",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            handler=_async_search,
        ),
    ]


# ------------------------------------------------------------------
# Construction
# ------------------------------------------------------------------


def test_server_construction():
    server = McpServer(name="test", tools=_make_tools())
    assert server.name == "test"
    assert len(server.tools) == 2


def test_add_tool():
    server = McpServer(name="test")
    assert len(server.tools) == 0
    server.add_tool(_make_tools()[0])
    assert len(server.tools) == 1


def test_empty_server():
    server = McpServer(name="empty")
    assert len(server.tools) == 0


# ------------------------------------------------------------------
# Initialize handshake
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize():
    server = McpServer(name="test-server", tools=_make_tools())
    response = await server.handle_request(
        _make_request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
    )
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1

    result = response["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "test-server"
    assert "tools" in result["capabilities"]


# ------------------------------------------------------------------
# tools/list
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_list():
    server = McpServer(name="test", tools=_make_tools())
    response = await server.handle_request(_make_request("tools/list"))
    tools = response["result"]["tools"]

    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert names == {"add", "search"}

    add_tool = next(t for t in tools if t["name"] == "add")
    assert add_tool["description"] == "Add two numbers"
    assert add_tool["inputSchema"]["required"] == ["a", "b"]


@pytest.mark.asyncio
async def test_tools_list_empty():
    server = McpServer(name="empty")
    response = await server.handle_request(_make_request("tools/list"))
    assert response["result"]["tools"] == []


# ------------------------------------------------------------------
# tools/call — sync handler
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_call_sync():
    server = McpServer(name="test", tools=_make_tools())
    response = await server.handle_request(
        _make_request("tools/call", {"name": "add", "arguments": {"a": 2, "b": 3}})
    )
    result = response["result"]
    assert result["content"][0]["type"] == "text"
    assert result["content"][0]["text"] == "5"


# ------------------------------------------------------------------
# tools/call — async handler
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_call_async():
    server = McpServer(name="test", tools=_make_tools())
    response = await server.handle_request(
        _make_request("tools/call", {"name": "search", "arguments": {"query": "AI"}})
    )
    result = response["result"]
    assert result["content"][0]["text"] == "Results for: AI"


# ------------------------------------------------------------------
# tools/call — dict return
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_call_dict_return():
    def dict_tool() -> dict:
        return {"key": "value"}

    server = McpServer(
        name="test",
        tools=[ToolSpec(name="dict_tool", description="Returns dict", handler=dict_tool)],
    )
    response = await server.handle_request(
        _make_request("tools/call", {"name": "dict_tool", "arguments": {}})
    )
    text = response["result"]["content"][0]["text"]
    assert json.loads(text) == {"key": "value"}


# ------------------------------------------------------------------
# Error cases
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_method():
    server = McpServer(name="test")
    response = await server.handle_request(_make_request("unknown/method"))
    assert "error" in response
    assert response["error"]["code"] == -32601
    assert "Method not found" in response["error"]["message"]


@pytest.mark.asyncio
async def test_unknown_tool():
    server = McpServer(name="test", tools=_make_tools())
    response = await server.handle_request(
        _make_request("tools/call", {"name": "nonexistent", "arguments": {}})
    )
    assert "error" in response
    assert "Unknown tool" in response["error"]["message"]


@pytest.mark.asyncio
async def test_tool_handler_error():
    def failing_tool() -> str:
        raise ValueError("Tool crash")

    server = McpServer(
        name="test",
        tools=[ToolSpec(name="fail", description="Fails", handler=failing_tool)],
    )
    response = await server.handle_request(
        _make_request("tools/call", {"name": "fail", "arguments": {}})
    )
    assert "error" in response
    assert "Tool crash" in response["error"]["message"]


@pytest.mark.asyncio
async def test_tool_no_handler():
    server = McpServer(
        name="test",
        tools=[ToolSpec(name="no_handler", description="No handler")],
    )
    response = await server.handle_request(
        _make_request("tools/call", {"name": "no_handler", "arguments": {}})
    )
    assert "error" in response


# ------------------------------------------------------------------
# Ping
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping():
    server = McpServer(name="test")
    response = await server.handle_request(_make_request("ping"))
    assert response["result"] == {}


# ------------------------------------------------------------------
# Request ID passthrough
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_id_passthrough():
    server = McpServer(name="test")
    response = await server.handle_request(_make_request("ping", id=42))
    assert response["id"] == 42


# ------------------------------------------------------------------
# HTTP transport (ASGI app)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asgi_app_jsonrpc():
    """ASGI app handles JSON-RPC requests."""
    from starlette.testclient import TestClient

    server = McpServer(name="http-test", tools=_make_tools())
    app = server.as_asgi_app()

    with TestClient(app) as client:
        response = client.post("/", json=_make_request("tools/list"))
        assert response.status_code == 200
        data = response.json()
        assert len(data["result"]["tools"]) == 2


@pytest.mark.asyncio
async def test_asgi_app_health():
    """ASGI app has a health check endpoint."""
    from starlette.testclient import TestClient

    server = McpServer(name="health-test", tools=_make_tools())
    app = server.as_asgi_app()

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["server"] == "health-test"
        assert data["tools"] == 2


@pytest.mark.asyncio
async def test_asgi_app_tool_call():
    """ASGI app can call tools via JSON-RPC."""
    from starlette.testclient import TestClient

    server = McpServer(name="call-test", tools=_make_tools())
    app = server.as_asgi_app()

    with TestClient(app) as client:
        response = client.post(
            "/",
            json=_make_request("tools/call", {"name": "add", "arguments": {"a": 10, "b": 20}}),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"]["content"][0]["text"] == "30"
