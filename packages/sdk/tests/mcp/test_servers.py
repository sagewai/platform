# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""MCP server registry tests."""
from __future__ import annotations

import pytest

from sagewai.mcp.servers import (
    MCP_SERVERS,
    McpCredentialField,
    McpServerEntry,
    UnknownMcpServerError,
    all_servers,
    get_server,
)


def test_all_servers_returns_7_seeded_entries():
    ids = {s.id for s in all_servers()}
    assert ids == {
        "filesystem",
        "github",
        "fetch",
        "postgres",
        "sqlite",
        "brave-search",
        "slack",
    }


def test_get_server_by_id():
    gh = get_server("github")
    assert gh.id == "github"
    assert gh.display_name == "GitHub"
    assert gh.transport == "stdio"


def test_get_server_unknown_raises():
    with pytest.raises(UnknownMcpServerError):
        get_server("not-a-real-server")


def test_seeded_servers_all_use_npx_default_command():
    """All 7 seed entries ship as npm packages via `npx -y @modelcontextprotocol/server-X`."""
    for entry in all_servers():
        assert entry.default_command is not None, entry.id
        assert entry.default_command[0] == "npx", entry.id
        assert "-y" in entry.default_command, entry.id


def test_github_has_github_token_credential():
    gh = get_server("github")
    names = {f.name for f in gh.credential_fields}
    assert names == {"GITHUB_TOKEN"}
    field = gh.credential_fields[0]
    assert field.type == "password"
    assert field.injection == "env"


def test_slack_has_two_credentials():
    slack = get_server("slack")
    names = {f.name for f in slack.credential_fields}
    assert names == {"SLACK_BOT_TOKEN", "SLACK_TEAM_ID"}
    by_name = {f.name: f for f in slack.credential_fields}
    assert by_name["SLACK_BOT_TOKEN"].type == "password"
    assert by_name["SLACK_TEAM_ID"].type == "text"
    assert by_name["SLACK_BOT_TOKEN"].injection == "env"
    assert by_name["SLACK_TEAM_ID"].injection == "env"


def test_filesystem_and_fetch_have_no_credentials():
    assert get_server("filesystem").credential_fields == ()
    assert get_server("fetch").credential_fields == ()
    assert get_server("sqlite").credential_fields == ()


def test_postgres_has_database_url():
    pg = get_server("postgres")
    names = {f.name for f in pg.credential_fields}
    assert names == {"DATABASE_URL"}


def test_brave_search_has_brave_api_key():
    bs = get_server("brave-search")
    names = {f.name for f in bs.credential_fields}
    assert names == {"BRAVE_API_KEY"}


def test_all_seeded_servers_have_docs_url():
    for entry in all_servers():
        assert entry.docs_url, entry.id
        assert entry.docs_url.startswith("https://"), entry.id


def test_credential_fields_are_immutable_tuples():
    for entry in all_servers():
        assert isinstance(entry.credential_fields, tuple), entry.id


def test_no_seeded_server_uses_header_injection():
    """All 7 seed servers use stdio + env. Header injection is for future http servers."""
    for entry in all_servers():
        for field in entry.credential_fields:
            assert field.injection == "env", f"{entry.id}/{field.name}"
