# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Safety CLI commands — guardrails configuration, audit, and export."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from sagewai.cli._helpers import (
    ADMIN_URL,
    _api_get,
    _api_put,
    _echo_json,
    _echo_table,
)


@click.group()
def safety() -> None:
    """Manage guardrails — configure, audit, and export safety events.

    \b
    Examples:
      sagewai safety guardrails --agent MyAgent      List guardrail configs
      sagewai safety set MyAgent --pii --no-hallucination
      sagewai safety audit --limit 50                Show recent safety events
      sagewai safety export --format csv -o events.csv
    """


@safety.command("guardrails")
@click.option(
    "--agent", "agent_name", default=None, help="Filter by agent name."
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def safety_guardrails(agent_name: str | None, as_json: bool) -> None:
    """List guardrail configs for all or a specific agent."""
    qs = f"?agent_name={agent_name}" if agent_name else ""
    data = _api_get(f"/api/v1/guardrails/configs{qs}")
    if as_json:
        _echo_json(data)
        return
    if not data:
        click.echo("No guardrail configs found.")
        return
    rows = [
        {
            "agent": c.get("agent_name", ""),
            "type": c.get("guardrail_type", ""),
            "enabled": "yes" if c.get("enabled") else "no",
        }
        for c in data
    ]
    _echo_table(rows, ["agent", "type", "enabled"])


@safety.command("set")
@click.argument("agent_name")
@click.option("--pii/--no-pii", default=None, help="Enable/disable PIIGuard.")
@click.option(
    "--hallucination/--no-hallucination",
    default=None,
    help="Enable/disable HallucinationGuard.",
)
@click.option(
    "--content-filter/--no-content-filter",
    default=None,
    help="Enable/disable ContentFilter.",
)
def safety_set(
    agent_name: str,
    pii: bool | None,
    hallucination: bool | None,
    content_filter: bool | None,
) -> None:
    """Set guardrail configs for an agent."""
    changes = []
    if pii is not None:
        changes.append(("pii", pii))
    if hallucination is not None:
        changes.append(("hallucination", hallucination))
    if content_filter is not None:
        changes.append(("content_filter", content_filter))

    if not changes:
        click.echo(
            "No guardrail flags specified. "
            "Use --pii, --hallucination, or --content-filter."
        )
        return

    for gtype, enabled in changes:
        _api_put(
            f"/api/v1/guardrails/configs/{agent_name}",
            {"guardrail_type": gtype, "enabled": enabled},
        )
        status = "enabled" if enabled else "disabled"
        click.echo(f"  {gtype}: {status}")

    click.echo(f"Guardrails updated for {agent_name}")


@safety.command("audit")
@click.option(
    "--agent", "agent_name", default=None, help="Filter by agent name."
)
@click.option(
    "--type", "event_type", default=None, help="Filter by event type."
)
@click.option("--limit", default=20, help="Max events to show.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def safety_audit(
    agent_name: str | None,
    event_type: str | None,
    limit: int,
    as_json: bool,
) -> None:
    """Show guardrail audit log."""
    params: list[str] = [f"limit={limit}"]
    if agent_name:
        params.append(f"agent_name={agent_name}")
    if event_type:
        params.append(f"event_type={event_type}")
    qs = "?" + "&".join(params)

    data = _api_get(f"/api/v1/audit/events{qs}")
    if as_json:
        _echo_json(data)
        return

    events = data.get("events", [])
    total = data.get("total", 0)
    click.echo(f"Showing {len(events)} of {total} events")

    if not events:
        click.echo("No events found.")
        return

    rows = [
        {
            "id": str(e.get("id", "")),
            "agent": e.get("agent_name", ""),
            "type": e.get("event_type", ""),
            "detail": (e.get("detail") or "")[:50],
            "created_at": (e.get("created_at") or "")[:19],
        }
        for e in events
    ]
    _echo_table(rows, ["id", "agent", "type", "detail", "created_at"])


@safety.command("export")
@click.option(
    "--agent", "agent_name", default=None, help="Filter by agent name."
)
@click.option(
    "--type", "event_type", default=None, help="Filter by event type."
)
@click.option(
    "--format",
    "fmt",
    default="json",
    type=click.Choice(["json", "csv"]),
    help="Export format.",
)
@click.option("--output", "-o", default=None, help="Output file path.")
def safety_export(
    agent_name: str | None,
    event_type: str | None,
    fmt: str,
    output: str | None,
) -> None:
    """Export guardrail events as JSON or CSV."""
    import httpx

    params: list[str] = [f"format={fmt}"]
    if agent_name:
        params.append(f"agent_name={agent_name}")
    if event_type:
        params.append(f"event_type={event_type}")
    qs = "?" + "&".join(params)

    resp = httpx.get(f"{ADMIN_URL}/api/v1/audit/export{qs}", timeout=30)
    resp.raise_for_status()

    content = resp.text
    if output:
        Path(output).write_text(content)
        click.echo(f"Exported to {output}")
    else:
        click.echo(content)
