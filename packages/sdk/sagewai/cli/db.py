# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""DB CLI commands — database migrations via Alembic."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from alembic.config import Config as AlembicConfig


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


def migrations_dir() -> Path:
    """Absolute path to the Alembic migrations shipped with the SDK.

    They live at ``sagewai/db/migrations`` — a sibling of this ``cli`` package —
    which is why we step up two parents from ``cli/db.py`` (``cli`` -> ``sagewai``)
    before descending into ``db/migrations``.
    """
    return Path(__file__).resolve().parent.parent / "db" / "migrations"


def build_alembic_config(db_url: str) -> AlembicConfig:
    """Build the programmatic Alembic config — the single migration entrypoint.

    ``sagewai db upgrade`` and the migration tests both route through here, so
    there is deliberately no ``alembic.ini`` on disk: ``script_location`` and
    ``sqlalchemy.url`` are set in code. ``env.py`` still reads
    ``SAGEWAI_DATABASE_URL`` from the environment at run time.
    """
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(migrations_dir()))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _run_alembic(args: list[str]) -> None:
    """Execute an Alembic command using the SDK's migrations directory."""
    try:
        from alembic import command as alembic_command
    except ImportError:
        click.echo(
            "Error: alembic not installed. "
            "Install with: uv add 'sagewai[postgres]'",
            err=True,
        )
        raise SystemExit(1)

    mig_dir = migrations_dir()
    if not mig_dir.exists():
        click.echo(
            f"Error: migrations directory not found at {mig_dir}",
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

    cfg = build_alembic_config(db_url)

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
