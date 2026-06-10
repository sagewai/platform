# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Single-org MCP execution: /api/v1/mcp/call + /api/v1/mcp/discover.

Hermetic — no network, no subprocess. We register a fake HTTP MCP
connection in the single-org store, then monkeypatch the transport
factory (``McpClient.connect_http``) to return fake ToolSpecs whose
handlers return a known value. The route must execute the named tool and
return its decoded result (200, not 501). A stdio connection with
host-exec disabled must be cleanly refused (non-2xx, clear error).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile
from sagewai.connections.bootstrap import build_connections_context
from sagewai.models.tool import ToolSpec


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())


@pytest.fixture
def _store_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(tmp_path / "admin-state.json"))
    return tmp_path


@pytest.fixture
def sf(_store_env, tmp_path: Path) -> AdminStateFile:
    sf = AdminStateFile(tmp_path / "admin-state.json")
    sf.complete_setup(
        org_name="Test Org",
        admin_email="admin@example.com",
        admin_password="hunter22",
    )
    return sf


@pytest.fixture
def token(sf: AdminStateFile) -> str:
    result = sf.validate_login("admin@example.com", "hunter22")
    assert result is not None
    return result["access_token"]


@pytest.fixture
def app(sf: AdminStateFile):
    return create_admin_serve_app(sf)


@pytest.fixture
def client(app, token: str) -> TestClient:
    # Bearer auth is exempt from CSRF and carries full admin scope, which is
    # what the POST /api/v1/mcp/* mutations require in single-org mode.
    tc = TestClient(app)
    tc.headers.update({"Authorization": f"Bearer {token}"})
    return tc


@pytest.fixture
def ctx(sf):
    return build_connections_context(sf)


def _fake_tools() -> list[ToolSpec]:
    async def _echo(**kwargs):
        return f"echoed:{kwargs.get('msg')}"

    return [
        ToolSpec(
            name="echo",
            description="Echo back the message",
            parameters={"type": "object", "properties": {"msg": {"type": "string"}}},
            handler=_echo,
        )
    ]


@pytest.fixture
def http_conn_id(ctx) -> str:
    """A free-form HTTP MCP connection (no policy gate)."""
    conn = ctx.store.create(
        protocol="mcp",
        project_id="default",
        display_name="Fake HTTP MCP",
        tags=[],
        protocol_data={
            "transport": "http",
            "url": "http://localhost:9999/mcp",
        },
    )
    return conn.id


@pytest.fixture
def stdio_conn_id(ctx) -> str:
    conn = ctx.store.create(
        protocol="mcp",
        project_id="default",
        display_name="Fake stdio MCP",
        tags=[],
        protocol_data={
            "transport": "stdio",
            "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
        },
    )
    return conn.id


def test_mcp_call_executes_tool_and_returns_result(client, http_conn_id, monkeypatch):
    async def _fake_connect_http(url, headers=None):
        return _fake_tools()

    from sagewai.mcp import client as mcp_client_mod

    monkeypatch.setattr(mcp_client_mod.McpClient, "connect_http", _fake_connect_http)

    resp = client.post(
        "/api/v1/mcp/call",
        headers={"X-Project-ID": "default"},
        json={
            "connection_id": http_conn_id,
            "tool": "echo",
            "arguments": {"msg": "hi"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error"] is None
    assert body["result"] == "echoed:hi"


def test_mcp_discover_returns_real_tool_list(client, http_conn_id, monkeypatch):
    async def _fake_connect_http(url, headers=None):
        return _fake_tools()

    from sagewai.mcp import client as mcp_client_mod

    monkeypatch.setattr(mcp_client_mod.McpClient, "connect_http", _fake_connect_http)

    resp = client.post(
        "/api/v1/mcp/discover",
        headers={"X-Project-ID": "default"},
        json={"connection_id": http_conn_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {t["name"] for t in body["tools"]}
    assert names == {"echo"}


def test_mcp_discover_uses_cached_tools_when_present(client, ctx):
    """If the connection already has discovered_tools cached, discover returns them
    without opening a client (no monkeypatch needed → would crash on real network)."""
    conn = ctx.store.create(
        protocol="mcp",
        project_id="default",
        display_name="Cached MCP",
        tags=[],
        protocol_data={
            "transport": "http",
            "url": "http://localhost:9999/mcp",
            "discovered_tools": [
                {"name": "cached_tool", "description": "", "input_schema": {}},
            ],
        },
    )
    resp = client.post(
        "/api/v1/mcp/discover",
        headers={"X-Project-ID": "default"},
        json={"connection_id": conn.id},
    )
    assert resp.status_code == 200, resp.text
    names = {t["name"] for t in resp.json()["tools"]}
    assert names == {"cached_tool"}


def test_mcp_call_unknown_connection_404(client):
    resp = client.post(
        "/api/v1/mcp/call",
        headers={"X-Project-ID": "default"},
        json={"connection_id": "conn-does-not-exist", "tool": "echo", "arguments": {}},
    )
    assert resp.status_code == 404, resp.text


def test_mcp_call_unknown_tool_404(client, http_conn_id, monkeypatch):
    async def _fake_connect_http(url, headers=None):
        return _fake_tools()

    from sagewai.mcp import client as mcp_client_mod

    monkeypatch.setattr(mcp_client_mod.McpClient, "connect_http", _fake_connect_http)

    resp = client.post(
        "/api/v1/mcp/call",
        headers={"X-Project-ID": "default"},
        json={
            "connection_id": http_conn_id,
            "tool": "no_such_tool",
            "arguments": {},
        },
    )
    assert resp.status_code == 404, resp.text
    assert "no_such_tool" in resp.json()["error"]


def test_mcp_call_stdio_refused_without_host_exec(client, stdio_conn_id, monkeypatch):
    """stdio connection is cleanly refused when host-exec is disabled (no crash)."""
    # Ensure host-exec is OFF for both the policy and any direct check.
    monkeypatch.delenv("SAGEWAI_ALLOW_HOST_EXEC", raising=False)

    resp = client.post(
        "/api/v1/mcp/call",
        headers={"X-Project-ID": "default"},
        json={"connection_id": stdio_conn_id, "tool": "echo", "arguments": {}},
    )
    assert resp.status_code != 200
    assert resp.status_code in (400, 403, 501), resp.text
    body = resp.json()
    err = body.get("error") or body.get("detail") or ""
    assert "host" in err.lower() or "stdio" in err.lower()
