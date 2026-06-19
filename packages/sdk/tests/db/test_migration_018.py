# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 018: revision-chain unit guard + a real Postgres up/down round-trip
that proves the org-global (project_id IS NULL) backfill on downgrade."""
from __future__ import annotations

import importlib
import os

import pytest
from alembic import command as alembic_command

from sagewai.cli.db import build_alembic_config

DB_URL = os.environ.get(
    "SAGEWAI_DATABASE_URL",
    "postgresql+asyncpg://sagewai:sagewai@localhost:5432/sagewai",
)


def test_migration_018_revision_chain():
    mod = importlib.import_module(
        "sagewai.db.migrations.versions.018_fleet_schema_correction"
    )
    assert mod.revision == "018_fleet_schema_correction"
    assert mod.down_revision == "017_api_tokens"
    assert callable(mod.upgrade) and callable(mod.downgrade)


@pytest.mark.integration
class TestMigration018RoundTrip:
    """Real Alembic up/down against Postgres (skipped when offline)."""

    def _cfg(self):
        return build_alembic_config(DB_URL)

    def _exec(self, sql: str):
        import asyncio

        import asyncpg

        async def _run():
            conn = await asyncpg.connect(DB_URL.replace("postgresql+asyncpg://", "postgresql://"))
            try:
                return await conn.fetch(sql)
            finally:
                await conn.close()

        return asyncio.run(_run())

    def test_null_project_downgrade_backfill(self):
        # Start clean at head (018 applied → project_id nullable).
        alembic_command.upgrade(self._cfg(), "018_fleet_schema_correction")
        # Insert an org-global worker row with project_id = NULL.
        self._exec(
            "INSERT INTO workers (worker_id, project_id, status) "
            "VALUES ('mig018-test', NULL, 'fleet')"
        )
        # Downgrade past 018 → the backfill must turn NULL into 'default' before
        # the NOT NULL restore, so this must not raise.
        alembic_command.downgrade(self._cfg(), "017_api_tokens")
        rows = self._exec("SELECT project_id FROM workers WHERE worker_id = 'mig018-test'")
        assert rows and rows[0]["project_id"] == "default"
        # Upgrade back to head and clean up.
        alembic_command.upgrade(self._cfg(), "018_fleet_schema_correction")
        self._exec("DELETE FROM workers WHERE worker_id = 'mig018-test'")
