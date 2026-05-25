# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""``sagewai connections`` generic CLI tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from cryptography.fernet import Fernet

from sagewai.cli.connections import connections


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(tmp_path / "admin-state.json"))
    yield tmp_path


def _runner() -> CliRunner:
    return CliRunner()


# ── protocols ──────────────────────────────────────────────────────


def test_protocols_lists_5():
    result = _runner().invoke(connections, ["protocols"])
    assert result.exit_code == 0, result.output
    # Plain text table has 5 protocol ids
    for pid in ["http", "sdk", "mcp", "inference", "oauth2"]:
        assert pid in result.output


def test_protocols_json():
    result = _runner().invoke(connections, ["protocols", "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    ids = {p["id"] for p in body}
    assert ids == {"http", "sdk", "mcp", "inference", "oauth2"}


# ── backends ───────────────────────────────────────────────────────


def test_backends_lists_3():
    result = _runner().invoke(connections, ["backends", "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    ids = {b["id"] for b in body}
    assert ids == {"local", "env", "sops"}


# ── list ────────────────────────────────────────────────────────────


def test_list_empty_returns_no_records():
    result = _runner().invoke(connections, ["list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_list_human():
    result = _runner().invoke(connections, ["list"])
    assert result.exit_code == 0, result.output
    assert "no records" in result.output.lower()


# ── add ────────────────────────────────────────────────────────────


def test_add_http_connection_succeeds():
    data = json.dumps({"base_url": "https://api.x", "auth": {"kind": "none"}})
    result = _runner().invoke(
        connections,
        [
            "add", "http",
            "--display-name", "HTTP X",
            "--data", data,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["protocol"] == "http"
    assert body["display_name"] == "HTTP X"


def test_add_oauth2_connection_masks_secret():
    data = json.dumps({
        "provider": "spotify", "client_id": "c", "client_secret": "MY-SECRET",
        "redirect_uri": "http://localhost/cb",
        "requested_scopes": ["user-read-private"],
        "granted_scopes": [], "tokens": None,
    })
    result = _runner().invoke(
        connections,
        ["add", "oauth2", "--display-name", "S",
         "--data", data, "--json"],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["protocol_data"]["client_secret"] == "***"
    # The plaintext secret never appears in the CLI output
    assert "MY-SECRET" not in result.output


def test_add_invalid_protocol_data_fails():
    data = json.dumps({"provider": "spotify", "requested_scopes": ["s"]})  # missing required
    result = _runner().invoke(
        connections,
        ["add", "oauth2", "--display-name", "Bad", "--data", data],
    )
    assert result.exit_code != 0


def test_add_unknown_protocol_fails():
    result = _runner().invoke(
        connections,
        ["add", "nonexistent", "--display-name", "X", "--data", "{}"],
    )
    assert result.exit_code != 0


# ── get ─────────────────────────────────────────────────────────────


def test_get_404_for_missing():
    result = _runner().invoke(connections, ["get", "nope"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_get_returns_record():
    add_data = json.dumps({"base_url": "https://api.x", "auth": {"kind": "none"}})
    add_result = _runner().invoke(
        connections,
        ["add", "http", "--display-name", "X", "--data", add_data, "--json"],
    )
    body = json.loads(add_result.output)
    cid = body["id"]
    result = _runner().invoke(connections, ["get", cid, "--json"])
    assert result.exit_code == 0, result.output
    fetched = json.loads(result.output)
    assert fetched["id"] == cid


# ── update ──────────────────────────────────────────────────────────


def test_update_display_name():
    add_data = json.dumps({"base_url": "https://api.x", "auth": {"kind": "none"}})
    add_result = _runner().invoke(
        connections,
        ["add", "http", "--display-name", "Before", "--data", add_data, "--json"],
    )
    cid = json.loads(add_result.output)["id"]
    result = _runner().invoke(
        connections,
        ["update", cid, "--display-name", "After", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["display_name"] == "After"


def test_update_404_for_missing():
    result = _runner().invoke(connections, ["update", "nope", "--display-name", "X"])
    assert result.exit_code != 0


# ── delete ──────────────────────────────────────────────────────────


def test_delete_with_yes_succeeds():
    add_data = json.dumps({"base_url": "https://api.x", "auth": {"kind": "none"}})
    add_result = _runner().invoke(
        connections,
        ["add", "http", "--display-name", "X", "--data", add_data, "--json"],
    )
    cid = json.loads(add_result.output)["id"]
    result = _runner().invoke(connections, ["delete", cid, "--yes"])
    assert result.exit_code == 0, result.output
    # confirm gone
    get_result = _runner().invoke(connections, ["get", cid])
    assert get_result.exit_code != 0


def test_delete_404_for_missing():
    result = _runner().invoke(connections, ["delete", "nope", "--yes"])
    assert result.exit_code != 0


# ── test ────────────────────────────────────────────────────────────


def test_test_records_result():
    add_data = json.dumps({
        "provider": "spotify", "client_id": "c", "client_secret": "s",
        "redirect_uri": "http://localhost/cb",
        "requested_scopes": ["user-read-private"],
        "granted_scopes": [], "tokens": None,
    })
    add_result = _runner().invoke(
        connections,
        ["add", "oauth2", "--display-name", "S", "--data", add_data, "--json"],
    )
    cid = json.loads(add_result.output)["id"]
    result = _runner().invoke(connections, ["test", cid, "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["ok"] is False  # no tokens → plugin returns ok=False


def test_test_404_for_missing():
    result = _runner().invoke(connections, ["test", "nope"])
    assert result.exit_code != 0


# ── set-default ─────────────────────────────────────────────────────


def test_set_default_swaps_flag():
    add_data = json.dumps({"base_url": "https://api.x", "auth": {"kind": "none"}})
    a_result = _runner().invoke(
        connections,
        ["add", "http", "--display-name", "A", "--data", add_data, "--json"],
    )
    b_data = json.dumps({"base_url": "https://api.b", "auth": {"kind": "none"}})
    b_result = _runner().invoke(
        connections,
        ["add", "http", "--display-name", "B", "--data", b_data, "--json"],
    )
    a_id = json.loads(a_result.output)["id"]
    b_id = json.loads(b_result.output)["id"]
    # a is default by virtue of being first
    assert json.loads(a_result.output)["is_default"] is True
    assert json.loads(b_result.output)["is_default"] is False
    result = _runner().invoke(connections, ["set-default", b_id, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["is_default"] is True


def test_set_default_404_for_missing():
    result = _runner().invoke(connections, ["set-default", "nope"])
    assert result.exit_code != 0


# ── Plugin sub-groups (oauth2 mounted as `sagewai connections oauth2 ...`) ──


def test_oauth2_subgroup_providers_works():
    result = _runner().invoke(connections, ["oauth2", "providers"])
    assert result.exit_code == 0, result.output
    assert "spotify" in result.output
    assert "google" in result.output


# ── MCP subgroup (sagewai connections mcp ...) ─────────────────────


def test_mcp_subgroup_servers_lists_registry():
    """`sagewai connections mcp servers` prints the 7 seeded entries."""
    result = _runner().invoke(connections, ["mcp", "servers"])
    assert result.exit_code == 0, result.output
    for sid in [
        "filesystem", "github", "fetch", "postgres",
        "sqlite", "brave-search", "slack",
    ]:
        assert sid in result.output


def test_mcp_subgroup_servers_json():
    result = _runner().invoke(connections, ["mcp", "servers", "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    ids = {e["id"] for e in body}
    assert "github" in ids
    assert len(body) == 7


def test_mcp_subgroup_tools_404_for_missing():
    result = _runner().invoke(connections, ["mcp", "tools", "conn_nope"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_mcp_subgroup_tools_prints_empty_when_no_cache():
    add_data = json.dumps({
        "transport": "stdio",
        "command": ["echo", "hi"],
    })
    add_result = _runner().invoke(
        connections,
        ["add", "mcp", "--display-name", "X", "--data", add_data, "--json"],
    )
    assert add_result.exit_code == 0, add_result.output
    cid = json.loads(add_result.output)["id"]
    result = _runner().invoke(connections, ["mcp", "tools", cid])
    assert result.exit_code == 0, result.output
    assert "no tools discovered" in result.output


def test_mcp_subgroup_refresh_404_for_missing():
    result = _runner().invoke(connections, ["mcp", "refresh", "conn_nope"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


# ── --server-ref flag on connections add ───────────────────────────


def test_connections_add_with_server_ref_for_non_mcp_protocol_rejects():
    """--server-ref is rejected when protocol != mcp."""
    result = _runner().invoke(
        connections,
        [
            "add", "http", "--display-name", "X",
            "--server-ref", "github",
            "--data", "{}",
        ],
    )
    assert result.exit_code != 0
    assert "--server-ref" in result.output.lower() or "server-ref" in result.output.lower()


def test_connections_add_mcp_without_data_or_server_ref_fails():
    result = _runner().invoke(
        connections,
        ["add", "mcp", "--display-name", "X"],
    )
    assert result.exit_code != 0


def test_connections_add_mcp_with_server_ref_unknown_fails():
    result = _runner().invoke(
        connections,
        [
            "add", "mcp", "--display-name", "X",
            "--server-ref", "not-a-real-server",
        ],
        input="anything\n",
    )
    assert result.exit_code != 0
    assert "unknown mcp server_ref" in result.output


def test_connections_add_mcp_with_server_ref_github_prompts_for_token():
    """--server-ref github prompts for GITHUB_TOKEN; masks in output."""
    result = _runner().invoke(
        connections,
        [
            "add", "mcp", "--display-name", "GH",
            "--server-ref", "github", "--json",
        ],
        input="ghp_super_secret\n",
    )
    assert result.exit_code == 0, result.output
    # The prompt line + JSON payload are mixed in output; extract the JSON tail.
    json_start = result.output.find("{")
    body = json.loads(result.output[json_start:])
    assert body["protocol"] == "mcp"
    assert body["protocol_data"]["server_ref"] == "github"
    # Password masked in the public view.
    assert body["protocol_data"]["credentials"]["GITHUB_TOKEN"] == "***"


def test_connections_add_mcp_with_server_ref_filesystem_prompts_for_path():
    """--server-ref filesystem prompts for path (no credentials)."""
    result = _runner().invoke(
        connections,
        [
            "add", "mcp", "--display-name", "FS",
            "--server-ref", "filesystem", "--json",
        ],
        input="/tmp/safe-root\n",
    )
    assert result.exit_code == 0, result.output
    json_start = result.output.find("{")
    body = json.loads(result.output[json_start:])
    assert body["protocol_data"]["server_ref"] == "filesystem"
    assert "/tmp/safe-root" in body["protocol_data"]["args"]


def test_connections_add_mcp_with_server_ref_slack_prompts_for_both_creds():
    result = _runner().invoke(
        connections,
        [
            "add", "mcp", "--display-name", "SL",
            "--server-ref", "slack", "--json",
        ],
        input="xoxb-secret\nT123\n",
    )
    assert result.exit_code == 0, result.output
    json_start = result.output.find("{")
    body = json.loads(result.output[json_start:])
    creds = body["protocol_data"]["credentials"]
    assert creds["SLACK_BOT_TOKEN"] == "***"  # masked (password)
    assert creds["SLACK_TEAM_ID"] == "T123"   # text — not masked
    # The plaintext token never appears in stdout.
    assert "xoxb-secret" not in result.output


def test_connections_add_mcp_without_server_ref_uses_free_form():
    """No --server-ref → existing --data path still works."""
    data = json.dumps({
        "transport": "stdio", "command": ["my-custom-mcp"],
    })
    result = _runner().invoke(
        connections,
        ["add", "mcp", "--display-name", "FF",
         "--data", data, "--json"],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["protocol_data"].get("server_ref") in (None, "")


def test_oauth2_subgroup_start_404_for_missing():
    result = _runner().invoke(connections, ["oauth2", "start", "nope"])
    assert result.exit_code != 0
