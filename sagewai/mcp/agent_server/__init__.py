# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""MCP server that exposes Sagewai agents as callable tools.

Connects to the Sagewai gateway (OpenAI-compatible API) and registers
each agent as an MCP tool.  MCP clients like Claude Code and Cursor
can then invoke any Sagewai agent directly.

Usage::

    python -m sagewai.mcp.agent_server \\
        --gateway-url http://localhost:8000 \\
        --token <token>

Or via environment variables::

    SAGEWAI_GATEWAY_URL=http://localhost:8000 \\
    SAGEWAI_GATEWAY_TOKEN=<token> \\
    python -m sagewai.mcp.agent_server

Claude Code ``mcp_servers`` config::

    {
        "sagewai": {
            "command": "python",
            "args": ["-m", "sagewai.mcp.agent_server"],
            "env": {
                "SAGEWAI_GATEWAY_URL": "http://localhost:8000",
                "SAGEWAI_GATEWAY_TOKEN": "<token>"
            }
        }
    }
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re

import httpx

from sagewai.mcp.server import McpServer
from sagewai.models.tool import ToolSpec

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY_URL = "http://localhost:8000"
TOOL_PREFIX = "sagewai_chat_"


def _sanitize_tool_name(agent_id: str) -> str:
    """Convert an agent id into a valid MCP tool name."""
    name = agent_id.replace("-", "_").replace(".", "_")
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    return f"{TOOL_PREFIX}{name}"


async def _list_agents(gateway_url: str, token: str) -> list[dict]:
    """Fetch available agents from the Sagewai gateway."""
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{gateway_url}/v1/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except httpx.HTTPError as exc:
            logger.error("Failed to list agents from %s: %s", gateway_url, exc)
            return []


async def _chat_with_agent(
    gateway_url: str,
    token: str,
    agent_name: str,
    message: str,
) -> str:
    """Send a message to a Sagewai agent via the gateway."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            resp = await client.post(
                f"{gateway_url}/v1/chat/completions",
                headers=headers,
                json={
                    "model": agent_name,
                    "messages": [{"role": "user", "content": message}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except httpx.HTTPError as exc:
            return f"Error communicating with agent '{agent_name}': {exc}"
        except (KeyError, IndexError) as exc:
            return f"Unexpected response format from agent '{agent_name}': {exc}"


def _build_tool(gateway_url: str, token: str, agent_id: str) -> ToolSpec:
    """Create a ToolSpec that chats with a specific agent."""

    async def _handler(message: str) -> str:
        return await _chat_with_agent(gateway_url, token, agent_id, message)

    return ToolSpec(
        name=_sanitize_tool_name(agent_id),
        description=f"Chat with the Sagewai '{agent_id}' agent.",
        parameters={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Message to send to the agent",
                },
            },
            "required": ["message"],
        },
        handler=_handler,
    )


async def create_agent_server(
    gateway_url: str | None = None,
    token: str | None = None,
) -> McpServer:
    """Create an MCP server exposing Sagewai gateway agents as tools.

    Args:
        gateway_url: Base URL of the Sagewai gateway.
            Falls back to ``SAGEWAI_GATEWAY_URL`` env var, then ``http://localhost:8000``.
        token: Bearer token for gateway authentication.
            Falls back to ``SAGEWAI_GATEWAY_TOKEN`` env var.

    Returns:
        A configured :class:`McpServer` ready to run.
    """
    gw = gateway_url or os.environ.get("SAGEWAI_GATEWAY_URL", DEFAULT_GATEWAY_URL)
    tk = token or os.environ.get("SAGEWAI_GATEWAY_TOKEN", "")

    server = McpServer(name="sagewai-agents")

    agents = await _list_agents(gw, tk)
    if not agents:
        logger.warning(
            "No agents found at %s. The server will start with zero tools. "
            "Ensure the gateway is running and the token is valid.",
            gw,
        )

    for agent in agents:
        agent_id = agent.get("id", "")
        if not agent_id:
            continue
        tool = _build_tool(gw, tk, agent_id)
        server.add_tool(tool)
        logger.info("Registered tool: %s -> agent '%s'", tool.name, agent_id)

    logger.info(
        "Sagewai MCP agent server ready with %d tool(s) from %s",
        len(server.tools),
        gw,
    )
    return server


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MCP server that exposes Sagewai agents as tools.",
    )
    parser.add_argument(
        "--gateway-url",
        default=None,
        help="Sagewai gateway URL (default: $SAGEWAI_GATEWAY_URL or http://localhost:8000)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Bearer token for gateway auth (default: $SAGEWAI_GATEWAY_TOKEN)",
    )
    return parser.parse_args()


async def _async_main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    server = await create_agent_server(
        gateway_url=args.gateway_url,
        token=args.token,
    )
    await server.run_stdio()


def main() -> None:
    """Entry point for ``python -m sagewai.mcp.agent_server``."""
    asyncio.run(_async_main())


__all__ = ["create_agent_server", "main"]
