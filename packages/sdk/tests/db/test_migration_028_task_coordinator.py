# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 028: Task coordinator tables."""

from __future__ import annotations

import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from sagewai.db.models import (
    TaskCommandModel,
    TaskDefaultsModel,
    TaskEventModel,
    TaskFeedModel,
    TaskModel,
    TaskRepositoryLeaseModel,
    TaskSpendModel,
    TaskTriggerModel,
    WorkActivityModel,
)

TASK_COORDINATOR_MODELS = (
    TaskModel,
    TaskEventModel,
    TaskFeedModel,
    TaskCommandModel,
    TaskSpendModel,
    TaskRepositoryLeaseModel,
    TaskDefaultsModel,
    TaskTriggerModel,
    WorkActivityModel,
)


def test_migration_028_revision_chain() -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.028_task_coordinator")
    assert mod.revision == "028_task_coordinator"
    assert mod.down_revision == "027_fleet_task_project_scope"
    assert callable(mod.upgrade) and callable(mod.downgrade)


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_task_tables_are_project_scoped(dialect) -> None:
    def ddl(model) -> str:
        return str(CreateTable(model.__table__).compile(dialect=dialect))

    tasks = ddl(TaskModel)
    assert "PRIMARY KEY (project_scope_key, task_id)" in tasks
    assert "lease_epoch" in tasks and "revision" in tasks
    assert "last_event_sequence" in tasks
    events = ddl(TaskEventModel)
    assert "PRIMARY KEY (project_scope_key, id)" in events
    assert "UNIQUE (project_scope_key, task_id, sequence)" in events
    feed = ddl(TaskFeedModel)
    assert "PRIMARY KEY (project_scope_key, task_id, feed_sequence)" in feed
    assert "PRIMARY KEY (project_scope_key, task_id, command_id)" in ddl(TaskCommandModel)
    assert "PRIMARY KEY (project_scope_key, reservation_id)" in ddl(TaskSpendModel)
    assert "PRIMARY KEY (project_scope_key, lease_key)" in ddl(TaskRepositoryLeaseModel)
    assert "PRIMARY KEY (project_scope_key)" in ddl(TaskDefaultsModel)
    assert "PRIMARY KEY (project_scope_key, trigger_id)" in ddl(TaskTriggerModel)
    assert "PRIMARY KEY (project_scope_key, work_id, run_id, sequence)" in ddl(WorkActivityModel)


def test_migration_028_creates_and_drops_every_table() -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.028_task_coordinator")
    assert set(mod.TABLES) == {
        "tasks", "task_events", "task_feed", "task_commands", "task_spend",
        "task_repository_leases", "task_defaults", "task_triggers", "work_activity",
    }


def _schema_signature(bind, table_names: tuple[str, ...]) -> dict[str, dict[str, list[tuple]]]:
    inspector = inspect(bind)
    signature: dict[str, dict[str, list[tuple]]] = {}
    for table_name in table_names:
        signature[table_name] = {
            "columns": [
                (
                    column["name"],
                    str(column["type"]),
                    bool(column["nullable"]),
                    int(column["primary_key"]),
                )
                for column in inspector.get_columns(table_name)
            ],
            "indexes": sorted(
                (
                    index["name"],
                    tuple(index["column_names"]),
                    bool(index.get("unique")),
                )
                for index in inspector.get_indexes(table_name)
            ),
            "unique_constraints": sorted(
                (
                    constraint["name"],
                    tuple(constraint["column_names"]),
                )
                for constraint in inspector.get_unique_constraints(table_name)
            ),
        }
    return signature


def _migration_signature(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, list[tuple]]]:
    mod = importlib.import_module("sagewai.db.migrations.versions.028_task_coordinator")
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            context = MigrationContext.configure(conn)
            monkeypatch.setattr(mod, "op", Operations(context))
            mod.upgrade()
            return _schema_signature(conn, mod.TABLES)
    finally:
        engine.dispose()


def _metadata_signature(table_names: tuple[str, ...]) -> dict[str, dict[str, list[tuple]]]:
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            TaskModel.metadata.create_all(conn, tables=[model.__table__ for model in TASK_COORDINATOR_MODELS])
            return _schema_signature(conn, table_names)
    finally:
        engine.dispose()


def test_migration_028_schema_matches_orm_create_all(monkeypatch: pytest.MonkeyPatch) -> None:
    table_names = tuple(model.__table__.name for model in TASK_COORDINATOR_MODELS)
    assert _migration_signature(monkeypatch) == _metadata_signature(table_names)
