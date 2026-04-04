# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Eval CLI commands — run eval suites and generate reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

import sagewai.cli as _cli


@click.group("eval")
def eval_group() -> None:
    """Evaluation framework — run eval suites and generate reports.

    \b
    Examples:
      sagewai eval run -d evals.jsonl --agent-name MyAgent
      sagewai eval datasets                  List eval datasets
      sagewai eval runs                      List past eval runs
      sagewai eval report -r results.jsonl   Generate summary report
    """


@eval_group.command("run")
@click.option(
    "--dataset",
    "-d",
    required=True,
    type=click.Path(exists=True),
    help="JSONL eval dataset.",
)
@click.option(
    "--agent-name", required=True, help="Registered agent name to evaluate."
)
@click.option(
    "--judge-model", default="gpt-4o", help="Model for the LLM judge."
)
@click.option(
    "--output", "-o", default=None, help="Output JSONL path for results."
)
def eval_run(
    dataset: str, agent_name: str, judge_model: str, output: str | None
) -> None:
    """Run an evaluation suite against a registered agent."""
    from sagewai.core.registry import AgentRegistry
    from sagewai.eval.dataset import EvalDataset
    from sagewai.eval.judge import LLMJudge
    from sagewai.eval.suite import EvalSuite

    registry = AgentRegistry.get_instance()
    target = registry.get(agent_name)
    if target is None:
        click.echo(
            f"Error: agent '{agent_name}' not found in the registry.",
            err=True,
        )
        raise SystemExit(1)

    ds = EvalDataset.from_jsonl(dataset)
    judge = LLMJudge(model=judge_model)
    suite = EvalSuite(agent=target, dataset=ds, judge=judge)

    click.echo(
        f"Running {len(ds.cases)} eval case(s) against '{agent_name}'..."
    )
    results = _cli._run_async(suite.run())
    summary = results.summary()
    _cli._echo_json(summary)

    if output:
        results.to_jsonl(output)
        click.echo(f"\nResults written to {output}")


@eval_group.command("datasets")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def eval_datasets(as_json: bool) -> None:
    """List eval datasets from the admin API."""
    data = _cli._api_get("/api/v1/eval/datasets")
    if as_json:
        _cli._echo_json(data)
        return
    if not data:
        click.echo("No eval datasets found.")
        return
    rows = [
        {
            "id": str(d.get("id", "")),
            "name": d.get("name", ""),
            "cases": str(d.get("case_count", 0)),
            "created": (d.get("created_at") or "")[:10],
        }
        for d in data
    ]
    _cli._echo_table(rows, ["id", "name", "cases", "created"])


@eval_group.command("create")
@click.option("--name", required=True, help="Dataset name.")
@click.option(
    "--file",
    "-f",
    required=True,
    type=click.Path(exists=True),
    help="JSON file with cases array.",
)
@click.option(
    "--description", "-d", default=None, help="Optional description."
)
def eval_create(name: str, file: str, description: str | None) -> None:
    """Create an eval dataset from a JSON file."""
    cases = json.loads(Path(file).read_text())
    body: dict[str, Any] = {"name": name, "cases": cases}
    if description:
        body["description"] = description
    data = _cli._api_post("/api/v1/eval/datasets", body)
    click.echo(
        f"Created dataset '{data.get('name', name)}'"
        f" (id={data.get('id', '?')})"
    )


@eval_group.command("delete")
@click.argument("dataset_id", type=int)
def eval_delete(dataset_id: int) -> None:
    """Delete an eval dataset by ID."""
    _cli._api_delete(f"/api/v1/eval/datasets/{dataset_id}")
    click.echo(f"Deleted dataset {dataset_id}")


@eval_group.command("runs")
@click.option(
    "--dataset-id", type=int, default=None, help="Filter by dataset ID."
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def eval_runs(dataset_id: int | None, as_json: bool) -> None:
    """List past eval runs from the admin API."""
    qs = f"?dataset_id={dataset_id}" if dataset_id else ""
    data = _cli._api_get(f"/api/v1/eval/runs{qs}")
    if as_json:
        _cli._echo_json(data)
        return
    if not data:
        click.echo("No eval runs found.")
        return
    rows = [
        {
            "id": str(r.get("id", "")),
            "agent": r.get("agent_name", ""),
            "model": r.get("model", ""),
            "pass_rate": f"{r.get('pass_rate', 0) * 100:.0f}%",
            "cases": f"{r.get('passed', 0)}/{r.get('total_cases', 0)}",
        }
        for r in data
    ]
    _cli._echo_table(rows, ["id", "agent", "model", "pass_rate", "cases"])


@eval_group.command("report")
@click.option(
    "--results",
    "-r",
    required=True,
    type=click.Path(exists=True),
    help="JSONL results file.",
)
def eval_report(results: str) -> None:
    """Generate a summary report from a JSONL results file."""
    scores: list[dict[str, Any]] = []
    with open(results) as f:
        for line in f:
            line = line.strip()
            if line:
                scores.append(json.loads(line))

    total = len(scores)
    passed = sum(1 for s in scores if s.get("passed", False))
    avg_score = (
        sum(s.get("score", 0.0) for s in scores) / total if total else 0.0
    )

    click.echo("Evaluation Report")
    click.echo("=" * 40)
    click.echo(f"  Total cases : {total}")
    click.echo(f"  Passed      : {passed}")
    click.echo(f"  Failed      : {total - passed}")
    click.echo(
        f"  Pass rate   : {passed / total * 100:.1f}%"
        if total
        else "  Pass rate   : N/A"
    )
    click.echo(f"  Avg score   : {avg_score:.3f}")
