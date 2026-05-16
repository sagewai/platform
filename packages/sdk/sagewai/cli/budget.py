# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Budget CLI commands — set spending limits and monitor costs per agent."""

from __future__ import annotations

from typing import Any

import click

from sagewai.cli._helpers import (
    _api_delete,
    _api_get,
    _api_post,
    _echo_json,
    _echo_table,
)


@click.group()
def budget() -> None:
    """Manage budgets — set spending limits and monitor costs per agent.

    \b
    Examples:
      sagewai budget api-list                        List all budget limits
      sagewai budget set MyAgent --daily 5.00        Set daily limit
      sagewai budget status MyAgent                  Show current spend vs limits
      sagewai budget remove MyAgent                  Remove budget limit
    """


@budget.command("api-list")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def budget_api_list(as_json: bool) -> None:
    """List budget limits."""
    data = _api_get("/api/v1/budget/limits")
    if as_json:
        _echo_json(data)
        return
    rows = [
        {
            "agent": lim.get("agent_name", ""),
            "daily": lim.get("daily_limit_usd", "—"),
            "monthly": lim.get("monthly_limit_usd", "—"),
        }
        for lim in data
    ]
    _echo_table(rows, ["agent", "daily", "monthly"])


@budget.command("set")
@click.argument("agent_name")
@click.option("--daily", type=float, default=None, help="Daily limit in USD.")
@click.option(
    "--monthly", type=float, default=None, help="Monthly limit in USD."
)
def budget_set(
    agent_name: str, daily: float | None, monthly: float | None
) -> None:
    """Set a budget limit for an agent."""
    body: dict[str, Any] = {"agent_name": agent_name}
    if daily is not None:
        body["daily_limit_usd"] = daily
    if monthly is not None:
        body["monthly_limit_usd"] = monthly
    _api_post("/api/v1/budget/limits", body)
    click.echo(f"Budget set for {agent_name}")


@budget.command("remove")
@click.argument("agent_name")
def budget_remove(agent_name: str) -> None:
    """Remove a budget limit for an agent."""
    _api_delete(f"/api/v1/budget/limits/{agent_name}")
    click.echo(f"Budget removed for {agent_name}")


@budget.command("status")
@click.argument("agent_name")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def budget_status(agent_name: str, as_json: bool) -> None:
    """Show budget status for an agent."""
    data = _api_get(f"/api/v1/budget/status/{agent_name}")
    if as_json:
        _echo_json(data)
        return
    click.echo(f"Budget status for {data.get('agent_name', agent_name)}:")
    click.echo(f"  Daily spend  : ${data.get('daily_spend', 0):.2f}")
    click.echo(f"  Monthly spend: ${data.get('monthly_spend', 0):.2f}")
    if data.get("daily_limit_usd"):
        click.echo(f"  Daily limit  : ${data['daily_limit_usd']:.2f}")
    if data.get("monthly_limit_usd"):
        click.echo(f"  Monthly limit: ${data['monthly_limit_usd']:.2f}")
