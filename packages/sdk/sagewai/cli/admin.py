# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Admin CLI commands — status, runs, costs, serve, and health."""

# NOTE: do NOT add `from __future__ import annotations` to this file.
# PEP 563 stringifies all annotations, which prevents FastAPI from
# recognising `request: Request` at runtime. FastAPI needs the live
# type object to inject the Starlette request.

import click

import sagewai.cli as _cli


@click.group()
def admin() -> None:
    """Admin operations — status, runs, costs, and serving the admin API.

    \b
    Examples:
      sagewai admin status        Show registry and run counts
      sagewai admin runs          List recent agent runs
      sagewai admin costs         Show cost analytics
      sagewai admin serve         Start the admin API server
      sagewai admin health        Show system health from admin API
    """


@admin.command("status")
def admin_status() -> None:
    """Show the current admin status (registry, runs, sessions)."""
    from sagewai.admin.state import AdminState
    from sagewai.core.registry import AgentRegistry

    registry = AgentRegistry.get_instance()
    agents = registry.list_agents()
    state = AdminState()

    click.echo("Sagewai Admin Status")
    click.echo("=" * 40)
    click.echo(f"  Registered agents : {len(agents)}")
    click.echo(f"  Total runs        : {state.total_runs}")
    click.echo(f"  Active sessions   : {state.active_sessions}")
    click.echo(f"  SDK version       : {_cli.VERSION}")


@admin.command("runs")
@click.option(
    "--agent", "agent_name", default=None, help="Filter by agent name."
)
@click.option("--limit", default=20, help="Maximum runs to show.")
def admin_runs(agent_name: str | None, limit: int) -> None:
    """List recent agent runs from the admin state."""
    from sagewai.admin.state import AdminState

    state = AdminState()
    runs = state.list_runs(agent_name=agent_name, limit=limit)
    if not runs:
        click.echo("No runs recorded yet.")
        return

    rows = [
        {
            "run_id": r.run_id,
            "agent": r.agent_name,
            "status": r.status,
            "tokens": r.total_tokens,
        }
        for r in runs
    ]
    _cli._echo_table(rows, ["run_id", "agent", "status", "tokens"])


@admin.command("costs")
@click.option(
    "--agent", "agent_name", default=None, help="Filter by agent name."
)
def admin_costs(agent_name: str | None) -> None:
    """Show cost analytics from the AnalyticsStore."""
    from sagewai.admin.analytics import AnalyticsStore

    store = AnalyticsStore()
    costs = store.get_costs(agent_name=agent_name)
    _cli._echo_json(costs)


@admin.command("serve")
@click.option("--host", default="0.0.0.0", help="Host to bind.")
@click.option("--port", default=8000, type=int, help="Port to bind.")
def admin_serve(host: str, port: int) -> None:
    """Start the admin API server (FastAPI + uvicorn)."""
    try:
        import uvicorn

        from sagewai.admin.serve import create_admin_serve_app
        from sagewai.admin.state_file import AdminStateFile

        sf = AdminStateFile()
        app = create_admin_serve_app(sf, version=_cli.VERSION)

        click.echo(f"Starting Sagewai Admin API on {host}:{port}")
        uvicorn.run(app, host=host, port=port)
    except ImportError as exc:
        click.echo(
            f"Error: missing dependency for admin serve: {exc}. "
            "Install with: uv add 'sagewai[fastapi]'",
            err=True,
        )
        raise SystemExit(1)


@admin.command("health")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def admin_health(as_json: bool) -> None:
    """Show system health status from the admin API."""
    data = _cli._api_get("/api/v1/health/detailed")
    if as_json:
        _cli._echo_json(data)
        return
    status = data.get("status", "unknown")
    click.echo(f"System Status: {status.upper()}")
    click.echo(f"SDK Version  : {data.get('sdk_version', '—')}")
    click.echo(f"Checked      : {data.get('checked_at', '—')}")
    click.echo()
    for svc in data.get("services", []):
        latency = (
            f" ({svc['latency_ms']:.1f}ms)"
            if svc.get("latency_ms")
            else ""
        )
        detail = f" — {svc['detail']}" if svc.get("detail") else ""
        click.echo(
            f"  {svc['name']:20s} {svc['status']}{latency}{detail}"
        )
