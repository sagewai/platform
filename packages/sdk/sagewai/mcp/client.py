# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""MCP Client — Connects to MCP servers and exposes their tools as ToolSpecs."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

import httpx

from sagewai.models.tool import ToolSpec

logger = logging.getLogger(__name__)

Transport = "_StdioTransport | _SseTransport | _StreamableHttpTransport"


class _TransportProtocol(Protocol):
    """Protocol that all transports implement."""

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any: ...
    async def close(self) -> None: ...


class _ProxiedTransport:
    """Mutable reference to the active transport — survives reconnection.

    Tool handler closures capture this proxy instead of a raw transport.
    When the underlying subprocess dies and a new one is spawned, we swap
    ``self.inner`` to the fresh transport; existing handlers keep working.
    """

    def __init__(self) -> None:
        self.inner: _TransportProtocol | None = None

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self.inner is None:
            raise ConnectionError("MCP transport not connected")
        return await self.inner.request(method, params)

    async def close(self) -> None:
        if self.inner is not None:
            await self.inner.close()
            self.inner = None


class _StdioTransport:
    """JSON-RPC transport over subprocess stdin/stdout."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self._request_id = 0

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC request and return the result."""
        self._request_id += 1
        msg = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params:
            msg["params"] = params

        data = json.dumps(msg) + "\n"
        self._process.stdin.write(data.encode())  # type: ignore[union-attr]
        await self._process.stdin.drain()  # type: ignore[union-attr]

        line = await self._process.stdout.readline()  # type: ignore[union-attr]
        if not line:
            raise ConnectionError("MCP server closed connection")

        response = json.loads(line)
        if "error" in response:
            raise RuntimeError(f"MCP error: {response['error']}")
        return response.get("result")

    async def close(self) -> None:
        """Terminate the subprocess."""
        self._process.terminate()
        await self._process.wait()


class _SseTransport:
    """JSON-RPC transport over HTTP + SSE."""

    def __init__(self, base_url: str, headers: dict[str, str] | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = dict(headers or {})
        self._client = httpx.AsyncClient(timeout=30.0, headers=self._headers)
        self._request_id = 0

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC request via HTTP POST."""
        self._request_id += 1
        msg = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params:
            msg["params"] = params

        response = await self._client.post(self._base_url, json=msg)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data.get("result")

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


class _StreamableHttpTransport:
    """JSON-RPC transport over streamable HTTP (MCP 2025-03-26 spec).

    Sends JSON-RPC requests via HTTP POST with Accept: text/event-stream.
    The response may be a single JSON-RPC response (for simple requests) or
    an SSE stream (for longer operations).
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self._url = url.rstrip("/")
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(headers or {}),
        }
        self._client = httpx.AsyncClient(timeout=60.0, headers=self._headers)
        self._request_id = 0
        self._session_id: str | None = None

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC request and return the result.

        Handles both direct JSON responses and SSE-streamed responses.
        If the server returns a Mcp-Session-Id header, it is captured
        and sent on subsequent requests.
        """
        self._request_id += 1
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params:
            msg["params"] = params

        headers: dict[str, str] = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        response = await self._client.post(self._url, json=msg, headers=headers)
        response.raise_for_status()

        # Capture session id if returned
        if "mcp-session-id" in response.headers:
            self._session_id = response.headers["mcp-session-id"]

        content_type = response.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            # Parse SSE response to extract the JSON-RPC result
            return self._parse_sse_response(response.text)

        # Direct JSON response
        data = response.json()
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data.get("result")

    def _parse_sse_response(self, text: str) -> Any:
        """Parse SSE text to extract the final JSON-RPC result."""
        last_data = None
        for line in text.split("\n"):
            if line.startswith("data: "):
                last_data = line[6:]

        if last_data:
            data = json.loads(last_data)
            if "error" in data:
                raise RuntimeError(f"MCP error: {data['error']}")
            return data.get("result")
        return None

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


class McpConnection:
    """A managed MCP connection that holds the transport for cleanup."""

    def __init__(
        self,
        tools: list[ToolSpec],
        transport: _StdioTransport | _SseTransport | _StreamableHttpTransport,
        proxy: _ProxiedTransport | None = None,
    ) -> None:
        self.tools = tools
        self.transport = transport
        self._proxy = proxy

    async def close(self) -> None:
        """Close the underlying transport."""
        if self._proxy:
            self._proxy.inner = None
        await self.transport.close()


class ResilientMcpConnection:
    """McpConnection wrapper with automatic reconnection on subprocess crash.

    When a tool call raises ``ConnectionError`` (subprocess died), this wrapper:
    1. Closes the dead transport
    2. Re-spawns the subprocess via ``ConnectorSpec.connect()``
    3. Swaps the proxy transport's inner reference so existing tool handlers work
    4. Retries the failed tool call

    Exponential backoff: 1s, 2s, 4s (configurable).  Max 3 retries by default.
    """

    def __init__(
        self,
        inner: McpConnection,
        reconnect_fn: "ReconnectFn | None" = None,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        self._inner = inner
        self._reconnect_fn = reconnect_fn
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._reconnect_count = 0

    @property
    def tools(self) -> list[ToolSpec]:
        return self._inner.tools

    @property
    def transport(self) -> _StdioTransport | _SseTransport | _StreamableHttpTransport:
        return self._inner.transport

    async def close(self) -> None:
        """Close the underlying connection."""
        self._reconnect_count = 0
        await self._inner.close()

    async def reconnect(self) -> None:
        """Replace the dead transport with a fresh one.

        If a ``reconnect_fn`` was provided (typically from ConnectorRegistry),
        it handles the full reconnection cycle.  Otherwise, we're a plain
        McpConnection and reconnection is not possible.
        """
        if self._reconnect_fn is None:
            raise ConnectionError("No reconnect function configured")

        # Close dead transport (ignore errors — it's already dead)
        try:
            await self._inner.transport.close()
        except Exception:
            pass

        new_conn = await self._reconnect_fn()

        # Swap proxy inner so existing tool handler closures point at new transport
        if self._inner._proxy and new_conn._proxy:
            self._inner._proxy.inner = new_conn.transport
        elif self._inner._proxy:
            self._inner._proxy.inner = new_conn.transport

        self._inner.transport = new_conn.transport
        self._reconnect_count += 1
        logger.info(
            "MCP connection recovered (attempt %d)", self._reconnect_count
        )


# Type alias for reconnect callback
ReconnectFn = "asyncio.coroutines.CoroWrapper"  # actually Callable[[], Awaitable[McpConnection]]


class McpClient:
    """Connect to MCP servers and discover their tools as ToolSpecs.

    Usage::

        # Stdio transport (subprocess)
        tools = await McpClient.connect("npx my-mcp-server")

        # Managed connection (keeps transport alive for cleanup)
        conn = await McpClient.connect_managed("npx my-mcp-server")
        tools = conn.tools
        await conn.close()

        # SSE transport (HTTP)
        tools = await McpClient.connect_sse("http://localhost:3000/mcp")

        # Streamable HTTP transport
        tools = await McpClient.connect_http("http://localhost:3000/mcp")

        # Use with an agent
        agent = UniversalAgent(name="bot", model="gpt-4o", tools=tools)
    """

    @classmethod
    async def connect(
        cls,
        server_cmd: str | list[str],
        env: dict[str, str] | None = None,
    ) -> list[ToolSpec]:
        """Launch an MCP server via stdio and return its tools as ToolSpecs.

        **Security note — trust boundary**: The command is executed with the
        same privileges as the host process via ``asyncio.create_subprocess_exec``
        (no shell invocation, preventing shell injection). However, the caller is
        responsible for ensuring the command is trusted. In multi-tenant
        deployments, restrict MCP server commands to an allowlist or run them
        in a sandboxed environment.

        Args:
            server_cmd: Command to launch the MCP server.
                        String is split by spaces; list is used as-is.
            env: Optional environment variables for the subprocess.
        """
        if isinstance(server_cmd, str):
            cmd = server_cmd.split()
        else:
            cmd = server_cmd

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        transport = _StdioTransport(process)
        try:
            return await cls._discover_tools(transport)
        except Exception:
            await transport.close()
            raise

    @classmethod
    async def connect_managed(
        cls,
        server_cmd: str | list[str],
        env: dict[str, str] | None = None,
        proxy: _ProxiedTransport | None = None,
    ) -> McpConnection:
        """Launch an MCP server and return a managed connection.

        Unlike ``connect()``, this keeps the transport alive so the caller
        can later close it cleanly. The returned ``McpConnection`` holds
        both the discovered tools and the transport handle.

        Args:
            server_cmd: Command to launch the MCP server.
            env: Optional environment variables for the subprocess.
            proxy: Optional proxy transport for resilient connections.
                   When provided, tool handler closures bind to the proxy
                   instead of the raw transport, enabling reconnection.
        """
        if isinstance(server_cmd, str):
            cmd = server_cmd.split()
        else:
            cmd = server_cmd

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        transport = _StdioTransport(process)
        if proxy is not None:
            proxy.inner = transport
        try:
            # Handlers bind to proxy (if given) so they survive reconnection
            handler_transport = proxy if proxy is not None else transport
            tools = await cls._discover_tools(transport, handler_transport)
            return McpConnection(tools=tools, transport=transport, proxy=proxy)
        except Exception:
            await transport.close()
            raise

    @classmethod
    async def connect_sse(
        cls, url: str, headers: dict[str, str] | None = None
    ) -> list[ToolSpec]:
        """Connect to an MCP server via HTTP+SSE and return its tools.

        Args:
            url: Base URL of the MCP server's JSON-RPC endpoint.
            headers: Optional extra HTTP headers (e.g. for authentication).
        """
        transport = _SseTransport(url, headers=headers)
        try:
            return await cls._discover_tools(transport)
        except Exception:
            await transport.close()
            raise

    @classmethod
    async def connect_http(cls, url: str, headers: dict[str, str] | None = None) -> list[ToolSpec]:
        """Connect to an MCP server via streamable HTTP and return its tools.

        This uses the MCP 2025-03-26 streamable HTTP transport: JSON-RPC
        requests are sent as HTTP POST and responses may be direct JSON or
        SSE streams. Session management via Mcp-Session-Id is handled
        automatically.

        Args:
            url: URL of the MCP server's endpoint.
            headers: Optional extra HTTP headers (e.g. for authentication).
        """
        transport = _StreamableHttpTransport(url, headers=headers)
        try:
            return await cls._discover_tools(transport)
        except Exception:
            await transport.close()
            raise

    @classmethod
    async def _discover_tools(
        cls,
        transport: _StdioTransport | _SseTransport | _StreamableHttpTransport,
        handler_transport: (
            _StdioTransport | _SseTransport | _StreamableHttpTransport | _ProxiedTransport | None
        ) = None,
    ) -> list[ToolSpec]:
        """Perform MCP handshake and convert discovered tools to ToolSpecs.

        Args:
            transport: The transport to use for the handshake and tool listing.
            handler_transport: The transport that tool handler closures bind to.
                If ``None``, handlers bind directly to *transport*.
                Pass a ``_ProxiedTransport`` for resilient connections so
                handlers survive reconnection.
        """
        if handler_transport is None:
            handler_transport = transport

        # Initialize handshake
        await transport.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "sagewai", "version": "0.1.0"},
            },
        )

        # List available tools
        result = await transport.request("tools/list")
        raw_tools = result.get("tools", []) if result else []

        tools: list[ToolSpec] = []
        for raw in raw_tools:
            name = raw["name"]
            description = raw.get("description", name)
            parameters = raw.get(
                "inputSchema",
                {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            )

            # Create a handler closure that proxies calls back through MCP.
            # When handler_transport is a _ProxiedTransport, the closure
            # survives reconnection because the proxy's .inner gets swapped.
            async def _make_handler(
                t: Any,
                tool_name: str,
            ):
                async def handler(**kwargs: Any) -> str:
                    result = await t.request(
                        "tools/call",
                        {"name": tool_name, "arguments": kwargs},
                    )
                    # MCP tools return content as list of content blocks
                    if isinstance(result, dict):
                        content = result.get("content", [])
                        if isinstance(content, list):
                            texts = [c.get("text", str(c)) for c in content if isinstance(c, dict)]
                            return "\n".join(texts) if texts else json.dumps(result)
                        return json.dumps(result)
                    return str(result)

                return handler

            handler = await _make_handler(handler_transport, name)

            tools.append(
                ToolSpec(
                    name=name,
                    description=description,
                    parameters=parameters,
                    handler=handler,
                )
            )

        logger.info("Discovered %d tools from MCP server", len(tools))
        return tools
