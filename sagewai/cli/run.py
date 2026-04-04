# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Run CLI group — interactive agent REPL and run management commands."""

from __future__ import annotations

import click

import sagewai.cli as _cli


@click.group("run", invoke_without_command=True)
@click.option(
    "--agent",
    "agent_name",
    default=None,
    help="Agent name for interactive REPL.",
)
@click.option(
    "--model",
    default=None,
    help="LLM model to use (e.g. gpt-4o, claude-3-5-sonnet).",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True),
    default=None,
    help="YAML config file for the agent.",
)
@click.option(
    "--tools", default=None, help="Comma-separated MCP tool commands."
)
@click.option(
    "--system-prompt",
    "system_prompt",
    default=None,
    help="Custom system prompt.",
)
@click.option(
    "--stream/--no-stream", default=True, help="Stream output (default: on)."
)
@click.pass_context
def run_group(
    ctx: click.Context,
    agent_name: str | None,
    model: str | None,
    config_path: str | None,
    tools: str | None,
    system_prompt: str | None,
    stream: bool,
) -> None:
    """Start an interactive agent REPL, or manage runs via subcommands.

    \b
    Examples:
      sagewai run --agent helper --model gpt-4o
      sagewai run --config agent.yaml
      sagewai run --agent coder --model claude-3-5-sonnet --tools "npx @mcp/filesystem"
      sagewai run api-list          (list runs from admin API)
    """
    if ctx.invoked_subcommand is not None:
        return

    if config_path is None and agent_name is None:
        click.echo(ctx.get_help())
        return

    # Look up via package so tests can patch sagewai.cli._start_agent_repl
    _cli._start_agent_repl(
        agent_name=agent_name,
        model=model,
        config_path=config_path,
        tools=tools,
        system_prompt=system_prompt,
        stream=stream,
    )


def _start_agent_repl(
    *,
    agent_name: str | None,
    model: str | None,
    config_path: str | None,
    tools: str | None,
    system_prompt: str | None,
    stream: bool,
) -> None:
    """Launch an interactive REPL with a UniversalAgent."""

    async def _setup_and_run() -> None:
        from sagewai.engines.universal import UniversalAgent
        from sagewai.models.tool import ToolSpec

        agent_tools: list[ToolSpec] = []

        # Load config from YAML if provided
        if config_path:
            import yaml

            with open(config_path) as f:
                cfg = yaml.safe_load(f)

            effective_name = cfg.get("name", agent_name or "cli-agent")
            effective_model = cfg.get("model", model or "gpt-4o")
            effective_prompt = cfg.get(
                "system_prompt",
                system_prompt or "You are a helpful assistant.",
            )

            # Load MCP tools from config
            mcp_servers = cfg.get("mcp_tools") or cfg.get("tools") or []
            if mcp_servers:
                from sagewai.mcp.client import McpClient

                for server_cmd in mcp_servers:
                    try:
                        discovered = await McpClient.connect(server_cmd)
                        agent_tools.extend(discovered)
                        click.echo(
                            click.style("  Connected MCP: ", fg="cyan")
                            + f"{server_cmd} ({len(discovered)} tools)"
                        )
                    except Exception as exc:
                        click.echo(
                            click.style("  Warning: ", fg="yellow")
                            + f"could not connect to {server_cmd}: {exc}",
                            err=True,
                        )
        else:
            effective_name = agent_name or "cli-agent"
            effective_model = model or "gpt-4o"
            effective_prompt = (
                system_prompt or "You are a helpful assistant."
            )

        # Connect MCP tools from --tools flag
        if tools:
            from sagewai.mcp.client import McpClient

            for server_cmd in [
                t.strip() for t in tools.split(",") if t.strip()
            ]:
                try:
                    discovered = await McpClient.connect(server_cmd)
                    agent_tools.extend(discovered)
                    click.echo(
                        click.style("  Connected MCP: ", fg="cyan")
                        + f"{server_cmd} ({len(discovered)} tools)"
                    )
                except Exception as exc:
                    click.echo(
                        click.style("  Warning: ", fg="yellow")
                        + f"could not connect to {server_cmd}: {exc}",
                        err=True,
                    )

        agent = UniversalAgent(
            name=effective_name,
            model=effective_model,
            system_prompt=effective_prompt,
            tools=agent_tools or None,
        )

        click.echo(
            click.style("\nAgent: ", fg="cyan")
            + f"{effective_name} ({effective_model})"
        )
        if agent_tools:
            click.echo(
                click.style("Tools: ", fg="cyan")
                + f"{len(agent_tools)} available"
            )
        click.echo(
            click.style("Type ", dim=True)
            + click.style("exit", bold=True)
            + click.style(" or press Ctrl+C to quit.\n", dim=True)
        )

        while True:
            try:
                user_input = click.prompt(
                    click.style("you", fg="green", bold=True),
                    prompt_suffix="> ",
                )
            except (EOFError, click.Abort, KeyboardInterrupt):
                click.echo("\nGoodbye.")
                break

            if user_input.strip().lower() in ("exit", "quit"):
                click.echo("Goodbye.")
                break

            if not user_input.strip():
                continue

            try:
                if stream:
                    chunks: list[str] = []
                    click.echo()
                    async for chunk in agent.chat_stream(user_input):
                        click.echo(chunk, nl=False)
                        chunks.append(chunk)
                    click.echo("\n")
                else:
                    response = await agent.chat(user_input)
                    click.echo(f"\n{response}\n")
            except KeyboardInterrupt:
                click.echo("\n(interrupted)")
            except Exception as exc:
                click.echo(
                    click.style("\nError: ", fg="red") + str(exc) + "\n",
                    err=True,
                )

    _cli._run_async(_setup_and_run())


@run_group.command("api-list")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def run_api_list(as_json: bool) -> None:
    """List recent runs from the admin API."""
    data = _cli._api_get("/admin/runs")
    if as_json:
        _cli._echo_json(data)
        return
    rows = [
        {
            "run_id": r.get("run_id", "")[:12],
            "agent": r.get("agent_name", ""),
            "status": r.get("status", ""),
            "tokens": r.get("total_tokens", 0),
        }
        for r in data
    ]
    _cli._echo_table(rows, ["run_id", "agent", "status", "tokens"])


@run_group.command("api-show")
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def run_api_show(run_id: str, as_json: bool) -> None:
    """Show run detail from the admin API."""
    data = _cli._api_get(f"/admin/runs/{run_id}")
    if as_json:
        _cli._echo_json(data)
        return
    click.echo(f"Run: {data.get('run_id', run_id)}")
    click.echo(f"  Agent  : {data.get('agent_name', '—')}")
    click.echo(f"  Status : {data.get('status', '—')}")
    click.echo(f"  Tokens : {data.get('total_tokens', 0)}")
    if data.get("input_text"):
        click.echo(f"  Input  : {data['input_text'][:100]}")
    if data.get("output_text"):
        click.echo(f"  Output : {data['output_text'][:100]}")


@run_group.command("pause")
@click.argument("run_id")
def run_pause(run_id: str) -> None:
    """Pause a running run."""
    _cli._api_post(f"/admin/runs/{run_id}/pause")
    click.echo(f"Paused run {run_id}")


@run_group.command("resume")
@click.argument("run_id")
def run_resume(run_id: str) -> None:
    """Resume a paused run."""
    _cli._api_post(f"/admin/runs/{run_id}/resume")
    click.echo(f"Resumed run {run_id}")


@run_group.command("cancel")
@click.argument("run_id")
def run_cancel(run_id: str) -> None:
    """Cancel a run."""
    _cli._api_post(f"/admin/runs/{run_id}/cancel")
    click.echo(f"Cancelled run {run_id}")
