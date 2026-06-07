# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for MCP client transports."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.mcp.client import McpClient, _SseTransport, _StreamableHttpTransport


def _mock_response(data=None, text=None, headers=None, status_code=200):
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.headers = headers or {}
    if data is not None:
        resp.json.return_value = data
    if text is not None:
        resp.text = text
    return resp


# ------------------------------------------------------------------
# _StreamableHttpTransport unit tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streamable_http_basic_request():
    """StreamableHttpTransport sends JSON-RPC and parses JSON response."""
    transport = _StreamableHttpTransport("http://localhost:3000/mcp")
    resp = _mock_response(
        data={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}},
        headers={"content-type": "application/json"},
    )
    transport._client.post = AsyncMock(return_value=resp)

    result = await transport.request("initialize", {"protocolVersion": "2025-03-26"})
    assert result["protocolVersion"] == "2025-03-26"
    await transport.close()


@pytest.mark.asyncio
async def test_streamable_http_sse_response():
    """StreamableHttpTransport parses SSE-streamed responses."""
    transport = _StreamableHttpTransport("http://localhost:3000/mcp")
    sse_body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
    resp = _mock_response(
        text=sse_body,
        headers={"content-type": "text/event-stream"},
    )
    transport._client.post = AsyncMock(return_value=resp)

    result = await transport.request("tools/list")
    assert result == {"tools": []}
    await transport.close()


@pytest.mark.asyncio
async def test_streamable_http_session_id():
    """StreamableHttpTransport captures and reuses Mcp-Session-Id."""
    transport = _StreamableHttpTransport("http://localhost:3000/mcp")

    resp1 = _mock_response(
        data={"jsonrpc": "2.0", "id": 1, "result": {}},
        headers={"content-type": "application/json", "mcp-session-id": "test-session-123"},
    )
    resp2 = _mock_response(
        data={"jsonrpc": "2.0", "id": 2, "result": {"tools": []}},
        headers={"content-type": "application/json"},
    )
    transport._client.post = AsyncMock(side_effect=[resp1, resp2])

    await transport.request("initialize")
    assert transport._session_id == "test-session-123"

    await transport.request("tools/list")
    # Second call should include session id in headers
    second_call = transport._client.post.call_args_list[1]
    assert second_call.kwargs["headers"]["Mcp-Session-Id"] == "test-session-123"
    await transport.close()


@pytest.mark.asyncio
async def test_streamable_http_error_response():
    """StreamableHttpTransport raises on JSON-RPC error."""
    transport = _StreamableHttpTransport("http://localhost:3000/mcp")
    resp = _mock_response(
        data={
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        },
        headers={"content-type": "application/json"},
    )
    transport._client.post = AsyncMock(return_value=resp)

    with pytest.raises(RuntimeError, match="MCP error"):
        await transport.request("unknown/method")
    await transport.close()


@pytest.mark.asyncio
async def test_streamable_http_custom_headers():
    """StreamableHttpTransport sends custom headers."""
    transport = _StreamableHttpTransport(
        "http://localhost:3000/mcp",
        headers={"Authorization": "Bearer test-token"},
    )
    resp = _mock_response(
        data={"jsonrpc": "2.0", "id": 1, "result": {}},
        headers={"content-type": "application/json"},
    )
    transport._client.post = AsyncMock(return_value=resp)

    await transport.request("initialize")
    # Custom header should be in the client's initial headers
    assert transport._headers["Authorization"] == "Bearer test-token"
    await transport.close()


@pytest.mark.asyncio
async def test_streamable_http_sse_error_in_stream():
    """StreamableHttpTransport raises on SSE error response."""
    transport = _StreamableHttpTransport("http://localhost:3000/mcp")
    sse_body = 'data: {"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"fail"}}\n\n'
    resp = _mock_response(
        text=sse_body,
        headers={"content-type": "text/event-stream"},
    )
    transport._client.post = AsyncMock(return_value=resp)

    with pytest.raises(RuntimeError, match="MCP error"):
        await transport.request("tools/call", {"name": "bad"})
    await transport.close()


# ------------------------------------------------------------------
# _SseTransport unit tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_transport_request():
    """SseTransport sends JSON-RPC via POST."""
    transport = _SseTransport("http://localhost:3000/mcp")
    resp = _mock_response(data={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
    transport._client.post = AsyncMock(return_value=resp)

    result = await transport.request("tools/list")
    assert result == {"tools": []}
    await transport.close()


@pytest.mark.asyncio
async def test_sse_transport_error():
    """SseTransport raises on JSON-RPC error."""
    transport = _SseTransport("http://localhost:3000/mcp")
    resp = _mock_response(
        data={"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Invalid"}}
    )
    transport._client.post = AsyncMock(return_value=resp)

    with pytest.raises(RuntimeError, match="MCP error"):
        await transport.request("bad")
    await transport.close()


# ------------------------------------------------------------------
# McpClient.connect_http integration test
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_http_discovers_tools():
    """connect_http performs handshake and discovers tools."""
    init_resp = _mock_response(
        data={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}},
        headers={"content-type": "application/json"},
    )
    tools_resp = _mock_response(
        data={
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {
                        "name": "get_weather",
                        "description": "Get current weather",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    }
                ]
            },
        },
        headers={"content-type": "application/json"},
    )

    with patch("sagewai.mcp.client.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=[init_resp, tools_resp])
        mock_client.aclose = AsyncMock()
        mock_client_cls.return_value = mock_client

        tools = await McpClient.connect_http("http://localhost:3000/mcp")

    assert len(tools) == 1
    assert tools[0].name == "get_weather"
    assert tools[0].description == "Get current weather"
    assert "city" in tools[0].parameters["properties"]


@pytest.mark.asyncio
async def test_connect_http_empty_tools():
    """connect_http returns empty list when no tools."""
    init_resp = _mock_response(
        data={"jsonrpc": "2.0", "id": 1, "result": {}},
        headers={"content-type": "application/json"},
    )
    tools_resp = _mock_response(
        data={"jsonrpc": "2.0", "id": 2, "result": {"tools": []}},
        headers={"content-type": "application/json"},
    )

    with patch("sagewai.mcp.client.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=[init_resp, tools_resp])
        mock_client.aclose = AsyncMock()
        mock_client_cls.return_value = mock_client

        tools = await McpClient.connect_http("http://localhost:3000/mcp")

    assert tools == []


# ------------------------------------------------------------------
# Host-exec policy guard for stdio MCP transports (#10B bypass)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_mcp_connect_refused_when_host_exec_disabled(monkeypatch):
    """connect() must raise before spawning any subprocess when host-exec is denied."""
    monkeypatch.delenv("SAGEWAI_ALLOW_HOST_EXEC", raising=False)
    with pytest.raises(RuntimeError, match="Host-backed execution disabled"):
        await McpClient.connect("echo hello")


@pytest.mark.asyncio
async def test_stdio_mcp_connect_managed_refused_when_host_exec_disabled(monkeypatch):
    """connect_managed() must raise before spawning any subprocess when host-exec is denied."""
    monkeypatch.delenv("SAGEWAI_ALLOW_HOST_EXEC", raising=False)
    with pytest.raises(RuntimeError, match="Host-backed execution disabled"):
        await McpClient.connect_managed(["echo", "hello"])


@pytest.mark.asyncio
async def test_stdio_mcp_connect_allowed_when_host_exec_enabled(monkeypatch):
    """connect() proceeds past the guard (and fails on MCP handshake) when flag is set."""
    monkeypatch.setenv("SAGEWAI_ALLOW_HOST_EXEC", "1")
    # 'echo hello' is not a real MCP server — we expect a ConnectionError or
    # similar from the handshake attempt, NOT the "Host-backed execution disabled" error.
    with pytest.raises(Exception) as exc_info:
        await McpClient.connect("echo hello")
    assert "Host-backed execution disabled" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_stdio_mcp_sse_unaffected_by_host_exec_guard(monkeypatch):
    """connect_sse() (network transport) is NOT gated on host_exec_allowed."""
    monkeypatch.delenv("SAGEWAI_ALLOW_HOST_EXEC", raising=False)
    # Should fail with a network/connection error, not the host-exec RuntimeError.
    with pytest.raises(Exception) as exc_info:
        await McpClient.connect_sse("http://127.0.0.1:19999/mcp")
    assert "Host-backed execution disabled" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_stdio_mcp_connect_http_unaffected_by_host_exec_guard(monkeypatch):
    """connect_http() (network transport) is NOT gated on host_exec_allowed."""
    monkeypatch.delenv("SAGEWAI_ALLOW_HOST_EXEC", raising=False)
    # Should fail with a network/connection error, not the host-exec RuntimeError.
    with pytest.raises(Exception) as exc_info:
        await McpClient.connect_http("http://127.0.0.1:19999/mcp")
    assert "Host-backed execution disabled" not in str(exc_info.value)
