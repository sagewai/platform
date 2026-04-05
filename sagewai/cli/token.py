# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Token CLI commands — list, create, revoke, and delete API tokens."""

from __future__ import annotations

import click

import sagewai.cli as _cli


@click.group()
def token() -> None:
    """Manage API tokens — list, create, revoke, and delete.

    \b
    Examples:
      sagewai token list                              List all tokens
      sagewai token create --agent MyAgent --scopes chat,tools
      sagewai token revoke <token-id>
    """


@token.command("list")
@click.option(
    "--agent", "agent_name", default=None, help="Filter by agent name."
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def token_list(agent_name: str | None, as_json: bool) -> None:
    """List API tokens."""
    qs = f"?agent_name={agent_name}" if agent_name else ""
    data = _cli._api_get(f"/api/v1/tokens/{qs}")
    if as_json:
        _cli._echo_json(data)
        return
    if not data:
        click.echo("No tokens found.")
        return
    rows = [
        {
            "token_id": t.get("token_id", "")[:12],
            "agent": t.get("agent_name", ""),
            "scopes": ", ".join(t.get("scopes", [])),
            "status": t.get("status", ""),
        }
        for t in data
    ]
    _cli._echo_table(rows, ["token_id", "agent", "scopes", "status"])


@token.command("create")
@click.option(
    "--agent", "agent_name", required=True, help="Agent name for the token."
)
@click.option("--scopes", default="chat", help="Comma-separated scopes.")
@click.option(
    "--expires-in", default=86400, type=int, help="Expiry in seconds."
)
def token_create(agent_name: str, scopes: str, expires_in: int) -> None:
    """Create a new API token."""
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    data = _cli._api_post(
        "/api/v1/tokens/",
        {
            "agent_name": agent_name,
            "scopes": scope_list,
            "expires_in_seconds": expires_in,
        },
    )
    click.echo(
        f"Token created for agent '{data.get('agent_name', agent_name)}'"
    )
    click.echo(f"  Token ID : {data.get('token_id', '—')}")
    click.echo(f"  Token    : {data.get('token', '—')}")
    click.echo(f"  Scopes   : {', '.join(data.get('scopes', []))}")
    click.echo(f"  Expires  : {data.get('expires_in_seconds', expires_in)}s")
    click.echo("\nSave this token now — it won't be shown again.")


@token.command("revoke")
@click.argument("token_id")
def token_revoke(token_id: str) -> None:
    """Revoke an API token."""
    _cli._api_post(f"/api/v1/tokens/{token_id}/revoke")
    click.echo(f"Token {token_id} revoked.")


@token.command("delete")
@click.argument("token_id")
def token_delete(token_id: str) -> None:
    """Delete an API token permanently."""
    _cli._api_delete(f"/api/v1/tokens/{token_id}")
    click.echo(f"Token {token_id} deleted.")
