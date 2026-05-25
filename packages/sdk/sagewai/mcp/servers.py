# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Static MCP server registry.

Mirrors the OAuth provider registry pattern from PR #356. Operators
pick a registry entry when registering an MCP connection; the registry
declares the default command/args/url-template and the credential fields
the server requires.

The seeded set covers the canonical 2026 MCP ecosystem reference servers
shipped via ``npx -y @modelcontextprotocol/server-<id>``. Adding a new
entry is a one-row code change reviewed alongside the tools that need it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class UnknownMcpServerError(KeyError):
    """Lookup for an MCP server id that isn't in the registry."""


@dataclass(frozen=True)
class McpCredentialField:
    """One credential the MCP server requires.

    Attributes:
        name: env var or header key name (e.g., ``GITHUB_TOKEN``).
        label: operator-facing display label.
        type: ``password`` (masked in UI + stored encrypted) or ``text``.
        injection: ``env`` for stdio transport subprocess env vars,
                   ``header`` for http/sse transport request headers.
        description: optional operator-facing help text.
        header_name: when ``injection="header"``, the header key. Defaults
                     to ``Authorization`` when None.
        header_value_template: when ``injection="header"``, the header
                               value template with ``{value}`` placeholder.
                               Defaults to ``{value}`` when None.
    """

    name: str
    label: str
    type: Literal["password", "text"]
    injection: Literal["env", "header"]
    description: str | None = None
    header_name: str | None = None
    header_value_template: str | None = None


@dataclass(frozen=True)
class McpServerEntry:
    id: str
    display_name: str
    transport: Literal["stdio", "http", "sse"]
    default_command: list[str] | None = None
    default_args: list[str] | None = None
    default_url_template: str | None = None
    credential_fields: tuple[McpCredentialField, ...] = ()
    docs_url: str = ""
    description: str = ""


MCP_SERVERS: tuple[McpServerEntry, ...] = (
    McpServerEntry(
        id="filesystem",
        display_name="Filesystem",
        transport="stdio",
        default_command=["npx", "-y", "@modelcontextprotocol/server-filesystem"],
        default_args=[],
        credential_fields=(),
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        description="Read/write files on a chrooted path.",
    ),
    McpServerEntry(
        id="github",
        display_name="GitHub",
        transport="stdio",
        default_command=["npx", "-y", "@modelcontextprotocol/server-github"],
        default_args=[],
        credential_fields=(
            McpCredentialField(
                name="GITHUB_TOKEN",
                label="GitHub Personal Access Token",
                type="password",
                injection="env",
                description="Create at github.com/settings/tokens (repo + read:org scopes).",
            ),
        ),
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/github",
        description="Issues, pull requests, code search.",
    ),
    McpServerEntry(
        id="fetch",
        display_name="Fetch (HTTP)",
        transport="stdio",
        default_command=["npx", "-y", "@modelcontextprotocol/server-fetch"],
        default_args=[],
        credential_fields=(),
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
        description="Make arbitrary HTTP requests as a tool.",
    ),
    McpServerEntry(
        id="postgres",
        display_name="PostgreSQL",
        transport="stdio",
        default_command=["npx", "-y", "@modelcontextprotocol/server-postgres"],
        default_args=[],
        credential_fields=(
            McpCredentialField(
                name="DATABASE_URL",
                label="Postgres connection string",
                type="password",
                injection="env",
                description="e.g., postgresql://user:pass@host:5432/dbname",
            ),
        ),
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
        description="Query a Postgres database (read-only by default).",
    ),
    McpServerEntry(
        id="sqlite",
        display_name="SQLite",
        transport="stdio",
        default_command=["npx", "-y", "@modelcontextprotocol/server-sqlite"],
        default_args=[],
        credential_fields=(),
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/sqlite",
        description="Query a local SQLite database file.",
    ),
    McpServerEntry(
        id="brave-search",
        display_name="Brave Search",
        transport="stdio",
        default_command=["npx", "-y", "@modelcontextprotocol/server-brave-search"],
        default_args=[],
        credential_fields=(
            McpCredentialField(
                name="BRAVE_API_KEY",
                label="Brave Search API Key",
                type="password",
                injection="env",
                description="Get from api.search.brave.com — free tier 2k queries/month.",
            ),
        ),
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
        description="Web search via Brave Search API.",
    ),
    McpServerEntry(
        id="slack",
        display_name="Slack",
        transport="stdio",
        default_command=["npx", "-y", "@modelcontextprotocol/server-slack"],
        default_args=[],
        credential_fields=(
            McpCredentialField(
                name="SLACK_BOT_TOKEN",
                label="Slack Bot Token",
                type="password",
                injection="env",
                description="xoxb-... from api.slack.com/apps -> OAuth & Permissions.",
            ),
            McpCredentialField(
                name="SLACK_TEAM_ID",
                label="Slack Team ID",
                type="text",
                injection="env",
                description="T... — find via team settings or Slack URL.",
            ),
        ),
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
        description="Post messages, search history, list channels.",
    ),
)

_BY_ID = {e.id: e for e in MCP_SERVERS}


def get_server(server_id: str) -> McpServerEntry:
    """Look up a registry entry by id; raises :class:`UnknownMcpServerError`."""
    try:
        return _BY_ID[server_id]
    except KeyError as exc:
        raise UnknownMcpServerError(server_id) from exc


def all_servers() -> tuple[McpServerEntry, ...]:
    """Return every registered server in declaration order."""
    return MCP_SERVERS


__all__ = [
    "MCP_SERVERS",
    "McpCredentialField",
    "McpServerEntry",
    "UnknownMcpServerError",
    "all_servers",
    "get_server",
]
