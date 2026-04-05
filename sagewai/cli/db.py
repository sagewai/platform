# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""DB CLI commands — database migrations via Alembic."""

from __future__ import annotations

import os
from pathlib import Path

import click


@click.group()
def db() -> None:
    """Database migrations and stats via Alembic.

    \b
    Examples:
      sagewai db upgrade               Apply pending migrations
      sagewai db downgrade --revision base
      sagewai db stats                 Show queue and workflow stats
    """


@db.command("upgrade")
@click.option(
    "--revision", default="head", help="Target revision (default: head)."
)
def db_upgrade(revision: str) -> None:
    """Run Alembic upgrade to the target revision."""
    _run_alembic(["upgrade", revision])


@db.command("downgrade")
@click.option(
    "--revision", required=True, help="Target revision to downgrade to."
)
def db_downgrade(revision: str) -> None:
    """Run Alembic downgrade to the target revision."""
    _run_alembic(["downgrade", revision])


def _run_alembic(args: list[str]) -> None:
    """Execute an Alembic command using the SDK's migrations directory."""
    try:
        from alembic import command as alembic_command
        from alembic.config import Config as AlembicConfig
    except ImportError:
        click.echo(
            "Error: alembic not installed. "
            "Install with: uv add 'sagewai[postgres]'",
            err=True,
        )
        raise SystemExit(1)

    migrations_dir = Path(__file__).resolve().parent / "db" / "migrations"
    if not migrations_dir.exists():
        click.echo(
            f"Error: migrations directory not found at {migrations_dir}",
            err=True,
        )
        raise SystemExit(1)

    db_url = os.environ.get("SAGEWAI_DATABASE_URL", "")
    if not db_url:
        click.echo(
            "Error: SAGEWAI_DATABASE_URL not set. "
            "Export the variable or add it to .env.",
            err=True,
        )
        raise SystemExit(1)

    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(migrations_dir))
    cfg.set_main_option("sqlalchemy.url", db_url)

    subcmd = args[0]
    revision = args[1] if len(args) > 1 else "head"
    click.echo(f"Running alembic {subcmd} {revision}...")

    if subcmd == "upgrade":
        alembic_command.upgrade(cfg, revision)
    elif subcmd == "downgrade":
        alembic_command.downgrade(cfg, revision)
    else:
        click.echo(f"Unknown alembic subcommand: {subcmd}", err=True)
        raise SystemExit(1)

    click.echo("Done.")
