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

Bundle A surface: validates ``protocol_data`` for stdio/http/sse
transports, optionally pinning to a registry entry via ``server_ref``,
dispatches credentials to subprocess env vars or HTTP headers per the
registry entry, runs the MCP handshake + tools/list during ``test()``,
and caches the discovered tools onto the record.

This module exposes an :class:`MCPClient` adapter wrapping the
``McpClient`` classmethod-factory API as an async context manager so
the plugin's ``test()`` body and any monkeypatched stub share a single
contract surface.
"""
from __future__ import annotations

import asyncio
import json as _json
from datetime import datetime, timezone
from typing import Any, ClassVar, Literal

import click
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.connections.models import Connection, TestResult
from sagewai.connections.protocols.base import PluginContext
from sagewai.connections.store_ops import store_get, store_update
from sagewai.mcp import servers as mcp_servers
from sagewai.mcp.client import McpClient


class MCPClient:
    """Async-context-manager wrapper over :class:`McpClient`.

    The underlying ``McpClient`` uses classmethod factories
    (``connect_managed``, ``connect_sse``, ``connect_http``) that return
    different shapes per transport. This adapter normalizes them so the
    plugin's ``test()`` can ``async with MCPClient(...)`` regardless of
    transport.

    ``env`` is forwarded only for stdio. ``headers`` is forwarded only
    for http/sse. The other axis is silently ignored.
    """

    def __init__(
        self,
        *,
        transport: str | None = None,
        command: list[str] | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        env: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._transport = transport
        self._command = command or []
        self._args = args or []
        self._url = url
        self._env = env
        self._headers = headers
        self._conn: Any = None  # McpConnection for stdio; list[ToolSpec] for http/sse
        self._tools: list[Any] | None = None

    async def __aenter__(self) -> "MCPClient":
        if self._transport == "stdio":
            cmd = [*self._command, *self._args]
            # Merge with os.environ so the subprocess inherits the parent's env;
            # passing env=None means "inherit fully". Passing a dict merges.
            import os

            merged_env = (
                {**os.environ, **self._env} if self._env else None
            )
            conn = await McpClient.connect_managed(cmd, env=merged_env)
            self._conn = conn
            self._tools = list(conn.tools)
        elif self._transport == "sse":
            self._tools = list(
                await McpClient.connect_sse(self._url or "", headers=self._headers)
            )
        else:  # http (and any future fallthrough)
            self._tools = list(
                await McpClient.connect_http(self._url or "", headers=self._headers)
            )
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

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a discovered tool by name and return its decoded result.

        Dispatches through the discovered ``ToolSpec``'s ``handler``
        closure, which proxies ``tools/call`` over the live transport and
        decodes the MCP content blocks. Raises ``KeyError`` if no tool
        with ``name`` was discovered.
        """
        for tool in self._tools or []:
            if _tool_attr(tool, "name") == name:
                handler = getattr(tool, "handler", None)
                if handler is None:
                    raise KeyError(name)
                return await handler(**(arguments or {}))
        raise KeyError(name)


class McpToolMeta(BaseModel):
    """One entry in the capability cache — what the MCP server reports via list_tools."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class McpProtocolData(BaseModel):
    """Validation schema for MCP server connections."""

    model_config = ConfigDict(extra="forbid")

    server_ref: str | None = None
    transport: Literal["stdio", "http", "sse"]
    command: list[str] | None = None
    args: list[str] | None = None
    url: str | None = None
    credentials: dict[str, str] = Field(default_factory=dict)
    discovered_tools: list[McpToolMeta] = Field(default_factory=list)
    last_discovered_at: str | None = None

    @model_validator(mode="after")
    def _validate(self):
        if self.server_ref is not None:
            try:
                entry = mcp_servers.get_server(self.server_ref)
            except mcp_servers.UnknownMcpServerError as exc:
                raise ValueError(
                    f"unknown MCP server_ref: {self.server_ref!r}"
                ) from exc
            required = {f.name for f in entry.credential_fields}
            missing = required - set(self.credentials.keys())
            if missing:
                raise ValueError(
                    f"missing required credentials for server_ref={self.server_ref!r}: "
                    f"{sorted(missing)!r}"
                )
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio transport requires 'command'")
        if self.transport in ("http", "sse") and not self.url:
            raise ValueError(f"{self.transport} transport requires 'url'")
        return self


def _tool_attr(tool: Any, name: str, default: Any = None) -> Any:
    """Extract attribute from a ToolSpec — works for dict or object shapes."""
    if isinstance(tool, dict):
        return tool.get(name, default)
    # ToolSpec may expose ``parameters`` rather than ``input_schema``.
    if name == "input_schema" and not hasattr(tool, "input_schema"):
        return getattr(tool, "parameters", default)
    return getattr(tool, name, default)


# ── Context injection (test + production) ──────────────────────────
#
# Mirrors the OAuth2 plugin pattern (PR #361). Route handlers + CLI
# commands need a fresh :class:`ConnectionsContext`; we use a module-
# level injectable singleton: tests use ``_test_inject_context``, the
# CLI sets it per-invocation, and production routes fall through to
# ``build_connections_context(AdminStateFile(default_admin_state_path()))``.

_INJECTED_CTX = None


def _test_inject_context(ctx) -> None:
    """Test/CLI hook: set the context the route bodies + CLI commands will use.

    Pass ``None`` to clear.
    """
    global _INJECTED_CTX
    _INJECTED_CTX = ctx


def _get_ctx():
    """Return the active :class:`ConnectionsContext`.

    Falls back to constructing a fresh context from the
    :class:`AdminStateFile` at the platform-default path when nothing
    has been injected (production path).
    """
    if _INJECTED_CTX is not None:
        return _INJECTED_CTX
    # Production path: construct fresh from AdminStateFile.
    from sagewai.admin.state_file import AdminStateFile, default_admin_state_path
    from sagewai.connections.bootstrap import build_connections_context

    return build_connections_context(AdminStateFile(default_admin_state_path()))


# ── extra_routes implementations ────────────────────────────────────

_mcp_router = APIRouter()


@_mcp_router.get("/servers")
async def _servers_route():
    """List registry entries."""
    return [
        {
            "id": e.id,
            "display_name": e.display_name,
            "transport": e.transport,
            "default_command": e.default_command,
            "default_args": e.default_args,
            "credential_fields": [
                {
                    "name": f.name,
                    "label": f.label,
                    "type": f.type,
                    "injection": f.injection,
                    "description": f.description,
                }
                for f in e.credential_fields
            ],
            "docs_url": e.docs_url,
            "description": e.description,
        }
        for e in mcp_servers.all_servers()
    ]


@_mcp_router.post("/{connection_id}/refresh")
async def _refresh_route(connection_id: str):
    """Re-discover tools + persist; mirrors ``test()``."""
    ctx = _get_ctx()
    record = await store_get(ctx.store, connection_id)
    if record is None:
        raise HTTPException(404, f"connection {connection_id} not found")
    if record.protocol != "mcp":
        raise HTTPException(400, f"connection {connection_id} is not mcp")
    plugin = McpProtocolPlugin()
    plugin_ctx = ctx.make_plugin_context(project_id=record.project_id, request=None)
    result = await plugin.test(record, ctx=plugin_ctx)
    if not result.ok:
        raise HTTPException(502, f"refresh failed: {result.message}")
    refreshed = await store_get(ctx.store, connection_id)
    return plugin.public_view(refreshed.protocol_data)


@_mcp_router.get("/{connection_id}/tools")
async def _tools_route(connection_id: str):
    """Return cached tools list."""
    ctx = _get_ctx()
    record = await store_get(ctx.store, connection_id)
    if record is None:
        raise HTTPException(404, f"connection {connection_id} not found")
    if record.protocol != "mcp":
        raise HTTPException(400, f"connection {connection_id} is not mcp")
    return {
        "tools": record.protocol_data.get("discovered_tools", []),
        "last_discovered_at": record.protocol_data.get("last_discovered_at"),
    }


# ── extra_cli implementations ───────────────────────────────────────


@click.command("servers")
@click.option("--json", "as_json", is_flag=True)
def _servers_cmd(as_json: bool):
    """List registry entries."""
    entries = mcp_servers.all_servers()
    if as_json:
        click.echo(_json.dumps([
            {
                "id": e.id,
                "display_name": e.display_name,
                "transport": e.transport,
                "credential_fields": [f.name for f in e.credential_fields],
                "docs_url": e.docs_url,
                "description": e.description,
            }
            for e in entries
        ]))
        return
    for e in entries:
        click.echo(
            f"{e.id:<14} {e.display_name:<18} ({e.transport}) — {e.description}"
        )


@click.command("refresh")
@click.argument("connection_id")
def _refresh_cmd(connection_id: str):
    """Re-discover tools and update the capability cache."""
    ctx = _get_ctx()
    record = ctx.store.get(connection_id)
    if record is None:
        click.echo(f"error: connection {connection_id} not found", err=True)
        raise SystemExit(1)
    plugin = McpProtocolPlugin()
    plugin_ctx = ctx.make_plugin_context(project_id=record.project_id, request=None)
    result = asyncio.run(plugin.test(record, ctx=plugin_ctx))
    if not result.ok:
        click.echo(f"refresh failed: {result.message}", err=True)
        raise SystemExit(1)
    refreshed = ctx.store.get(connection_id)
    n = len(refreshed.protocol_data.get("discovered_tools", []))
    click.echo(f"ok — discovered {n} tools")


@click.command("tools")
@click.argument("connection_id")
@click.option("--json", "as_json", is_flag=True)
def _tools_cmd(connection_id: str, as_json: bool):
    """Print the cached tools list."""
    ctx = _get_ctx()
    record = ctx.store.get(connection_id)
    if record is None:
        click.echo(f"error: connection {connection_id} not found", err=True)
        raise SystemExit(1)
    tools = record.protocol_data.get("discovered_tools", [])
    if as_json:
        click.echo(_json.dumps({
            "tools": tools,
            "last_discovered_at": record.protocol_data.get("last_discovered_at"),
        }))
        return
    if not tools:
        click.echo(
            "no tools discovered yet — run `sagewai connections mcp refresh <id>` first"
        )
        return
    for t in tools:
        click.echo(f"{t.get('name'):<24} {t.get('description', '')}")


# ── Plugin ─────────────────────────────────────────────────────────


class McpProtocolPlugin:
    id: ClassVar[str] = "mcp"
    display_name: ClassVar[str] = "MCP server"
    # ``sensitive_fields`` ClassVar stays empty for MCP; per-record dispatch
    # via ``sensitive_field_paths_for`` derives the actual paths from the
    # resolved registry entry. Other plugins continue to use the ClassVar.
    sensitive_fields: ClassVar[tuple[str, ...]] = ()

    def protocol_data_schema(self) -> type[BaseModel]:
        return McpProtocolData

    def sensitive_field_paths_for(self, connection: Connection) -> tuple[str, ...]:
        """Per-record sensitive paths derived from the resolved registry entry.

        - server_ref points at a known registry entry: every credential
          field with ``type=password`` becomes ``credentials.<NAME>``.
        - server_ref points at an unknown entry (shouldn't happen if writes
          went through the schema validator, but defensively): mask every
          credential.
        - No server_ref (free-form): mask every credential.
        """
        pd = connection.protocol_data
        server_ref = pd.get("server_ref")
        sensitive: list[str] = []
        if server_ref:
            try:
                entry = mcp_servers.get_server(server_ref)
                for f in entry.credential_fields:
                    if f.type == "password":
                        sensitive.append(f"credentials.{f.name}")
            except mcp_servers.UnknownMcpServerError:
                # Defensive: unknown server_ref — mask everything in credentials.
                for name in pd.get("credentials", {}):
                    sensitive.append(f"credentials.{name}")
        else:
            # Free-form: mask everything defensively.
            for name in pd.get("credentials", {}):
                sensitive.append(f"credentials.{name}")
        return tuple(sensitive)

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        out = dict(protocol_data)
        if include_secrets:
            return out
        server_ref = out.get("server_ref")
        sensitive_names: set[str] = set()
        if server_ref:
            try:
                entry = mcp_servers.get_server(server_ref)
                sensitive_names = {
                    f.name for f in entry.credential_fields if f.type == "password"
                }
            except mcp_servers.UnknownMcpServerError:
                sensitive_names = set(out.get("credentials", {}).keys())
        else:
            sensitive_names = set(out.get("credentials", {}).keys())
        creds = out.get("credentials")
        if isinstance(creds, dict) and sensitive_names:
            out["credentials"] = {
                k: ("***" if k in sensitive_names else v) for k, v in creds.items()
            }
        return out

    async def on_create(self, connection: Connection, *, ctx: PluginContext) -> Connection:
        return connection

    async def on_update(
        self, before: Connection, after: Connection, *, ctx: PluginContext
    ) -> Connection:
        return after

    async def on_delete(self, connection: Connection, *, ctx: PluginContext) -> None:
        return None

    # ── test() — discover + cache ─────────────────────────────────

    def _resolve_effective_config(self, connection: Connection) -> dict:
        """Resolve effective command/args/url from registry + record overrides."""
        pd = connection.protocol_data
        server_ref = pd.get("server_ref")
        if server_ref:
            try:
                entry = mcp_servers.get_server(server_ref)
            except mcp_servers.UnknownMcpServerError:
                # Defensive fallthrough — pretend no server_ref.
                entry = None
            if entry is not None:
                return {
                    "transport": pd.get("transport") or entry.transport,
                    "command": pd.get("command") or list(entry.default_command or []),
                    "args": pd.get("args") if pd.get("args") is not None else list(entry.default_args or []),
                    "url": pd.get("url") or entry.default_url_template,
                }
        return {
            "transport": pd.get("transport"),
            "command": pd.get("command"),
            "args": pd.get("args"),
            "url": pd.get("url"),
        }

    def _dispatch_credentials(
        self, connection: Connection, decrypted_creds: dict[str, str]
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Split credentials into (env, headers) per the registry's injection axis."""
        server_ref = connection.protocol_data.get("server_ref")
        env: dict[str, str] = {}
        headers: dict[str, str] = {}
        if not server_ref:
            # Free-form: env for everything
            return dict(decrypted_creds), {}
        try:
            entry = mcp_servers.get_server(server_ref)
        except mcp_servers.UnknownMcpServerError:
            return dict(decrypted_creds), {}
        name_to_field = {f.name: f for f in entry.credential_fields}
        for cred_name, cred_value in decrypted_creds.items():
            field = name_to_field.get(cred_name)
            if field is None:
                # Extra credential not in the registry — default to env.
                env[cred_name] = cred_value
                continue
            if field.injection == "env":
                env[field.name] = cred_value
            elif field.injection == "header":
                header_name = field.header_name or "Authorization"
                template = field.header_value_template or "{value}"
                headers[header_name] = template.format(value=cred_value)
        return env, headers

    async def test(self, connection: Connection, *, ctx: PluginContext) -> TestResult:
        pd = connection.protocol_data

        # 1. Resolve effective command/args/url from registry + overrides
        effective = self._resolve_effective_config(connection)

        # 2. Decrypt credentials via ctx.creds (router from PR3)
        if ctx.creds is not None:
            try:
                decrypted_pd = ctx.creds.decrypt(
                    pd,
                    sensitive_field_paths=self.sensitive_field_paths_for(connection),
                    connection_credentials_backend=connection.credentials_backend,
                )
            except Exception:
                # Already-decrypted (or malformed) inputs — pass through.
                decrypted_pd = pd
        else:
            decrypted_pd = pd
        decrypted_creds = decrypted_pd.get("credentials", {}) or {}

        # 3. Dispatch to env vs header per registry's injection axis
        env, headers = self._dispatch_credentials(connection, decrypted_creds)

        # 4. Connect + list_tools
        try:
            async with MCPClient(
                transport=effective["transport"],
                command=effective.get("command"),
                args=effective.get("args"),
                url=effective.get("url"),
                env=env or None,
                headers=headers or None,
            ) as client:
                tools = await client.list_tools()
        except Exception as exc:
            return TestResult(ok=False, message=f"{type(exc).__name__}: {exc}")

        # 5. Persist discovered_tools + last_discovered_at, re-encrypted
        new_pd = dict(decrypted_pd)
        new_pd["discovered_tools"] = [
            {
                "name": _tool_attr(t, "name"),
                "description": _tool_attr(t, "description", ""),
                "input_schema": _tool_attr(t, "input_schema", {}) or {},
            }
            for t in tools
        ]
        new_pd["last_discovered_at"] = datetime.now(timezone.utc).isoformat()

        if ctx.creds is not None:
            try:
                encrypted_pd = ctx.creds.encrypt(
                    new_pd,
                    sensitive_field_paths=self.sensitive_field_paths_for(connection),
                    connection_credentials_backend=connection.credentials_backend,
                )
            except Exception:
                encrypted_pd = new_pd
        else:
            encrypted_pd = new_pd

        await store_update(ctx.store, connection.id, protocol_data=encrypted_pd, status="ready")
        return TestResult(
            ok=True,
            message=(
                f"connected via {effective['transport']}; "
                f"discovered {len(tools)} tools"
            ),
        )

    def extra_routes(self) -> APIRouter:
        return _mcp_router

    def extra_cli(self) -> list[click.Command]:
        # Return the sub-commands as a list so the generic CLI mounts them
        # under `sagewai connections mcp ...`.
        return [_servers_cmd, _refresh_cmd, _tools_cmd]


__all__ = [
    "MCPClient",
    "McpProtocolData",
    "McpProtocolPlugin",
    "McpToolMeta",
    "_test_inject_context",
]
