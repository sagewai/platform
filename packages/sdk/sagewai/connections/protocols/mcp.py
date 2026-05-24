# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""MCP server plugin.

Minimal in this spec: validates ``protocol_data`` for stdio/http/sse
transports, and probes the server via :class:`sagewai.mcp.client.McpClient`.
A follow-up spec (the queued MCP-as-first-class-record one) adds deep
capability discovery, transport health monitoring, and credentials.

This module exposes an :class:`MCPClient` adapter wrapping the
``McpClient`` classmethod-factory API as an async context manager so
the plugin's ``test()`` body and any monkeypatched stub share a single
contract surface.
"""
from __future__ import annotations

from typing import Any, ClassVar, Literal

import click
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.connections.models import Connection, TestResult
from sagewai.connections.protocols.base import PluginContext
from sagewai.mcp.client import McpClient


class MCPClient:
    """Async-context-manager wrapper over :class:`McpClient`.

    The underlying ``McpClient`` uses classmethod factories
    (``connect_managed``, ``connect_sse``, ``connect_http``) that return
    different shapes per transport. This adapter normalizes them so the
    plugin's ``test()`` can ``async with MCPClient(...)`` regardless of
    transport.
    """

    def __init__(
        self,
        *,
        transport: str | None = None,
        command: list[str] | None = None,
        args: list[str] | None = None,
        url: str | None = None,
    ) -> None:
        self._transport = transport
        self._command = command or []
        self._args = args or []
        self._url = url
        self._conn: Any = None  # McpConnection for stdio; list[ToolSpec] for http/sse
        self._tools: list[Any] | None = None

    async def __aenter__(self) -> "MCPClient":
        if self._transport == "stdio":
            cmd = [*self._command, *self._args]
            conn = await McpClient.connect_managed(cmd)
            self._conn = conn
            self._tools = list(conn.tools)
        elif self._transport == "sse":
            self._tools = list(await McpClient.connect_sse(self._url or ""))
        else:  # http (and any future fallthrough)
            self._tools = list(await McpClient.connect_http(self._url or ""))
        return self

    async def __aexit__(self, *exc: object) -> None:
        # Only stdio holds a McpConnection with a transport to close.
        conn = self._conn
        self._conn = None
        if conn is not None and hasattr(conn, "close"):
            try:
                await conn.close()
            except Exception:
                pass

    async def list_tools(self) -> list[Any]:
        return list(self._tools or [])


class McpProtocolData(BaseModel):
    """Validation schema for MCP server connections."""

    model_config = ConfigDict(extra="forbid")

    transport: Literal["stdio", "http", "sse"]
    command: list[str] | None = None
    args: list[str] | None = None
    url: str | None = None

    @model_validator(mode="after")
    def _transport_fields(self):
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio transport requires 'command'")
        if self.transport in ("http", "sse") and not self.url:
            raise ValueError(f"{self.transport} transport requires 'url'")
        return self


@click.command("probe")
@click.argument("connection_id")
def _probe(connection_id: str) -> None:
    """Probe an MCP server connection — placeholder until PR4 wires the CLI."""
    click.echo(f"probing {connection_id} (full wiring lands in PR4)")


class McpProtocolPlugin:
    id: ClassVar[str] = "mcp"
    display_name: ClassVar[str] = "MCP server"
    sensitive_fields: ClassVar[tuple[str, ...]] = ()

    def protocol_data_schema(self) -> type[BaseModel]:
        return McpProtocolData

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        return dict(protocol_data)

    async def on_create(self, connection: Connection, *, ctx: PluginContext) -> Connection:
        return connection

    async def on_update(
        self, before: Connection, after: Connection, *, ctx: PluginContext
    ) -> Connection:
        return after

    async def on_delete(self, connection: Connection, *, ctx: PluginContext) -> None:
        return None

    async def test(self, connection: Connection, *, ctx: PluginContext) -> TestResult:
        data = connection.protocol_data
        transport = data.get("transport")
        try:
            if transport == "stdio":
                client = MCPClient(
                    transport="stdio",
                    command=data.get("command", []),
                    args=data.get("args"),
                )
            else:
                client = MCPClient(transport=transport, url=data.get("url"))
            async with client:
                pass
            return TestResult(ok=True, message=f"connected via {transport}")
        except Exception as exc:
            return TestResult(ok=False, message=str(exc))

    def extra_routes(self) -> APIRouter:
        return APIRouter()

    def extra_cli(self) -> list[click.Command]:
        return [_probe]


__all__ = ["MCPClient", "McpProtocolPlugin", "McpProtocolData"]
