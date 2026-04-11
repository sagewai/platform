# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Agent CLI commands — list, run, and chat with registered agents."""

from __future__ import annotations

from typing import Any

import click

import sagewai.cli as _cli


@click.group()
def agent() -> None:
    """Manage agents — list, run, or chat with registered agents.

    \b
    Examples:
      sagewai agent list                          List registered agents
      sagewai agent run --name MyAgent -m "Hi"    Send a message to an agent
      sagewai agent chat --name MyAgent           Interactive chat session
      sagewai agent api-list                      List agents from admin API
    """


@agent.command("list")
def agent_list() -> None:
    """List all agents in the global AgentRegistry."""
    from sagewai.core.registry import AgentRegistry

    registry = AgentRegistry.get_instance()
    agents = registry.list_agents()

    if not agents:
        click.echo("No agents registered in the global registry.")
        click.echo(
            "Register agents in your application code or via a YAML workflow."
        )
        return

    rows = [
        {"name": name, "capabilities": ", ".join(caps)}
        for name, caps in agents.items()
    ]
    _cli._echo_table(rows, ["name", "capabilities"])


@agent.command("run")
@click.option(
    "--name",
    required=True,
    help="Agent name (must be registered in the registry).",
)
@click.option(
    "--message", "-m", required=True, help="Message to send to the agent."
)
@click.option("--model", default=None, help="Override the agent's model.")
def agent_run(name: str, message: str, model: str | None) -> None:
    """Run a single message through a named agent."""
    from sagewai.core.registry import AgentRegistry

    registry = AgentRegistry.get_instance()
    target = registry.get(name)
    if target is None:
        click.echo(
            f"Error: agent '{name}' not found in the registry.", err=True
        )
        raise SystemExit(1)

    if model:
        target.config.model = model

    result = _cli._run_async(target.chat(message))
    click.echo(result)


@agent.command("chat")
@click.option("--name", required=True, help="Agent name to chat with.")
@click.option("--model", default=None, help="Override the agent's model.")
def agent_chat(name: str, model: str | None) -> None:
    """Start an interactive chat session with an agent."""
    from sagewai.core.registry import AgentRegistry

    registry = AgentRegistry.get_instance()
    target = registry.get(name)
    if target is None:
        click.echo(
            f"Error: agent '{name}' not found in the registry.", err=True
        )
        raise SystemExit(1)

    if model:
        target.config.model = model

    click.echo(
        f"Chatting with agent '{name}'. Type 'exit' or Ctrl+D to quit.\n"
    )
    while True:
        try:
            user_input = click.prompt("you", prompt_suffix="> ")
        except (EOFError, click.Abort):
            click.echo("\nGoodbye.")
            break
        if user_input.strip().lower() in ("exit", "quit"):
            click.echo("Goodbye.")
            break
        response = _cli._run_async(target.chat(user_input))
        click.echo(f"\n{response}\n")


@agent.command("api-list")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def agent_api_list(as_json: bool) -> None:
    """List agents from the admin API."""
    data = _cli._api_get("/admin/agents")
    if as_json:
        _cli._echo_json(data)
        return
    rows = [
        {
            "name": a.get("name", ""),
            "status": a.get("status", ""),
            "model": a.get("model", ""),
        }
        for a in data
    ]
    _cli._echo_table(rows, ["name", "status", "model"])


@agent.command("api-show")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def agent_api_show(name: str, as_json: bool) -> None:
    """Show agent detail from the admin API."""
    data = _cli._api_get(f"/admin/agents/{name}")
    if as_json:
        _cli._echo_json(data)
        return
    click.echo(f"Agent: {data.get('name', name)}")
    click.echo(f"  Model         : {data.get('model', '—')}")
    click.echo(f"  Status        : {data.get('status', '—')}")
    click.echo(f"  Total runs    : {data.get('total_runs', 0)}")
    click.echo(f"  Max iterations: {data.get('max_iterations', '—')}")
    caps = data.get("capabilities", [])
    if caps:
        click.echo(f"  Capabilities  : {', '.join(caps)}")
    tools: list[Any] = data.get("tools", [])
    if tools:
        click.echo(f"  Tools         : {', '.join(tools)}")
