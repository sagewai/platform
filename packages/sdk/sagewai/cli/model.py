# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Model CLI commands — list models, view routing rules, and test routing."""

from __future__ import annotations

import click

import sagewai.cli as _cli


@click.group()
def model() -> None:
    """Manage model routing — list models, view rules, and test routing.

    \b
    Examples:
      sagewai model list                    List available LLM models
      sagewai model rules                   Show routing rules
      sagewai model test "Summarize this"   Test which model gets selected
    """


@model.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def model_list(as_json: bool) -> None:
    """List available LLM models."""
    data = _cli._api_get("/api/v1/model-router/models")
    if as_json:
        _cli._echo_json(data)
        return
    for m in data:
        click.echo(f"  {m}")


@model.command("rules")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def model_rules(as_json: bool) -> None:
    """List model routing rules."""
    data = _cli._api_get("/api/v1/model-router/rules")
    if as_json:
        _cli._echo_json(data)
        return
    if not data:
        click.echo("No routing rules configured.")
        return
    rows = [
        {
            "name": r.get("name", ""),
            "target": r.get("target_model", ""),
            "condition": r.get("condition", ""),
        }
        for r in data
    ]
    _cli._echo_table(rows, ["name", "target", "condition"])


@model.command("test")
@click.argument("query")
@click.option(
    "--default", "default_model", default="gpt-4o", help="Default model."
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def model_test(query: str, default_model: str, as_json: bool) -> None:
    """Test model routing for a query."""
    data = _cli._api_post(
        "/api/v1/model-router/test",
        {"query": query, "context": {}, "default_model": default_model},
    )
    if as_json:
        _cli._echo_json(data)
        return
    click.echo(f"Query    : {data.get('query', query)}")
    click.echo(f"Selected : {data.get('selected_model', '—')}")
    click.echo(f"Default  : {data.get('default_model', default_model)}")
