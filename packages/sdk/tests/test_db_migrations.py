# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for Alembic migrations.

Two layers, one canonical entrypoint:

- **Unit** (default suite, no database): the migrations directory the CLI feeds
  Alembic actually exists, and the revision chain is well-formed and reaches a
  single head. This guards against the regression where ``sagewai db upgrade``
  pointed at a non-existent ``cli/db/migrations`` directory.
- **Integration** (``@pytest.mark.integration``, needs PostgreSQL): apply the
  full ``001_initial`` -> ``017_api_tokens`` chain against a live database using
  the *same* programmatic ``build_alembic_config`` the ``sagewai db upgrade``
  CLI uses. There is deliberately no ``alembic.ini`` — code is the single source
  of truth for ``script_location`` and the URL.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from alembic import command as alembic_command
from alembic.script import ScriptDirectory

from sagewai.cli.db import build_alembic_config, migrations_dir

DB_URL = os.environ.get(
    "SAGEWAI_DATABASE_URL",
    "postgresql+asyncpg://sagewai:sagewai@localhost:5432/sagewai",
)


# --- Unit: migration layout & chain (no database required) ------------------


def test_migrations_dir_exists() -> None:
    """The directory the CLI hands Alembic must actually exist on disk.

    Regression guard: ``sagewai db upgrade`` once resolved ``cli/db/migrations``
    (one parent too shallow) instead of ``db/migrations``, so every migration
    silently failed against a live database.
    """
    d = migrations_dir()
    assert d.is_dir(), f"migrations directory not found: {d}"
    assert (d / "env.py").is_file()
    assert (d / "script.py.mako").is_file()
    assert (d / "versions").is_dir()


def test_migration_chain_has_single_base_and_head() -> None:
    """Every migration on disk is reachable on one chain to exactly one head."""
    script = ScriptDirectory.from_config(build_alembic_config(DB_URL))

    head = script.get_current_head()  # raises CommandError if multiple heads
    assert head is not None, "no head revision resolved — wrong script_location?"

    revs = list(script.walk_revisions())
    bases = sorted(r.revision for r in revs if r.down_revision is None)
    assert bases == ["001_initial"], f"expected single base 001_initial, got {bases}"

    # No orphans: the reachable chain equals the migration files on disk.
    on_disk = {p.stem for p in (migrations_dir() / "versions").glob("[0-9]*.py")}
    reachable = {r.revision for r in revs}
    assert on_disk == reachable, (
        f"migrations on disk and reachable chain disagree: {on_disk ^ reachable}"
    )


# --- Integration: apply migrations to a live PostgreSQL ---------------------


def _alembic(subcommand: str, revision: str) -> None:
    """Drive Alembic through the same programmatic config as ``sagewai db upgrade``."""
    getattr(alembic_command, subcommand)(build_alembic_config(DB_URL), revision)


async def _db_revision() -> str | None:
    """Read the revision Alembic recorded in the live database."""
    import asyncpg

    dsn = DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT version_num FROM alembic_version")
    finally:
        await conn.close()


@pytest.mark.integration
class TestMigrationRoundTrip:
    def test_upgrade_to_head(self) -> None:
        _alembic("upgrade", "head")

    def test_downgrade_to_base(self) -> None:
        _alembic("downgrade", "base")

    def test_full_round_trip(self) -> None:
        """downgrade base -> upgrade head -> downgrade base -> upgrade head."""
        for subcommand, revision in [
            ("downgrade", "base"),
            ("upgrade", "head"),
            ("downgrade", "base"),
            ("upgrade", "head"),
        ]:
            _alembic(subcommand, revision)

    def test_db_revision_matches_script_head(self) -> None:
        _alembic("upgrade", "head")
        script_head = ScriptDirectory.from_config(
            build_alembic_config(DB_URL)
        ).get_current_head()
        assert asyncio.run(_db_revision()) == script_head
