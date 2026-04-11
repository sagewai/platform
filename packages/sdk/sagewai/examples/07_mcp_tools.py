#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 07 — MCP Tools: Connect to Any MCP Tool Server.

The Model Context Protocol (MCP) lets agents discover and use tools
from external servers. Sagewai's ``McpClient`` handles the wire
protocol — just point it at a server and get back ``ToolSpec`` objects.

**The Problem**: Every AI tool integration requires custom code —
parsing schemas, handling auth, managing connections. No standard exists.

**The Solution**: MCP is the USB-C of AI tools. ``McpClient.connect()``
launches a server via stdio and auto-discovers all available tools.

Requirements::

    pip install sagewai
    export OPENAI_API_KEY=sk-...
    # An MCP server must be available (e.g., a Python script or npx package)

Usage::

    python 07_mcp_tools.py
"""

from __future__ import annotations

import asyncio

from sagewai import McpClient, UniversalAgent


async def main() -> None:
    """Connect to an MCP server and use its tools in an agent."""

    # ── Connect via stdio (subprocess) ──────────────────────────────
    # Replace with your actual MCP server command.
    # Common examples:
    #   ["python", "-m", "mcp_server_filesystem"]
    #   ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    #   ["python", "-m", "sagewai.mcp.server"]
    server_cmd = ["python", "-m", "sagewai.mcp.server"]

    print("Connecting to MCP server...")
    try:
        tools = await McpClient.connect(server_cmd)
    except Exception as exc:
        print(f"Could not connect to MCP server: {exc}")
        print()
        print("To run this example, ensure you have an MCP server available.")
        print("Try: pip install mcp-server-filesystem")
        print("Then: python 07_mcp_tools.py")
        return

    print(f"Discovered {len(tools)} tools:")
    for t in tools:
        print(f"  - {t.name}: {t.description[:60]}")
    print()

    # ── Create agent with MCP tools ─────────────────────────────────
    agent = UniversalAgent(
        name="mcp-agent",
        model="gpt-4o",
        system_prompt="You are an assistant with access to external tools.",
        tools=tools,
    )

    response = await agent.chat("List the tools you have access to.")
    print(f"Agent: {response}")

    # ── SSE transport (remote server) ───────────────────────────────
    # For remote MCP servers, use connect_sse or connect_http:
    #
    #   tools = await McpClient.connect_sse("http://localhost:3000/mcp")
    #   tools = await McpClient.connect_http("http://localhost:3000/mcp")

    # ── Managed connection (with auto-reconnect) ────────────────────
    # For long-lived connections, use connect_managed:
    #
    #   conn = await McpClient.connect_managed(server_cmd)
    #   agent = UniversalAgent(name="bot", model="gpt-4o", tools=conn.tools)
    #   # ... use agent ...
    #   await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
