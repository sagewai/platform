# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 022: chain guard and PostgreSQL-compatible knowledge schema."""

from __future__ import annotations

import importlib
import os

import pytest
from alembic import command as alembic_command
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from sagewai.cli.db import build_alembic_config
from sagewai.db.models import KnowledgeItemModel

DB_URL = os.environ.get(
    "SAGEWAI_DATABASE_URL",
    "postgresql+asyncpg://sagewai:sagewai@localhost:5432/sagewai",
)


def test_migration_022_revision_chain() -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.022_shared_evidence_board")

    assert mod.revision == "022_shared_evidence_board"
    assert mod.down_revision == "021_work_domain"
    assert callable(mod.upgrade) and callable(mod.downgrade)


def test_knowledge_table_compiles_with_postgres_mapping() -> None:
    ddl = str(CreateTable(KnowledgeItemModel.__table__).compile(dialect=postgresql.dialect()))

    assert "source_refs JSONB NOT NULL" in ddl
    assert "artifact_refs JSONB NOT NULL" in ddl
    assert "factness_score INTEGER" in ddl
    assert "ck_knowledge_items_factness_score" in ddl


@pytest.mark.integration
class TestMigration022RoundTrip:
    def _cfg(self):
        return build_alembic_config(DB_URL)

    def test_up_down_up(self):
        alembic_command.upgrade(self._cfg(), "022_shared_evidence_board")
        alembic_command.downgrade(self._cfg(), "021_work_domain")
        alembic_command.upgrade(self._cfg(), "022_shared_evidence_board")
