# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 021: revision-chain guard + Postgres-gated up/down round-trip."""

from __future__ import annotations

import importlib
import os

import pytest
from alembic import command as alembic_command
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from sagewai.cli.db import build_alembic_config
from sagewai.db.models import WorkEventModel, WorkItemModel

DB_URL = os.environ.get(
    "SAGEWAI_DATABASE_URL",
    "postgresql+asyncpg://sagewai:sagewai@localhost:5432/sagewai",
)


def test_migration_021_revision_chain() -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.021_work_domain")

    assert mod.revision == "021_work_domain"
    assert mod.down_revision == "020_fleet_task_lease"
    assert callable(mod.upgrade) and callable(mod.downgrade)


def test_work_tables_compile_with_postgres_jsonb_mapping() -> None:
    dialect = postgresql.dialect()

    work_items = str(CreateTable(WorkItemModel.__table__).compile(dialect=dialect))
    work_events = str(CreateTable(WorkEventModel.__table__).compile(dialect=dialect))

    assert "profile_context JSONB NOT NULL" in work_items
    assert "payload_json JSONB NOT NULL" in work_events
    assert "UNIQUE (project_scope_key, work_id, sequence)" in work_events


@pytest.mark.integration
class TestMigration021RoundTrip:
    def _cfg(self):
        return build_alembic_config(DB_URL)

    def test_up_down_up(self):
        alembic_command.upgrade(self._cfg(), "021_work_domain")
        alembic_command.downgrade(self._cfg(), "020_fleet_task_lease")
        alembic_command.upgrade(self._cfg(), "021_work_domain")
