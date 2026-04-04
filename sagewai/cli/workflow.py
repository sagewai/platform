# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Workflow CLI commands — run, resume, list, and inspect YAML workflows."""

from __future__ import annotations

import click

import sagewai.cli as _cli


@click.group()
def workflow() -> None:
    """Manage workflows — run, resume, list, and inspect YAML-defined workflows.

    \b
    Examples:
      sagewai workflow run -f pipeline.yaml -i "topic: AI"
      sagewai workflow resume -f pipeline.yaml --run-id abc123 -i "topic: AI"
      sagewai workflow list --status completed
      sagewai workflow history --limit 10
    """


@workflow.command("run")
@click.option(
    "--file",
    "-f",
    required=True,
    type=click.Path(exists=True),
    help="YAML workflow.",
)
@click.option("--input", "-i", "input_text", required=True, help="Input message.")
@click.option(
    "--run-id", default=None, help="Explicit run ID (for resumption)."
)
def workflow_run(file: str, input_text: str, run_id: str | None) -> None:
    """Execute a YAML workflow file."""
    from sagewai.core.yaml_workflow import load_workflow

    wf_agent = load_workflow(file)
    click.echo(f"Running workflow '{wf_agent.config.name}'...")
    result = _cli._run_async(wf_agent.chat(input_text))
    click.echo(result)


@workflow.command("resume")
@click.option(
    "--file",
    "-f",
    required=True,
    type=click.Path(exists=True),
    help="YAML workflow.",
)
@click.option("--run-id", required=True, help="Run ID to resume.")
@click.option(
    "--input", "-i", "input_text", required=True, help="Original input message."
)
def workflow_resume(file: str, run_id: str, input_text: str) -> None:
    """Resume a previously checkpointed workflow run."""
    from sagewai.core.durability import DurableRunner
    from sagewai.core.state import InMemoryStore
    from sagewai.core.yaml_workflow import load_workflow

    wf_agent = load_workflow(file)
    runner = DurableRunner(store=InMemoryStore())
    click.echo(
        f"Resuming workflow '{wf_agent.config.name}' run_id={run_id}..."
    )

    # For sequential workflows we can directly use the runner
    agents = getattr(wf_agent, "_agents", [wf_agent])
    result = _cli._run_async(
        runner.run_sequential(
            agents=agents, input_text=input_text, run_id=run_id
        )
    )
    click.echo(result)


@workflow.command("list-templates")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def workflow_list_templates(as_json: bool) -> None:
    """List available workflow templates from the admin API."""
    data = _cli._api_get("/workflows/templates")
    if as_json:
        _cli._echo_json(data)
        return
    rows = [
        {
            "name": t.get("name", ""),
            "description": t.get("description", "")[:60],
        }
        for t in data
    ]
    _cli._echo_table(rows, ["name", "description"])


@workflow.command("validate")
@click.option(
    "--yaml",
    "yaml_str",
    required=True,
    help="YAML workflow definition string.",
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def workflow_validate(yaml_str: str, as_json: bool) -> None:
    """Validate a YAML workflow via the admin API."""
    data = _cli._api_post("/workflows/validate", {"yaml": yaml_str})
    if as_json:
        _cli._echo_json(data)
        return
    if data.get("valid"):
        click.echo(f"Valid workflow: {data.get('name', '—')}")
        agents = data.get("agents", [])
        if agents:
            click.echo(
                f"  Agents: {', '.join(a.get('name', '') for a in agents)}"
            )
    else:
        click.echo(f"Invalid: {data.get('error', 'unknown error')}")


@workflow.command("history")
@click.option("--limit", default=50, type=int, help="Maximum runs to show.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def workflow_history(limit: int, as_json: bool) -> None:
    """List past workflow runs from the admin API."""
    data = _cli._api_get(f"/workflows/history?limit={limit}")
    if as_json:
        _cli._echo_json(data)
        return
    if not data:
        click.echo("No workflow runs found.")
        return
    rows = [
        {
            "workflow": r.get("workflow_name", ""),
            "run_id": r.get("run_id", "")[:12],
            "status": r.get("status", ""),
            "created": r.get("created_at", "")[:19],
        }
        for r in data
    ]
    _cli._echo_table(rows, ["workflow", "run_id", "status", "created"])
