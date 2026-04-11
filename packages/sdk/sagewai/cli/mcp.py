# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""MCP CLI commands — list, start, inspect, and call MCP servers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

import sagewai.cli as _cli


@click.group()
def mcp() -> None:
    """Manage MCP servers — list, start, or inspect tools.

    \b
    Examples:
      sagewai mcp list                              List known MCP server directories
      sagewai mcp start "npx @mcp/filesystem"       Start a server and discover tools
      sagewai mcp tools "python -m mcp_stripe"      List tools from a server
      sagewai mcp call "npx server" --tool search    Call a tool on a server
    """


@mcp.command("list")
def mcp_list() -> None:
    """List known MCP server directories."""
    # Discover mcp-servers from the project root
    candidates = [
        Path.cwd() / "mcp-servers",
        Path(__file__).resolve().parents[3] / "mcp-servers",
    ]

    servers_dir: Path | None = None
    for candidate in candidates:
        if candidate.is_dir():
            servers_dir = candidate
            break

    if servers_dir is None:
        click.echo("No mcp-servers/ directory found.")
        return

    rows: list[dict[str, Any]] = []
    for entry in sorted(servers_dir.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            pyproject = entry / "pyproject.toml"
            status = "configured" if pyproject.exists() else "scaffold"
            rows.append({"server": entry.name, "status": status})

    if not rows:
        click.echo("No MCP servers found.")
        return

    _cli._echo_table(rows, ["server", "status"])


@mcp.command("start")
@click.argument("server_cmd")
def mcp_start(server_cmd: str) -> None:
    """Start an MCP server and discover its tools.

    SERVER_CMD is the shell command to launch the server
    (e.g. 'npx my-mcp-server').
    """
    from sagewai.mcp.client import McpClient

    click.echo(f"Connecting to MCP server: {server_cmd}")
    tools = _cli._run_async(McpClient.connect(server_cmd))
    click.echo(f"Discovered {len(tools)} tool(s):")
    for t in tools:
        click.echo(f"  - {t.name}: {t.description}")


@mcp.command("tools")
@click.argument("server_cmd")
def mcp_tools(server_cmd: str) -> None:
    """Discover and list tools from an MCP server.

    SERVER_CMD is the shell command to launch the server.
    """
    from sagewai.mcp.client import McpClient

    tools = _cli._run_async(McpClient.connect(server_cmd))
    rows = [
        {
            "name": t.name,
            "description": (t.description or "")[:60],
            "params": len(t.parameters.get("properties", {})),
        }
        for t in tools
    ]
    _cli._echo_table(rows, ["name", "description", "params"])


@mcp.command("call")
@click.argument("server_cmd")
@click.option("--tool", required=True, help="Tool name to call.")
@click.option("--args", "args_json", default="{}", help="JSON arguments.")
def mcp_call(server_cmd: str, tool: str, args_json: str) -> None:
    """Call a tool on an MCP server.

    SERVER_CMD is the shell command to launch the server.
    """
    data = _cli._api_post(
        "/api/v1/mcp/call",
        {
            "server_cmd": server_cmd,
            "tool_name": tool,
            "arguments": json.loads(args_json),
        },
    )
    click.echo(f"Tool: {data.get('tool_name', tool)}")
    click.echo("Result:")
    _cli._echo_json(data.get("result", {}))


@mcp.command("api-servers")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def mcp_api_servers(as_json: bool) -> None:
    """List MCP servers from the admin API."""
    data = _cli._api_get("/api/v1/mcp/servers")
    if as_json:
        _cli._echo_json(data)
        return
    if not data:
        click.echo("No MCP servers found.")
        return
    rows = [
        {
            "name": s.get("name", ""),
            "path": s.get("path", ""),
            "status": s.get("status", ""),
        }
        for s in data
    ]
    _cli._echo_table(rows, ["name", "path", "status"])
