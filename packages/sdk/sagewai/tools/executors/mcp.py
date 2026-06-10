# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""``kind: mcp`` executor — MCP client over stdio, HTTP, or SSE.

Wires the catalog ``server_ref`` to the real :class:`McpClient`
transports. HTTP and SSE are ungated; stdio launches a local subprocess
and is therefore refused unless ``host_exec_allowed()`` is true.

``_open_client`` returns a thin :class:`_CallToolClient` adapter that
exposes the ``call_tool(name, inputs)`` / ``close()`` shape ``run()``
expects, dispatching tool calls through each discovered ``ToolSpec``'s
handler closure (which proxies ``tools/call`` over the live transport).
"""
from __future__ import annotations

import inspect
from typing import Any, Callable

from sagewai.mcp.client import McpClient
from sagewai.sandbox.policy import host_exec_allowed
from sagewai.tools.registry import CatalogEntry


class McpStdioRefusedError(RuntimeError):
    """Raised when a stdio MCP server is requested but host-exec is disabled."""


class _CallToolClient:
    """Adapter exposing ``call_tool``/``close`` over a discovered tool set.

    ``connect_http``/``connect_sse`` return ``list[ToolSpec]`` whose
    ``handler`` closures proxy ``tools/call`` over the live transport.
    stdio returns a managed connection holding both tools and a
    closeable transport. This adapter normalizes both so ``run()`` and
    the admin route share one ``call_tool(name, inputs)`` contract.
    """

    def __init__(self, tools: list[Any], *, conn: Any = None) -> None:
        self._by_name = {t.name: t for t in tools}
        self._conn = conn

    @property
    def tools(self) -> list[Any]:
        return list(self._by_name.values())

    async def call_tool(self, name: str, inputs: dict[str, Any]) -> Any:
        spec = self._by_name.get(name)
        if spec is None or spec.handler is None:
            raise KeyError(name)
        return await spec.handler(**(inputs or {}))

    async def close(self) -> None:
        conn = self._conn
        self._conn = None
        if conn is not None and hasattr(conn, "close"):
            try:
                await conn.close()
            except Exception:
                pass


def _parse_server_ref(server_ref: str) -> tuple[str, str]:
    """Split a catalog ``server_ref`` into ``(transport, target)``.

    Recognized forms::

        stdio:<command line>     -> ("stdio", "<command line>")
        sse:<url>                -> ("sse", "<url>")
        http://... | https://... -> ("http", "<url>")
        <bare>                   -> ("stdio", "<bare>")  # legacy default

    The bare fallback preserves the historical stdio default so existing
    catalog entries without an explicit prefix keep working (and stay
    host-exec gated).
    """
    ref = server_ref.strip()
    if ref.startswith("stdio:"):
        return "stdio", ref[len("stdio:") :].strip()
    if ref.startswith("sse:"):
        return "sse", ref[len("sse:") :].strip()
    if ref.startswith(("http://", "https://")):
        return "http", ref
    return "stdio", ref


async def _open_client(server_ref: str) -> _CallToolClient:
    """Resolve the transport from ``server_ref`` and open the matching client.

    HTTP/SSE are ungated. stdio is refused (``McpStdioRefusedError``) unless
    ``host_exec_allowed()`` — the same gate :class:`McpClient` enforces
    internally, checked up-front here for a clear error.
    """
    transport, target = _parse_server_ref(server_ref)
    if transport == "http":
        tools = await McpClient.connect_http(target)
        return _CallToolClient(tools)
    if transport == "sse":
        tools = await McpClient.connect_sse(target)
        return _CallToolClient(tools)
    # stdio
    if not host_exec_allowed():
        raise McpStdioRefusedError(
            "Host-backed execution disabled. Set SAGEWAI_ALLOW_HOST_EXEC=1 to "
            "enable stdio MCP servers (they launch local subprocesses)."
        )
    conn = await McpClient.connect_managed(target)
    return _CallToolClient(list(conn.tools), conn=conn)


async def run(
    entry: CatalogEntry,
    *,
    operation: str | None,
    inputs: dict[str, Any],
    project_id: str,
    get_credentials: Callable[..., Any],
) -> dict[str, Any]:
    cfg = entry.exec_["mcp"]
    tool_name = operation or cfg.get("tool_name")
    if tool_name is None:
        raise ValueError("mcp executor: no tool_name and no operation passed")
    client = _open_client(cfg["server_ref"])
    # ``_open_client`` is async in production; tests monkeypatch it with a
    # plain function returning a stub. Support both.
    if inspect.isawaitable(client):
        client = await client
    try:
        return await client.call_tool(tool_name, inputs)
    finally:
        await client.close()
