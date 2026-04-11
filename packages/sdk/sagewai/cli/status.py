# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Status CLI command — check infrastructure connectivity."""

from __future__ import annotations

import os
from typing import Any

import click

import sagewai.cli as _cli

# Default connection settings for infrastructure services
_INFRA_SERVICES: list[dict[str, Any]] = [
    {
        "name": "PostgreSQL",
        "env_host": "SAGEWAI_PG_HOST",
        "env_port": "SAGEWAI_PG_PORT",
        "default_host": "localhost",
        "default_port": 5432,
        "env_url": "SAGEWAI_DATABASE_URL",
        "env_url_alt": "DATABASE_URL",
    },
    {
        "name": "Redis",
        "env_host": "SAGEWAI_REDIS_HOST",
        "env_port": "SAGEWAI_REDIS_PORT",
        "default_host": "localhost",
        "default_port": 6379,
        "env_url": "REDIS_URL",
    },
    {
        "name": "Milvus",
        "env_host": "SAGEWAI_MILVUS_HOST",
        "env_port": "SAGEWAI_MILVUS_PORT",
        "default_host": "localhost",
        "default_port": 19530,
    },
    {
        "name": "NebulaGraph",
        "env_host": "SAGEWAI_NEBULA_HOST",
        "env_port": "SAGEWAI_NEBULA_PORT",
        "default_host": "localhost",
        "default_port": 9669,
    },
]


def _resolve_host_port(svc: dict[str, Any]) -> tuple[str, int]:
    """Resolve host and port for a service from env vars or defaults."""
    # Try URL-based env vars first (for Postgres, Redis)
    for url_key in [svc.get("env_url", ""), svc.get("env_url_alt", "")]:
        if url_key:
            url = os.environ.get(url_key, "")
            if url:
                # Parse host:port from URL
                from urllib.parse import urlparse

                parsed = urlparse(url)
                if parsed.hostname:
                    port = parsed.port or svc["default_port"]
                    return parsed.hostname, port

    host = os.environ.get(svc["env_host"], svc["default_host"])
    port_str = os.environ.get(svc["env_port"], "")
    port = int(port_str) if port_str else svc["default_port"]
    return host, port


def _check_tcp_connection(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a TCP connection can be established."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except (socket.error, OSError):
        return False


@click.command()
@click.option(
    "--service",
    default=None,
    help="Check a specific service (postgres, redis, milvus, nebula).",
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def status(service: str | None, as_json: bool) -> None:
    """Check connectivity to infrastructure services.

    \b
    Checks: PostgreSQL, Redis, Milvus, NebulaGraph.
    Reads connection info from environment variables or .env file.

    \b
    Examples:
      sagewai status
      sagewai status --service postgres
      sagewai status --json
    """
    # Try to load .env if python-dotenv is available
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    # Filter services if --service is specified
    service_filter = {
        "postgres": "PostgreSQL",
        "postgresql": "PostgreSQL",
        "pg": "PostgreSQL",
        "redis": "Redis",
        "milvus": "Milvus",
        "nebula": "NebulaGraph",
        "nebulagraph": "NebulaGraph",
    }

    if service:
        target_name = service_filter.get(service.lower())
        if not target_name:
            click.echo(
                f"Unknown service: {service}. "
                f"Available: "
                f"{', '.join(sorted(set(service_filter.values())))}",
                err=True,
            )
            raise SystemExit(1)
        services = [s for s in _INFRA_SERVICES if s["name"] == target_name]
    else:
        services = _INFRA_SERVICES

    # Look up _check_tcp_connection via package so tests can patch it
    check_tcp = _cli._check_tcp_connection

    results: list[dict[str, Any]] = []
    for svc in services:
        host, port = _resolve_host_port(svc)
        connected = check_tcp(host, port)
        results.append(
            {
                "service": svc["name"],
                "host": host,
                "port": port,
                "status": "connected" if connected else "unreachable",
            }
        )

    if as_json:
        _cli._echo_json(results)
        return

    # Calculate column widths for alignment
    max_svc = max(len(r["service"]) for r in results)
    max_addr = max(len(f"{r['host']}:{r['port']}") for r in results)

    click.echo()
    click.echo(
        click.style("  SERVICE", bold=True).ljust(max_svc + 12)
        + click.style("ADDRESS", bold=True).ljust(max_addr + 4)
        + click.style("STATUS", bold=True)
    )
    click.echo("  " + "-" * (max_svc + max_addr + 20))

    for r in results:
        svc_name = r["service"].ljust(max_svc + 2)
        addr = f"{r['host']}:{r['port']}".ljust(max_addr + 2)
        if r["status"] == "connected":
            status_str = click.style("connected", fg="green")
        else:
            status_str = click.style("unreachable", fg="red")
        click.echo(f"  {svc_name}  {addr}  {status_str}")

    click.echo()

    # Summary line
    total = len(results)
    up = sum(1 for r in results if r["status"] == "connected")
    if up == total:
        click.echo(
            click.style(f"  All {total} services connected.", fg="green")
        )
    elif up == 0:
        click.echo(
            click.style(f"  All {total} services unreachable.", fg="red")
        )
    else:
        click.echo(
            click.style(f"  {up}/{total} services connected.", fg="yellow")
        )
    click.echo()
