# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""MCP plugin tests."""
from __future__ import annotations

import pytest
from fastapi import APIRouter
from pydantic import ValidationError

from sagewai.connections.models import Connection
from sagewai.connections.protocols.base import PluginContext
from sagewai.connections.protocols.mcp import McpProtocolPlugin
from sagewai.connections.store import ConnectionStore


def _conn(protocol_data):
    return Connection(
        id="conn_mcp_x", protocol="mcp", project_id="default",
        display_name="filesystem", tags=(), credentials_backend=None,
        status="pending", last_tested_at=None, last_test_ok=None,
        is_default=False, created_at="t", updated_at="t",
        last_error=None, protocol_data=protocol_data,
    )


def test_plugin_identity():
    p = McpProtocolPlugin()
    assert p.id == "mcp"
    assert p.display_name == "MCP server"
    assert p.sensitive_fields == ()


def test_schema_accepts_stdio_form():
    p = McpProtocolPlugin()
    p.protocol_data_schema().model_validate({
        "transport": "stdio",
        "command": ["mcp-server-filesystem"],
        "args": ["--root", "/tmp"],
    })


def test_schema_accepts_http_form():
    p = McpProtocolPlugin()
    p.protocol_data_schema().model_validate({
        "transport": "http",
        "url": "http://localhost:9000/mcp",
    })


def test_schema_accepts_sse_form():
    p = McpProtocolPlugin()
    p.protocol_data_schema().model_validate({
        "transport": "sse",
        "url": "http://localhost:9000/sse",
    })


def test_schema_rejects_unknown_transport():
    p = McpProtocolPlugin()
    with pytest.raises(ValidationError):
        p.protocol_data_schema().model_validate({"transport": "websocket", "url": "ws://x"})


def test_schema_rejects_stdio_without_command():
    p = McpProtocolPlugin()
    with pytest.raises(ValidationError):
        p.protocol_data_schema().model_validate({"transport": "stdio"})


def test_schema_rejects_http_without_url():
    p = McpProtocolPlugin()
    with pytest.raises(ValidationError):
        p.protocol_data_schema().model_validate({"transport": "http"})


def test_public_view_passes_through():
    p = McpProtocolPlugin()
    data = {"transport": "stdio", "command": ["x"]}
    assert p.public_view(data) == data


def test_extra_routes_empty():
    assert McpProtocolPlugin().extra_routes().routes == []


def test_extra_cli_has_probe_command():
    cmds = McpProtocolPlugin().extra_cli()
    names = {c.name for c in cmds}
    assert "probe" in names


@pytest.mark.asyncio
async def test_test_method_returns_ok_when_client_connects(monkeypatch, tmp_path):
    """Patch MCPClient to a stub that 'connects' successfully."""
    p = McpProtocolPlugin()

    class _StubClient:
        def __init__(self, *a, **kw):
            self.connected = False

        async def __aenter__(self):
            self.connected = True
            return self

        async def __aexit__(self, *exc):
            self.connected = False

        async def list_tools(self):
            return [{"name": "stub_tool"}]

    monkeypatch.setattr("sagewai.connections.protocols.mcp.MCPClient", _StubClient)
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    conn = _conn({"transport": "http", "url": "http://localhost:9000/mcp"})
    result = await p.test(conn, ctx=ctx)
    assert result.ok is True


@pytest.mark.asyncio
async def test_test_method_returns_not_ok_on_connect_failure(monkeypatch, tmp_path):
    p = McpProtocolPlugin()

    class _RaisingClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): raise ConnectionRefusedError("server down")
        async def __aexit__(self, *exc): pass

    monkeypatch.setattr("sagewai.connections.protocols.mcp.MCPClient", _RaisingClient)
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    conn = _conn({"transport": "http", "url": "http://localhost:9000/mcp"})
    result = await p.test(conn, ctx=ctx)
    assert result.ok is False
    assert "server down" in (result.message or "")
