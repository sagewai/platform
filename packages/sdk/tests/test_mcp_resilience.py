# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for MCP client crash recovery and resilient connections."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from sagewai.mcp.client import (
    McpConnection,
    ResilientMcpConnection,
    _ProxiedTransport,
    _StdioTransport,
)
from sagewai.models.tool import ToolSpec


# ─── Helpers ───


class FakeTransport:
    """Fake transport that can be made to crash on demand."""

    def __init__(self, tools: list[dict] | None = None) -> None:
        self._tools = tools or [
            {"name": "greet", "description": "Say hello", "inputSchema": {}},
        ]
        self._alive = True
        self._request_id = 0
        self._call_log: list[tuple[str, dict | None]] = []

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._call_log.append((method, params))
        if not self._alive:
            raise ConnectionError("MCP server closed connection")
        if method == "initialize":
            return {"protocolVersion": "2024-11-05", "capabilities": {}}
        if method == "tools/list":
            return {"tools": self._tools}
        if method == "tools/call":
            name = params["name"] if params else "unknown"
            return {"content": [{"text": f"result from {name}"}]}
        return None

    async def close(self) -> None:
        self._alive = False

    def kill(self) -> None:
        """Simulate subprocess crash."""
        self._alive = False


# ─── _ProxiedTransport tests ───


class TestProxiedTransport:
    @pytest.mark.asyncio
    async def test_request_delegates_to_inner(self):
        proxy = _ProxiedTransport()
        fake = FakeTransport()
        proxy.inner = fake

        result = await proxy.request("tools/list")
        assert result["tools"][0]["name"] == "greet"

    @pytest.mark.asyncio
    async def test_request_raises_when_not_connected(self):
        proxy = _ProxiedTransport()
        with pytest.raises(ConnectionError, match="not connected"):
            await proxy.request("tools/list")

    @pytest.mark.asyncio
    async def test_close_nullifies_inner(self):
        proxy = _ProxiedTransport()
        fake = FakeTransport()
        proxy.inner = fake

        await proxy.close()
        assert proxy.inner is None

    @pytest.mark.asyncio
    async def test_close_when_already_none(self):
        proxy = _ProxiedTransport()
        await proxy.close()  # should not raise

    @pytest.mark.asyncio
    async def test_swap_inner_after_crash(self):
        """After crash, swapping .inner to a new transport resumes requests."""
        proxy = _ProxiedTransport()
        old = FakeTransport()
        proxy.inner = old

        # Verify it works
        result = await proxy.request("tools/list")
        assert result is not None

        # Crash
        old.kill()
        with pytest.raises(ConnectionError):
            await proxy.request("tools/list")

        # Swap to new transport
        new = FakeTransport()
        proxy.inner = new
        result = await proxy.request("tools/list")
        assert result["tools"][0]["name"] == "greet"


# ─── ResilientMcpConnection tests ───


def _make_tool(name: str, transport: Any) -> ToolSpec:
    """Create a ToolSpec whose handler calls through the given transport."""

    async def handler(**kwargs: Any) -> str:
        result = await transport.request(
            "tools/call", {"name": name, "arguments": kwargs}
        )
        if isinstance(result, dict):
            content = result.get("content", [])
            if isinstance(content, list):
                texts = [c.get("text", str(c)) for c in content if isinstance(c, dict)]
                return "\n".join(texts)
        return str(result)

    return ToolSpec(
        name=name,
        description=f"Tool {name}",
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )


class TestResilientMcpConnection:
    @pytest.mark.asyncio
    async def test_tools_property(self):
        fake = FakeTransport()
        proxy = _ProxiedTransport()
        proxy.inner = fake
        tool = _make_tool("greet", proxy)
        conn = McpConnection(tools=[tool], transport=fake, proxy=proxy)
        resilient = ResilientMcpConnection(inner=conn)

        assert len(resilient.tools) == 1
        assert resilient.tools[0].name == "greet"

    @pytest.mark.asyncio
    async def test_close_cleans_up(self):
        fake = FakeTransport()
        proxy = _ProxiedTransport()
        proxy.inner = fake
        tool = _make_tool("greet", proxy)
        conn = McpConnection(tools=[tool], transport=fake, proxy=proxy)
        resilient = ResilientMcpConnection(inner=conn)

        await resilient.close()
        assert proxy.inner is None

    @pytest.mark.asyncio
    async def test_reconnect_swaps_transport(self):
        """After crash, reconnect() swaps the proxy inner to a fresh transport."""
        old_transport = FakeTransport()
        proxy = _ProxiedTransport()
        proxy.inner = old_transport
        tool = _make_tool("greet", proxy)
        conn = McpConnection(tools=[tool], transport=old_transport, proxy=proxy)

        new_transport = FakeTransport()
        new_proxy = _ProxiedTransport()
        new_proxy.inner = new_transport
        new_conn = McpConnection(
            tools=[_make_tool("greet", new_proxy)],
            transport=new_transport,
            proxy=new_proxy,
        )

        reconnect_fn = AsyncMock(return_value=new_conn)
        resilient = ResilientMcpConnection(
            inner=conn, reconnect_fn=reconnect_fn
        )

        # Simulate crash
        old_transport.kill()
        with pytest.raises(ConnectionError):
            await proxy.request("tools/list")

        # Reconnect
        await resilient.reconnect()
        reconnect_fn.assert_awaited_once()

        # The proxy now points at the new transport
        assert proxy.inner is new_transport
        result = await proxy.request("tools/list")
        assert result["tools"][0]["name"] == "greet"

    @pytest.mark.asyncio
    async def test_reconnect_without_fn_raises(self):
        fake = FakeTransport()
        conn = McpConnection(tools=[], transport=fake)
        resilient = ResilientMcpConnection(inner=conn)

        with pytest.raises(ConnectionError, match="No reconnect function"):
            await resilient.reconnect()

    @pytest.mark.asyncio
    async def test_reconnect_count_increments(self):
        old = FakeTransport()
        proxy = _ProxiedTransport()
        proxy.inner = old
        conn = McpConnection(tools=[], transport=old, proxy=proxy)

        new_transport = FakeTransport()
        new_proxy = _ProxiedTransport()
        new_proxy.inner = new_transport
        new_conn = McpConnection(tools=[], transport=new_transport, proxy=new_proxy)
        reconnect_fn = AsyncMock(return_value=new_conn)

        resilient = ResilientMcpConnection(inner=conn, reconnect_fn=reconnect_fn)
        assert resilient._reconnect_count == 0

        await resilient.reconnect()
        assert resilient._reconnect_count == 1

    @pytest.mark.asyncio
    async def test_tool_handler_survives_reconnect(self):
        """The key test: a tool handler bound to a proxy works after reconnect."""
        # Initial connection
        transport_v1 = FakeTransport()
        proxy = _ProxiedTransport()
        proxy.inner = transport_v1
        tool = _make_tool("greet", proxy)
        conn = McpConnection(tools=[tool], transport=transport_v1, proxy=proxy)

        # Verify tool works
        result = await tool.handler()
        assert "result from greet" in result

        # Crash
        transport_v1.kill()
        with pytest.raises(ConnectionError):
            await tool.handler()

        # Reconnect — swap proxy inner
        transport_v2 = FakeTransport()
        new_proxy = _ProxiedTransport()
        new_proxy.inner = transport_v2
        new_conn = McpConnection(
            tools=[_make_tool("greet", new_proxy)],
            transport=transport_v2,
            proxy=new_proxy,
        )
        reconnect_fn = AsyncMock(return_value=new_conn)
        resilient = ResilientMcpConnection(
            inner=conn, reconnect_fn=reconnect_fn
        )
        await resilient.reconnect()

        # Same tool handler, same proxy — now works again
        result = await tool.handler()
        assert "result from greet" in result
        # Calls went to transport_v2, not transport_v1
        assert any(
            m == "tools/call" for m, _ in transport_v2._call_log
        )


# ─── Integration: handler closures bind to proxy ───


class TestHandlerProxyBinding:
    @pytest.mark.asyncio
    async def test_handler_uses_proxy_not_raw_transport(self):
        """Handlers should call through proxy, not the raw transport."""
        proxy = _ProxiedTransport()
        raw = FakeTransport()
        proxy.inner = raw

        tool = _make_tool("test_tool", proxy)
        result = await tool.handler(msg="hello")
        assert "result from test_tool" in result

        # The call went through proxy → raw
        assert len(raw._call_log) == 1
        assert raw._call_log[0][0] == "tools/call"
