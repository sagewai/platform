# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Strategy CLI commands — list and compare agent execution strategies."""

from __future__ import annotations

import click

from sagewai.cli._helpers import _api_get, _echo_json, _echo_table


@click.group()
def strategy() -> None:
    """Manage strategies — list and compare agent execution strategies.

    \b
    Examples:
      sagewai strategy list        List available strategies (ReAct, LATS, ToT, etc.)
    """


@strategy.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def strategy_list(as_json: bool) -> None:
    """List available agent strategies."""
    data = _api_get("/strategies/list")
    if as_json:
        _echo_json(data)
        return
    rows = [
        {
            "name": s.get("name", ""),
            "description": s.get("description", "")[:60],
        }
        for s in data
    ]
    _echo_table(rows, ["name", "description"])
