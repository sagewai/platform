# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 019: revision-chain guard + Postgres-gated up/down round-trip."""
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


def test_migration_019_revision_chain():
    mod = importlib.import_module("sagewai.db.migrations.versions.019_fleet_tasks")
    assert mod.revision == "019_fleet_tasks"
    assert mod.down_revision == "018_fleet_schema_correction"
    assert callable(mod.upgrade) and callable(mod.downgrade)


@pytest.mark.integration
class TestMigration019RoundTrip:
    def _cfg(self):
        return build_alembic_config(DB_URL)

    def test_up_down_up(self):
        alembic_command.upgrade(self._cfg(), "019_fleet_tasks")
        alembic_command.downgrade(self._cfg(), "018_fleet_schema_correction")
        alembic_command.upgrade(self._cfg(), "019_fleet_tasks")
