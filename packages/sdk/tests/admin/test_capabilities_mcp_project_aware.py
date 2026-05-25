# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""CapabilityCatalog.mcp_servers reads from registered MCP connections."""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile
from sagewai.connections.bootstrap import build_connections_context


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())


@pytest.fixture
def _store_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(
        "SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "connections.json")
    )
    monkeypatch.setenv(
        "SAGEWAI_ADMIN_STATE_FILE", str(tmp_path / "admin-state.json")
    )
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
    tc = TestClient(app)
    tc.cookies.set("sagewai_auth", token)
    return tc


@pytest.fixture
def ctx(sf):
    return build_connections_context(sf)


def test_capabilities_mcp_servers_empty_when_no_registered_connections(client):
    """No MCP connections → empty mcp_servers list."""
    resp = client.get(
        "/playground/capabilities", headers={"X-Project-ID": "default"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mcp_servers"] == []
    # Other buckets remain populated.
    assert len(body["tools"]) > 0
    assert len(body["strategies"]) > 0


def test_capabilities_mcp_servers_lists_registered_connections(client, ctx):
    """When MCP connections exist in the project, /capabilities lists them."""
    ctx.store.create(
        protocol="mcp", project_id="default", display_name="GH Test",
        tags=[],
        protocol_data={
            "server_ref": "github",
            "transport": "stdio",
            "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
            "credentials": {"GITHUB_TOKEN": "ghp_x"},
        },
    )
    ctx.store.create(
        protocol="mcp", project_id="default", display_name="FS Test",
        tags=[],
        protocol_data={
            "server_ref": "filesystem",
            "transport": "stdio",
            "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
            "args": ["/tmp"],
        },
    )
    resp = client.get(
        "/playground/capabilities", headers={"X-Project-ID": "default"}
    )
    assert resp.status_code == 200
    body = resp.json()
    names = {entry["name"] for entry in body["mcp_servers"]}
    assert names == {"GH Test", "FS Test"}
    # Description encodes the server_ref + transport + tool count.
    gh_entry = next(e for e in body["mcp_servers"] if e["name"] == "GH Test")
    assert "github" in gh_entry["description"]
    assert "stdio" in gh_entry["description"]
    assert "0 tools" in gh_entry["description"]


def test_capabilities_mcp_servers_project_isolated(client, ctx):
    """MCP connections in project A don't appear when querying project B."""
    ctx.store.create(
        protocol="mcp", project_id="project-a", display_name="A1",
        tags=[],
        protocol_data={
            "server_ref": "filesystem",
            "transport": "stdio",
            "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
            "args": ["/tmp"],
        },
    )
    ctx.store.create(
        protocol="mcp", project_id="project-b", display_name="B1",
        tags=[],
        protocol_data={
            "server_ref": "filesystem",
            "transport": "stdio",
            "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
            "args": ["/tmp"],
        },
    )
    resp_a = client.get(
        "/playground/capabilities", headers={"X-Project-ID": "project-a"}
    )
    resp_b = client.get(
        "/playground/capabilities", headers={"X-Project-ID": "project-b"}
    )
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    names_a = {e["name"] for e in resp_a.json()["mcp_servers"]}
    names_b = {e["name"] for e in resp_b.json()["mcp_servers"]}
    assert names_a == {"A1"}
    assert names_b == {"B1"}


def test_capabilities_includes_discovered_tools_count(client, ctx):
    """Tool count from the capability cache shows in the description."""
    ctx.store.create(
        protocol="mcp", project_id="default", display_name="With Tools",
        tags=[],
        protocol_data={
            "server_ref": "filesystem",
            "transport": "stdio",
            "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
            "args": ["/tmp"],
            "discovered_tools": [
                {"name": "read_file", "description": "", "input_schema": {}},
                {"name": "write_file", "description": "", "input_schema": {}},
                {"name": "list_directory", "description": "", "input_schema": {}},
            ],
        },
    )
    resp = client.get(
        "/playground/capabilities", headers={"X-Project-ID": "default"}
    )
    assert resp.status_code == 200
    body = resp.json()
    entry = next(e for e in body["mcp_servers"] if e["name"] == "With Tools")
    assert "3 tools" in entry["description"]
