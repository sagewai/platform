# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for extended McpProtocolPlugin — server_ref, credentials, cache."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.connections.models import Connection
from sagewai.connections.protocols.base import get_sensitive_field_paths_for
from sagewai.connections.protocols.mcp import (
    McpProtocolData,
    McpProtocolPlugin,
)


def _make_conn(protocol_data: dict, conn_id: str = "conn_mcp_x", protocol: str = "mcp") -> Connection:
    now = datetime.now(timezone.utc).isoformat()
    return Connection(
        id=conn_id,
        kind="connection",
        protocol=protocol,
        project_id="default",
        display_name="Test",
        tags=(),
        credentials_backend=None,
        status="pending",
        last_tested_at=None,
        last_test_ok=None,
        is_default=False,
        created_at=now,
        updated_at=now,
        last_error=None,
        protocol_data=protocol_data,
    )


def test_protocol_data_accepts_server_ref():
    data = {
        "server_ref": "github",
        "transport": "stdio",
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "credentials": {"GITHUB_TOKEN": "ghp_xxx"},
    }
    McpProtocolData.model_validate(data)


def test_protocol_data_rejects_unknown_server_ref():
    data = {
        "server_ref": "not-a-server",
        "transport": "stdio",
        "command": ["echo"],
    }
    with pytest.raises(ValueError):  # Pydantic wraps UnknownMcpServerError
        McpProtocolData.model_validate(data)


def test_protocol_data_rejects_missing_required_credentials():
    """github requires GITHUB_TOKEN; if missing, validation fails."""
    data = {
        "server_ref": "github",
        "transport": "stdio",
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "credentials": {},  # missing GITHUB_TOKEN
    }
    with pytest.raises(ValueError) as exc_info:
        McpProtocolData.model_validate(data)
    assert "GITHUB_TOKEN" in str(exc_info.value)


def test_protocol_data_accepts_free_form_no_server_ref():
    """Connections without server_ref work as before."""
    data = {
        "transport": "stdio",
        "command": ["my-custom-mcp"],
        "args": ["--flag"],
    }
    McpProtocolData.model_validate(data)


def test_protocol_data_accepts_capability_cache_fields():
    data = {
        "transport": "stdio",
        "command": ["echo"],
        "discovered_tools": [
            {"name": "tool_a", "description": "a tool", "input_schema": {"type": "object"}},
        ],
        "last_discovered_at": "2026-05-25T12:00:00+00:00",
    }
    McpProtocolData.model_validate(data)


def test_sensitive_field_paths_for_github_returns_password_credentials():
    plugin = McpProtocolPlugin()
    conn = _make_conn({
        "server_ref": "github",
        "transport": "stdio",
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "credentials": {"GITHUB_TOKEN": "ghp_xxx"},
    })
    paths = plugin.sensitive_field_paths_for(conn)
    assert paths == ("credentials.GITHUB_TOKEN",)


def test_sensitive_field_paths_for_slack_returns_only_password_fields():
    plugin = McpProtocolPlugin()
    conn = _make_conn({
        "server_ref": "slack",
        "transport": "stdio",
        "command": ["npx", "-y", "@modelcontextprotocol/server-slack"],
        "credentials": {"SLACK_BOT_TOKEN": "xoxb-x", "SLACK_TEAM_ID": "T123"},
    })
    paths = plugin.sensitive_field_paths_for(conn)
    # SLACK_BOT_TOKEN is password; SLACK_TEAM_ID is text — only the password
    assert paths == ("credentials.SLACK_BOT_TOKEN",)


def test_sensitive_field_paths_for_filesystem_returns_empty():
    """No credentials = no sensitive paths."""
    plugin = McpProtocolPlugin()
    conn = _make_conn({
        "server_ref": "filesystem",
        "transport": "stdio",
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
        "args": ["/tmp/safe-root"],
    })
    assert plugin.sensitive_field_paths_for(conn) == ()


def test_sensitive_field_paths_for_free_form_masks_all_credentials():
    """Free-form (no server_ref) — defensively mask every credential."""
    plugin = McpProtocolPlugin()
    conn = _make_conn({
        "transport": "stdio",
        "command": ["custom-server"],
        "credentials": {"SECRET_KEY": "abc", "PUBLIC_FLAG": "true"},
    })
    paths = set(plugin.sensitive_field_paths_for(conn))
    assert paths == {"credentials.SECRET_KEY", "credentials.PUBLIC_FLAG"}


def test_public_view_masks_password_credentials_for_github():
    plugin = McpProtocolPlugin()
    pd = {
        "server_ref": "github",
        "transport": "stdio",
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "credentials": {"GITHUB_TOKEN": "ghp_real_secret"},
    }
    view = plugin.public_view(pd)
    assert view["credentials"]["GITHUB_TOKEN"] == "***"
    assert view["server_ref"] == "github"  # non-sensitive preserved


def test_public_view_include_secrets_returns_plaintext():
    plugin = McpProtocolPlugin()
    pd = {
        "server_ref": "github",
        "transport": "stdio",
        "command": ["x"],
        "credentials": {"GITHUB_TOKEN": "ghp_real"},
    }
    assert (
        plugin.public_view(pd, include_secrets=True)["credentials"]["GITHUB_TOKEN"]
        == "ghp_real"
    )


def test_public_view_preserves_text_credentials():
    """Slack's SLACK_TEAM_ID is type=text — not masked."""
    plugin = McpProtocolPlugin()
    pd = {
        "server_ref": "slack",
        "transport": "stdio",
        "command": ["x"],
        "credentials": {"SLACK_BOT_TOKEN": "xoxb-secret", "SLACK_TEAM_ID": "T123"},
    }
    view = plugin.public_view(pd)
    assert view["credentials"]["SLACK_BOT_TOKEN"] == "***"
    assert view["credentials"]["SLACK_TEAM_ID"] == "T123"


def test_get_sensitive_field_paths_for_helper_dispatches_to_plugin_method():
    """The base.py helper finds and calls plugin.sensitive_field_paths_for."""
    plugin = McpProtocolPlugin()
    conn = _make_conn({
        "server_ref": "github",
        "transport": "stdio",
        "command": ["x"],
        "credentials": {"GITHUB_TOKEN": "x"},
    })
    paths = get_sensitive_field_paths_for(plugin, conn)
    assert paths == ("credentials.GITHUB_TOKEN",)


def test_get_sensitive_field_paths_for_helper_falls_back_to_classvar():
    """Plugins without sensitive_field_paths_for fall back to the ClassVar."""
    # The http plugin has no per-record method.
    from sagewai.connections.protocols.http import HttpProtocolPlugin

    plugin = HttpProtocolPlugin()
    conn = _make_conn(
        {
            "base_url": "https://api.example.com",
            "auth": {"kind": "none"},
        },
        protocol="http",
    )
    paths = get_sensitive_field_paths_for(plugin, conn)
    assert paths == plugin.sensitive_fields  # static tuple


# ── test() integration: discovery + cache persistence ──────────────


@pytest.mark.asyncio
async def test_test_method_discovers_tools_and_caches_them(monkeypatch, tmp_path):
    """test() runs list_tools, persists discovered_tools to the record."""
    from cryptography.fernet import Fernet

    from sagewai.admin.state_file import AdminStateFile
    from sagewai.connections.bootstrap import build_connections_context

    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "store.json"))
    sf = AdminStateFile(tmp_path / "admin-state.json")
    bootstrap = build_connections_context(sf)
    plugin = McpProtocolPlugin()

    # Create an MCP connection with server_ref=github
    initial_pd = {
        "server_ref": "github",
        "transport": "stdio",
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "credentials": {"GITHUB_TOKEN": "ghp_test"},
    }
    conn = bootstrap.store.create(
        protocol="mcp",
        project_id="default",
        display_name="Test GitHub",
        tags=[],
        protocol_data=initial_pd,
    )
    # Encrypt credentials per per-record paths.
    encrypted_pd = bootstrap.router.encrypt(
        bootstrap.store.get(conn.id).protocol_data,
        sensitive_field_paths=plugin.sensitive_field_paths_for(conn),
        connection_credentials_backend=None,
    )
    bootstrap.store.update(conn.id, protocol_data=encrypted_pd)

    # Mock the MCPClient adapter so test() doesn't try to spawn npx
    class _FakeTool:
        def __init__(self, name, description=""):
            self.name = name
            self.description = description
            self.input_schema = {}

    async def _fake_aenter(self):
        self._tools = [
            _FakeTool("list_issues", "list github issues"),
            _FakeTool("create_pr"),
        ]
        return self

    async def _fake_aexit(self, *exc):
        pass

    monkeypatch.setattr(
        "sagewai.connections.protocols.mcp.MCPClient.__aenter__", _fake_aenter
    )
    monkeypatch.setattr(
        "sagewai.connections.protocols.mcp.MCPClient.__aexit__", _fake_aexit
    )

    # Run test()
    plugin_ctx = bootstrap.make_plugin_context(project_id="default", request=None)
    fresh = bootstrap.store.get(conn.id)
    result = await plugin.test(fresh, ctx=plugin_ctx)
    assert result.ok is True

    # Verify cache populated
    updated = bootstrap.store.get(conn.id)
    cached_tools = updated.protocol_data.get("discovered_tools")
    assert cached_tools is not None
    assert len(cached_tools) == 2
    assert {t["name"] for t in cached_tools} == {"list_issues", "create_pr"}
    assert updated.protocol_data.get("last_discovered_at") is not None
    assert updated.status == "ready"


@pytest.mark.asyncio
async def test_test_method_returns_not_ok_on_connect_failure(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet

    from sagewai.admin.state_file import AdminStateFile
    from sagewai.connections.bootstrap import build_connections_context

    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "store.json"))
    sf = AdminStateFile(tmp_path / "admin-state.json")
    bootstrap = build_connections_context(sf)

    conn = bootstrap.store.create(
        protocol="mcp",
        project_id="default",
        display_name="Bad",
        tags=[],
        protocol_data={
            "server_ref": "filesystem",
            "transport": "stdio",
            "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
            "args": ["/tmp/fake"],
        },
    )

    async def _raise_aenter(self):
        raise ConnectionError("subprocess died")

    monkeypatch.setattr(
        "sagewai.connections.protocols.mcp.MCPClient.__aenter__", _raise_aenter
    )

    plugin = McpProtocolPlugin()
    plugin_ctx = bootstrap.make_plugin_context(project_id="default", request=None)
    fresh = bootstrap.store.get(conn.id)
    result = await plugin.test(fresh, ctx=plugin_ctx)
    assert result.ok is False
    assert "subprocess died" in (result.message or "")


# ── extra_routes: TestClient integration ──────────────────────────────


@pytest.fixture
def _mcp_test_app(tmp_path, monkeypatch):
    """Mount the MCP plugin's extra_routes under a TestClient."""
    from cryptography.fernet import Fernet
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sagewai.admin.state_file import AdminStateFile
    from sagewai.connections.bootstrap import build_connections_context
    from sagewai.connections.protocols import mcp as mcp_module

    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "store.json"))
    sf = AdminStateFile(tmp_path / "admin-state.json")
    ctx = build_connections_context(sf)
    mcp_module._test_inject_context(ctx)

    fastapi_app = FastAPI()
    plugin = McpProtocolPlugin()
    fastapi_app.include_router(
        plugin.extra_routes(), prefix=f"/api/v1/admin/connections/{plugin.id}"
    )
    client = TestClient(fastapi_app)
    yield client, ctx
    mcp_module._test_inject_context(None)


def test_servers_route_returns_registry_entries(_mcp_test_app):
    """GET /servers returns the 7 seeded entries."""
    client, _ctx = _mcp_test_app
    resp = client.get("/api/v1/admin/connections/mcp/servers")
    assert resp.status_code == 200
    body = resp.json()
    ids = {e["id"] for e in body}
    assert ids == {
        "filesystem",
        "github",
        "fetch",
        "postgres",
        "sqlite",
        "brave-search",
        "slack",
    }
    # Verify shape on the github entry.
    gh = next(e for e in body if e["id"] == "github")
    assert gh["display_name"] == "GitHub"
    assert gh["transport"] == "stdio"
    names = {f["name"] for f in gh["credential_fields"]}
    assert names == {"GITHUB_TOKEN"}


def test_tools_route_reads_cached_tools(_mcp_test_app):
    """GET /{id}/tools returns the cached list without re-discovery."""
    client, ctx = _mcp_test_app
    conn = ctx.store.create(
        protocol="mcp", project_id="default", display_name="cached", tags=[],
        protocol_data={
            "server_ref": "filesystem",
            "transport": "stdio",
            "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
            "args": ["/tmp"],
            "discovered_tools": [
                {"name": "read_file", "description": "read", "input_schema": {}},
                {"name": "write_file", "description": "write", "input_schema": {}},
            ],
            "last_discovered_at": "2026-05-25T12:00:00+00:00",
        },
    )
    resp = client.get(f"/api/v1/admin/connections/mcp/{conn.id}/tools")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["tools"]) == 2
    assert {t["name"] for t in body["tools"]} == {"read_file", "write_file"}
    assert body["last_discovered_at"] == "2026-05-25T12:00:00+00:00"


def test_tools_route_404_for_missing_id(_mcp_test_app):
    client, _ctx = _mcp_test_app
    resp = client.get("/api/v1/admin/connections/mcp/conn_nope/tools")
    assert resp.status_code == 404


def test_tools_route_400_for_non_mcp_connection(_mcp_test_app):
    client, ctx = _mcp_test_app
    conn = ctx.store.create(
        protocol="http", project_id="default", display_name="http", tags=[],
        protocol_data={"base_url": "https://api.example.com", "auth": {"kind": "none"}},
    )
    resp = client.get(f"/api/v1/admin/connections/mcp/{conn.id}/tools")
    assert resp.status_code == 400


def test_refresh_route_re_discovers_tools(_mcp_test_app, monkeypatch):
    """POST /{id}/refresh runs test() and returns the masked record."""
    client, ctx = _mcp_test_app
    plugin = McpProtocolPlugin()
    initial_pd = {
        "server_ref": "filesystem",
        "transport": "stdio",
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
        "args": ["/tmp"],
    }
    conn = ctx.store.create(
        protocol="mcp", project_id="default", display_name="fs", tags=[],
        protocol_data=initial_pd,
    )

    class _FakeTool:
        def __init__(self, name, description=""):
            self.name = name
            self.description = description
            self.input_schema = {}

    async def _fake_aenter(self):
        self._tools = [_FakeTool("read_file", "read")]
        return self

    async def _fake_aexit(self, *exc):
        pass

    monkeypatch.setattr(
        "sagewai.connections.protocols.mcp.MCPClient.__aenter__", _fake_aenter
    )
    monkeypatch.setattr(
        "sagewai.connections.protocols.mcp.MCPClient.__aexit__", _fake_aexit
    )

    resp = client.post(f"/api/v1/admin/connections/mcp/{conn.id}/refresh")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["discovered_tools"]) == 1
    assert body["discovered_tools"][0]["name"] == "read_file"


def test_refresh_route_404_for_missing_id(_mcp_test_app):
    client, _ctx = _mcp_test_app
    resp = client.post("/api/v1/admin/connections/mcp/conn_nope/refresh")
    assert resp.status_code == 404


def test_refresh_route_502_when_test_fails(_mcp_test_app, monkeypatch):
    client, ctx = _mcp_test_app
    conn = ctx.store.create(
        protocol="mcp", project_id="default", display_name="bad", tags=[],
        protocol_data={
            "server_ref": "filesystem",
            "transport": "stdio",
            "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
            "args": ["/tmp"],
        },
    )

    async def _raise_aenter(self):
        raise ConnectionError("subprocess died")

    monkeypatch.setattr(
        "sagewai.connections.protocols.mcp.MCPClient.__aenter__", _raise_aenter
    )
    resp = client.post(f"/api/v1/admin/connections/mcp/{conn.id}/refresh")
    assert resp.status_code == 502
    assert "subprocess died" in resp.text
