# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""CLI commands for Sagewai Autopilot."""

from __future__ import annotations

import json
import sys

import click
import httpx


def _api_url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{port}{path}"


def _headers(token: str | None = None) -> dict[str, str]:
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


@click.group("autopilot")
def autopilot_group() -> None:
    """Manage Sagewai Autopilot — goal-driven agent missions."""


@autopilot_group.command("status")
@click.option("--host", default="localhost", help="Admin server host")
@click.option("--port", default=8765, type=int, help="Admin server port")
@click.option("--token", default=None, help="Auth token")
def status(host: str, port: int, token: str | None) -> None:
    """Show autopilot status."""
    try:
        resp = httpx.get(
            _api_url(host, port, "/api/v1/autopilot/status"),
            headers=_headers(token),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        click.echo(json.dumps(data, indent=2))
    except httpx.HTTPError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@autopilot_group.command("enable")
@click.option("--tier", default="anonymous", help="Tier: anonymous, free, skip")
@click.option("--host", default="localhost")
@click.option("--port", default=8765, type=int)
@click.option("--token", default=None)
def enable(tier: str, host: str, port: int, token: str | None) -> None:
    """Enable autopilot with a tier."""
    try:
        resp = httpx.post(
            _api_url(host, port, "/api/v1/autopilot/enable"),
            json={"tier": tier},
            headers=_headers(token),
            timeout=10,
        )
        resp.raise_for_status()
        click.echo(f"Autopilot enabled (tier={tier})")
    except httpx.HTTPError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@autopilot_group.command("disable")
@click.option("--host", default="localhost")
@click.option("--port", default=8765, type=int)
@click.option("--token", default=None)
def disable(host: str, port: int, token: str | None) -> None:
    """Disable autopilot."""
    try:
        resp = httpx.post(
            _api_url(host, port, "/api/v1/autopilot/disable"),
            headers=_headers(token),
            timeout=10,
        )
        resp.raise_for_status()
        click.echo("Autopilot disabled")
    except httpx.HTTPError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@autopilot_group.command("goal")
@click.argument("goal_text")
@click.option("--host", default="localhost")
@click.option("--port", default=8765, type=int)
@click.option("--token", default=None)
@click.option("--project", default=None, help="Project ID")
def goal(goal_text: str, host: str, port: int, token: str | None, project: str | None) -> None:
    """Submit a goal to autopilot and see the routing result."""
    hdrs = _headers(token)
    if project:
        hdrs["X-Project-ID"] = project
    try:
        resp = httpx.post(
            _api_url(host, port, "/api/v1/autopilot/goal"),
            json={"goal": goal_text},
            headers=hdrs,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        kind = data.get("kind", "unknown")
        click.echo(f"Routing result: {kind}")
        if kind == "auto_routed":
            click.echo(f"Preview:\n{data.get('preview', '')}")
            click.echo("\nRun 'sagewai autopilot approve' to schedule this mission.")
        elif kind == "picker_needed":
            candidates = data.get("candidates", [])
            click.echo(f"Top {len(candidates)} candidates — pick one:")
            for i, c in enumerate(candidates):
                bp = json.loads(c.get("blueprint_json", "{}"))
                click.echo(f"  [{i}] {bp.get('title', '?')} (score: {c.get('score', '?')})")
        elif kind == "synthesis_needed":
            click.echo("No matching blueprint. The service will generate one.")
        click.echo(json.dumps(data, indent=2))
    except httpx.HTTPError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@autopilot_group.command("missions")
@click.option("--host", default="localhost")
@click.option("--port", default=8765, type=int)
@click.option("--token", default=None)
@click.option("--project", default=None)
def missions(host: str, port: int, token: str | None, project: str | None) -> None:
    """List autopilot missions."""
    hdrs = _headers(token)
    if project:
        hdrs["X-Project-ID"] = project
    try:
        resp = httpx.get(
            _api_url(host, port, "/api/v1/autopilot/missions"),
            headers=hdrs,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            click.echo("No missions yet.")
            return
        for m in data:
            click.echo(
                f"  {m.get('mission_id', '?')[:16]}  "
                f"{m.get('status', '?'):12s}  "
                f"{m.get('blueprint_id', '?')}"
            )
    except httpx.HTTPError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
