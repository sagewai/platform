# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 024: deterministic project-scope persistence identities."""

from __future__ import annotations

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from sagewai.db.models import (
    KnowledgeItemModel,
    KnowledgeSourceRefModel,
    WorkEventModel,
    WorkItemModel,
)


def test_migration_024_revision_chain() -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.024_work_project_scope")

    assert mod.revision == "024_work_project_scope"
    assert mod.down_revision == "023_knowledge_source_refs"
    assert callable(mod.upgrade) and callable(mod.downgrade)


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_scoped_models_compile_with_non_null_composite_identities(dialect) -> None:
    work_items = str(CreateTable(WorkItemModel.__table__).compile(dialect=dialect))
    work_events = str(CreateTable(WorkEventModel.__table__).compile(dialect=dialect))
    knowledge_items = str(CreateTable(KnowledgeItemModel.__table__).compile(dialect=dialect))
    source_refs = str(CreateTable(KnowledgeSourceRefModel.__table__).compile(dialect=dialect))

    assert "project_scope_key" in work_items
    assert "PRIMARY KEY (project_scope_key, work_id)" in work_items
    assert "project_scope_key" in work_events
    assert "PRIMARY KEY (project_scope_key, id)" in work_events
    assert "UNIQUE (project_scope_key, work_id, sequence)" in work_events
    assert "project_id TEXT" in knowledge_items
    assert "project_id TEXT NOT NULL" not in knowledge_items
    assert "PRIMARY KEY (project_scope_key, id)" in knowledge_items
    assert "PRIMARY KEY (project_scope_key, knowledge_item_id, source_ref)" in source_refs
    assert (
        "FOREIGN KEY(project_scope_key, knowledge_item_id) "
        "REFERENCES knowledge_items (project_scope_key, id) ON DELETE CASCADE"
        in source_refs
    )


def _create_legacy_schema(connection: sa.Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE work_items ("
        "work_id TEXT PRIMARY KEY, project_id TEXT, source_ref TEXT, profile TEXT NOT NULL, "
        "status TEXT NOT NULL, contract_version INTEGER, active_run_id TEXT, pending_gate TEXT, "
        "profile_context JSON NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_work_items_project_id ON work_items (project_id)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE work_events ("
        "id TEXT PRIMARY KEY, project_id TEXT, work_id TEXT NOT NULL, sequence INTEGER NOT NULL, "
        "event_type TEXT NOT NULL, actor_type TEXT NOT NULL, actor_ref TEXT, payload_json JSON NOT NULL, "
        "created_at DATETIME NOT NULL, CONSTRAINT uq_work_events_work_sequence "
        "UNIQUE (work_id, sequence))"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_work_events_project_work_sequence "
        "ON work_events (project_id, work_id, sequence)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE knowledge_items ("
        "id TEXT PRIMARY KEY, project_id TEXT NOT NULL, work_id TEXT, kind TEXT NOT NULL, "
        "statement TEXT NOT NULL, source_refs JSON NOT NULL, artifact_refs JSON NOT NULL, "
        "factness_score INTEGER NOT NULL DEFAULT 0, importance_score INTEGER NOT NULL DEFAULT 50, "
        "created_by TEXT NOT NULL, created_at DATETIME NOT NULL, supersedes TEXT, "
        "CONSTRAINT ck_knowledge_items_factness_score CHECK (factness_score IN (0, 100)))"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_knowledge_items_project_work_created_at "
        "ON knowledge_items (project_id, work_id, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE knowledge_source_refs ("
        "knowledge_item_id TEXT NOT NULL, project_id TEXT NOT NULL, source_ref TEXT NOT NULL, "
        "CONSTRAINT pk_knowledge_source_refs PRIMARY KEY (knowledge_item_id, source_ref), "
        "FOREIGN KEY(knowledge_item_id) REFERENCES knowledge_items (id) ON DELETE CASCADE)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_knowledge_source_refs_project_ref_item "
        "ON knowledge_source_refs (project_id, source_ref, knowledge_item_id)"
    )
    connection.exec_driver_sql(
        "CREATE VIRTUAL TABLE knowledge_items_fts USING fts5(item_id UNINDEXED, statement)"
    )


def _run_migration(connection: sa.Connection, direction: str) -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.024_work_project_scope")
    original_op = mod.op
    mod.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(mod, direction)()
    finally:
        mod.op = original_op


def _seed_legacy_rows(connection: sa.Connection) -> None:
    connection.exec_driver_sql(
        "INSERT INTO work_items VALUES "
        "('global-work', NULL, NULL, 'software', 'open', NULL, NULL, NULL, '{}', "
        "'2026-01-01', '2026-01-01'), "
        "('project-work', 'alpha', NULL, 'software', 'open', NULL, NULL, NULL, '{}', "
        "'2026-01-01', '2026-01-01')"
    )
    connection.exec_driver_sql(
        "INSERT INTO work_events VALUES "
        "('global-event', NULL, 'global-work', 1, 'WORK_CREATED', 'system', NULL, '{}', '2026-01-01'), "
        "('project-event', 'alpha', 'project-work', 1, 'WORK_CREATED', 'system', NULL, '{}', '2026-01-01')"
    )
    connection.exec_driver_sql(
        "INSERT INTO knowledge_items VALUES "
        "('knowledge-1', 'alpha', 'project-work', 'fact', 'scoped fact', '[\"source-1\"]', '[]', "
        "100, 50, 'test', '2026-01-01', NULL)"
    )
    connection.exec_driver_sql(
        "INSERT INTO knowledge_source_refs VALUES ('knowledge-1', 'alpha', 'source-1')"
    )
    connection.exec_driver_sql(
        "INSERT INTO knowledge_items_fts VALUES ('knowledge-1', 'scoped fact')"
    )


def test_sqlite_upgrade_backfills_scope_and_preserves_rows() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        _create_legacy_schema(connection)
        _seed_legacy_rows(connection)

        _run_migration(connection, "upgrade")

        work_rows = connection.exec_driver_sql(
            "SELECT work_id, project_id, project_scope_key FROM work_items ORDER BY work_id"
        ).all()
        assert work_rows == [
            ("global-work", None, "g:"),
            ("project-work", "alpha", "p:alpha"),
        ]
        assert connection.exec_driver_sql(
            "SELECT project_scope_key FROM work_events WHERE id = 'global-event'"
        ).scalar_one() == "g:"
        assert connection.exec_driver_sql(
            "SELECT project_scope_key FROM knowledge_items WHERE id = 'knowledge-1'"
        ).scalar_one() == "p:alpha"
        assert connection.exec_driver_sql(
            "SELECT project_scope_key FROM knowledge_source_refs"
        ).scalar_one() == "p:alpha"

        fts_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(knowledge_items_fts)")
        }
        assert fts_columns == {"project_scope_key", "item_id", "statement"}
        assert connection.exec_driver_sql(
            "SELECT project_scope_key, item_id FROM knowledge_items_fts"
        ).one() == ("p:alpha", "knowledge-1")

        connection.exec_driver_sql(
            "INSERT INTO work_items "
            "(project_scope_key, work_id, project_id, profile, status, profile_context, created_at, updated_at) "
            "VALUES ('p:alpha', 'same', 'alpha', 'software', 'open', '{}', '2026-01-01', '2026-01-01'), "
            "('p:beta', 'same', 'beta', 'software', 'open', '{}', '2026-01-01', '2026-01-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO knowledge_items "
            "(project_scope_key, id, project_id, kind, statement, source_refs, artifact_refs, "
            "factness_score, importance_score, created_by, created_at) VALUES "
            "('g:', 'global-knowledge', NULL, 'fact', 'global fact', '[]', '[]', 100, 50, 'test', '2026-01-01')"
        )

        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_sqlite_downgrade_refuses_lossy_legacy_identity_without_mutation() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _create_legacy_schema(connection)
        _seed_legacy_rows(connection)
        _run_migration(connection, "upgrade")
        connection.exec_driver_sql(
            "INSERT INTO work_items "
            "(project_scope_key, work_id, project_id, profile, status, profile_context, created_at, updated_at) "
            "VALUES ('p:beta', 'project-work', 'beta', 'software', 'open', '{}', '2026-01-01', '2026-01-01')"
        )

        with pytest.raises(RuntimeError, match="legacy unscoped identities"):
            _run_migration(connection, "downgrade")

        assert "project_scope_key" in {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(work_items)")
        }
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM work_items WHERE work_id = 'project-work'"
        ).scalar_one() == 2


def test_sqlite_safe_downgrade_restores_legacy_schema_and_data() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _create_legacy_schema(connection)
        _seed_legacy_rows(connection)
        _run_migration(connection, "upgrade")

        _run_migration(connection, "downgrade")

        assert "project_scope_key" not in {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(work_items)")
        }
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM work_items").scalar_one() == 2
        assert connection.exec_driver_sql(
            "SELECT statement FROM knowledge_items WHERE id = 'knowledge-1'"
        ).scalar_one() == "scoped fact"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
