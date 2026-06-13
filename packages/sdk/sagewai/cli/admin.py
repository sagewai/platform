# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
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


@admin.command("reset-password")
@click.option(
    "--email",
    default=None,
    help="Admin email to reset (defaults to the single configured admin).",
)
@click.option(
    "--password",
    default=None,
    help="New password. Omit to be prompted securely (keeps it out of shell history).",
)
def admin_reset_password(email, password) -> None:
    """Reset the admin password locally — recover from a lockout without email/SMTP.

    \b
    On a host install:        sagewai admin reset-password
    Inside a Docker stack:    docker compose exec backend sagewai admin reset-password

    Rewrites the admin password in the state file and revokes existing sessions;
    then sign in at /login with the new password. (Single-org; multi-tenant
    accounts live in the identity store, not the state file.)
    """
    from sagewai.admin.state_file import AdminStateFile, default_admin_state_path

    sf = AdminStateFile(default_admin_state_path())
    if not sf.is_setup_complete():
        click.echo(
            "No admin account exists yet — open the setup wizard at /setup instead.",
            err=True,
        )
        raise SystemExit(1)
    if password is None:
        password = click.prompt("New password", hide_input=True, confirmation_prompt=True)
    if len(password) < 8:
        click.echo("Password must be at least 8 characters.", err=True)
        raise SystemExit(1)
    if sf.reset_admin_password(password, email=email):
        click.echo("Admin password reset. Sign in at /login with the new password.")
    else:
        suffix = f" for {email!r}." if email else "."
        click.echo(f"No matching admin account found{suffix}", err=True)
        raise SystemExit(1)


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
@click.option("--host", default="127.0.0.1", help="Host to bind (default: loopback).")
@click.option("--port", default=8000, type=int, help="Port to bind.")
def admin_serve(host: str, port: int) -> None:
    """Start the admin API server (FastAPI + uvicorn)."""
    try:
        import uvicorn

        from sagewai import home
        from sagewai.admin.serve import create_admin_serve_app
        from sagewai.admin.state_file import AdminStateFile

        if host not in {"127.0.0.1", "localhost", "::1"}:
            click.echo(
                f"WARNING: binding to {host} exposes the admin API beyond localhost. "
                "Ensure auth, TLS, and SAGEWAI_ADMIN_ALLOWED_ORIGINS are configured.",
                err=True,
            )

        home.migrate_home()
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


@admin.group("project")
def admin_project() -> None:
    """Manage project-level settings (sandbox defaults, etc.).

    \b
    Examples:
      sagewai admin project set-sandbox-defaults acme \\
          --mode per_run \\
          --image ghcr.io/sagewai/sandbox-general:0.1.0 \\
          --network-policy full
    """


@admin_project.command("set-sandbox-defaults")
@click.argument("project_id")
@click.option(
    "--mode",
    type=click.Choice(["none", "per_tool", "per_run", "per_worker"]),
    required=True,
    help="Default sandbox isolation mode for this project.",
)
@click.option(
    "--image",
    required=True,
    help="Default image reference for this project.",
)
@click.option(
    "--network-policy",
    type=click.Choice(["none", "egress_allowlist", "full"]),
    required=True,
    help="Default network policy for this project.",
)
def admin_project_set_sandbox_defaults(
    project_id: str, mode: str, image: str, network_policy: str
) -> None:
    """Write default_sandbox_requirements for a project to admin-state.json."""
    from sagewai.admin.state_file import AdminStateFile

    state = AdminStateFile()
    data = state._read()
    projects = data.setdefault("projects", [])

    if isinstance(projects, dict):
        target = projects.setdefault(project_id, {})
    else:
        # list-of-dicts shape (existing AdminStateFile schema)
        target = next(
            (p for p in projects if p.get("slug") == project_id or p.get("id") == project_id),
            None,
        )
        if target is None:
            target = {"slug": project_id}
            projects.append(target)

    target["default_sandbox_requirements"] = {
        "sandbox_mode": mode,
        "image": image,
        "network_policy": network_policy,
    }
    state._write(data)
    click.echo(
        f"Set sandbox defaults for project '{project_id}': "
        f"mode={mode} image={image} network_policy={network_policy}"
    )


from sagewai.cli.sealed import sealed_group  # noqa: E402

admin.add_command(sealed_group)

from sagewai.cli.profiles import profiles_group  # noqa: E402

admin.add_command(profiles_group)

from sagewai.cli.directives import directives_group  # noqa: E402

admin.add_command(directives_group)


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
