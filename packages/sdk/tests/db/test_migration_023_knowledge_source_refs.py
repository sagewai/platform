# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 023: indexed source-reference navigation over canonical knowledge."""

from __future__ import annotations

import importlib
import os

import pytest
from alembic import command as alembic_command
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from sagewai.cli.db import build_alembic_config
from sagewai.db.models import KnowledgeSourceRefModel

DB_URL = os.environ.get(
    "SAGEWAI_DATABASE_URL",
    "postgresql+asyncpg://sagewai:sagewai@localhost:5432/sagewai",
)


def test_migration_023_revision_chain() -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.023_knowledge_source_refs")

    assert mod.revision == "023_knowledge_source_refs"
    assert mod.down_revision == "022_shared_evidence_board"
    assert callable(mod.upgrade) and callable(mod.downgrade)


def test_source_ref_navigation_table_and_index_compile_for_both_dialects() -> None:
    table = KnowledgeSourceRefModel.__table__
    postgres_ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    sqlite_ddl = str(CreateTable(table).compile(dialect=sqlite.dialect()))
    index = next(
        item for item in table.indexes if item.name == "ix_knowledge_source_refs_project_ref_item"
    )

    for ddl in (postgres_ddl, sqlite_ddl):
        assert "knowledge_item_id" in ddl
        assert "project_id" in ddl
        assert "source_ref" in ddl
        assert "PRIMARY KEY (knowledge_item_id, source_ref)" in ddl
        assert "FOREIGN KEY(knowledge_item_id) REFERENCES knowledge_items (id)" in ddl
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        index_ddl = str(CreateIndex(index).compile(dialect=dialect))
        assert "(project_id, source_ref, knowledge_item_id)" in index_ddl


def test_migration_backfills_canonical_json_source_refs(monkeypatch) -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.023_knowledge_source_refs")
    executed: list[str] = []

    class RecordingOp:
        def create_table(self, *_args, **_kwargs) -> None:
            return None

        def create_index(self, *_args, **_kwargs) -> None:
            return None

        def execute(self, statement) -> None:
            executed.append(str(statement))

    monkeypatch.setattr(mod, "op", RecordingOp())

    mod.upgrade()

    assert len(executed) == 1
    assert "jsonb_array_elements_text(knowledge.source_refs)" in executed[0]
    assert "SELECT DISTINCT" in executed[0]


@pytest.mark.integration
class TestMigration023RoundTrip:
    def _cfg(self):
        return build_alembic_config(DB_URL)

    def test_up_down_up(self):
        alembic_command.upgrade(self._cfg(), "023_knowledge_source_refs")
        alembic_command.downgrade(self._cfg(), "022_shared_evidence_board")
        alembic_command.upgrade(self._cfg(), "023_knowledge_source_refs")
