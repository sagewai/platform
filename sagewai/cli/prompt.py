# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Prompt CLI commands — list, show, and replay prompt logs."""

from __future__ import annotations

import click

import sagewai.cli as _cli


@click.group()
def prompt() -> None:
    """Manage prompt logs — list, show, replay, and export.

    \b
    Examples:
      sagewai prompt list --agent MyAgent     List prompt logs for an agent
      sagewai prompt show <log-id>            Show prompt detail
      sagewai prompt replay <log-id> --model claude-3-5-sonnet
    """


@prompt.command("list")
@click.option(
    "--agent", "agent_name", default=None, help="Filter by agent name."
)
@click.option("--model", "model_name", default=None, help="Filter by model.")
@click.option("--limit", default=20, help="Max logs to show.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def prompt_list(
    agent_name: str | None,
    model_name: str | None,
    limit: int,
    as_json: bool,
) -> None:
    """List prompt logs from the admin API."""
    params: list[str] = [f"limit={limit}"]
    if agent_name:
        params.append(f"agent_name={agent_name}")
    if model_name:
        params.append(f"model={model_name}")
    qs = "?" + "&".join(params) if params else ""

    data = _cli._api_get(f"/api/v1/prompts/logs{qs}")
    if as_json:
        _cli._echo_json(data)
        return
    if not data:
        click.echo("No prompt logs found.")
        return
    rows = [
        {
            "log_id": entry.get("log_id", "")[:12],
            "agent": entry.get("agent_name", ""),
            "model": entry.get("model", ""),
            "tokens": str(
                entry.get("input_tokens", 0) + entry.get("output_tokens", 0)
            ),
            "cost": f"${entry.get('cost_usd', 0):.4f}",
        }
        for entry in data
    ]
    _cli._echo_table(rows, ["log_id", "agent", "model", "tokens", "cost"])


@prompt.command("show")
@click.argument("log_id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def prompt_show(log_id: str, as_json: bool) -> None:
    """Show a prompt log detail."""
    data = _cli._api_get(f"/api/v1/prompts/logs/{log_id}")
    if as_json:
        _cli._echo_json(data)
        return
    click.echo(f"Log: {data.get('log_id', log_id)}")
    click.echo(f"  Agent    : {data.get('agent_name', '—')}")
    click.echo(f"  Model    : {data.get('model', '—')}")
    click.echo(f"  Duration : {data.get('duration_ms', 0)}ms")
    click.echo(f"  Cost     : ${data.get('cost_usd', 0):.4f}")
    click.echo()
    for msg in data.get("prompt_messages", []):
        click.echo(
            f"  [{msg.get('role', '?')}] {msg.get('content', '')[:200]}"
        )
    resp = data.get("response_message", {})
    if resp:
        click.echo(f"\n  [assistant] {resp.get('content', '')[:200]}")


@prompt.command("replay")
@click.argument("log_id")
@click.option("--model", required=True, help="Model to replay with.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def prompt_replay(log_id: str, model: str, as_json: bool) -> None:
    """Replay a prompt log with a different model."""
    data = _cli._api_post(
        "/api/v1/prompts/replay", {"log_id": log_id, "model": model}
    )
    if as_json:
        _cli._echo_json(data)
        return
    click.echo(f"Original ({data.get('original_model', '—')}):")
    click.echo(
        f"  {data.get('original_response', {}).get('content', '')[:300]}"
    )
    click.echo(f"\nReplay ({data.get('replay_model', model)}):")
    click.echo(
        f"  {data.get('replay_response', {}).get('content', '')[:300]}"
    )
