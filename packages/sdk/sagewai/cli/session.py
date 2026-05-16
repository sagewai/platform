# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Session CLI commands — list, show, and inspect conversation history."""

from __future__ import annotations

import click

from sagewai.cli import _helpers


@click.group()
def session() -> None:
    """Manage sessions — list, show, and inspect conversation history.

    \b
    Examples:
      sagewai session list                   List active sessions
      sagewai session show <session-id>      Show session detail
      sagewai session messages <session-id>  Show conversation messages
    """


@session.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def session_list(as_json: bool) -> None:
    """List active sessions."""
    data = _helpers._api_get("/admin/sessions")
    if as_json:
        _helpers._echo_json(data)
        return
    if isinstance(data, dict):
        items = data.get("items", [])
    elif isinstance(data, list):
        items = data
    else:
        click.echo(f"Unexpected response format: {type(data).__name__}")
        return
    rows = [
        {
            "session_id": s.get("session_id", "")[:12],
            "agent": s.get("agent_name", ""),
            "messages": s.get("message_count", 0),
        }
        for s in items
    ]
    _helpers._echo_table(rows, ["session_id", "agent", "messages"])


@session.command("show")
@click.argument("session_id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def session_show(session_id: str, as_json: bool) -> None:
    """Show session detail."""
    data = _helpers._api_get(f"/admin/sessions/{session_id}")
    if as_json:
        _helpers._echo_json(data)
        return
    click.echo(f"Session: {data.get('session_id', session_id)}")
    click.echo(f"  Agent    : {data.get('agent_name', '—')}")
    click.echo(f"  Messages : {data.get('message_count', 0)}")


@session.command("messages")
@click.argument("session_id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def session_messages(session_id: str, as_json: bool) -> None:
    """Show conversation messages for a session."""
    data = _helpers._api_get(f"/api/v1/sessions/{session_id}/messages")
    if as_json:
        _helpers._echo_json(data)
        return
    click.echo(f"Session: {data.get('session_id', session_id)}")
    click.echo(f"Agent  : {data.get('agent_name', '—')}")
    click.echo(f"Messages: {data.get('total_messages', 0)}")
    click.echo()
    for msg in data.get("messages", []):
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        click.echo(f"  [{role}] {content}")
