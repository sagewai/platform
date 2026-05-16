# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
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


@workflow.command("replay")
@click.argument("run_id")
@click.option(
    "--from-step", "from_step", type=int, default=0,
    help="Step index (0-based) to replay from.",
)
@click.option(
    "--yes", is_flag=True, help="Skip confirmation prompt.",
)
def workflow_replay(run_id: str, from_step: int, yes: bool) -> None:
    """Replay a workflow run from a given step.

    Steps before --from-step are reused from the original run.
    Steps from --from-step onward re-execute against the original
    Sealed-iii.C injection snapshot.
    """
    base = f"/api/v1/admin/workflows/runs/{run_id}/replay"

    if not yes:
        preview = _cli._api_post(f"{base}/preview", {"from_step": from_step})
        blockers = preview.get("blockers") or []
        warnings = preview.get("warnings") or []
        if blockers:
            click.echo("Cannot replay — blockers:")
            for b in blockers:
                click.echo(f"  • {b.get('type', 'unknown')}")
            raise click.Abort()
        if warnings:
            click.echo("Warnings:")
            for w in warnings:
                key = w.get("secret_key")
                tag = f": {key}" if key else ""
                click.echo(f"  ⚠ {w.get('type', 'unknown')}{tag}")
            click.confirm("Proceed?", abort=True)

    body = _cli._api_post(
        base, {"from_step": from_step, "confirm_warnings": True},
    )
    click.echo(f"✓ Created replay run {body['new_run_id']}")


@workflow.command("replay-status")
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def workflow_replay_status(run_id: str, as_json: bool) -> None:
    """List replays of a run."""
    body = _cli._api_get(f"/api/v1/admin/workflows/runs/{run_id}/replays")
    if as_json:
        _cli._echo_json(body)
        return
    rows = [
        {
            "run_id": r.get("run_id", "")[:16],
            "from_step": r.get("replay_from_step"),
            "status": r.get("status", ""),
            "started_at": r.get("started_at", ""),
        }
        for r in body.get("replays", [])
    ]
    if not rows:
        click.echo("No replays of this run.")
        return
    _cli._echo_table(rows, ["run_id", "from_step", "status", "started_at"])
