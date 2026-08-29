# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 026: explicitly scoped durable workflow identities."""

from __future__ import annotations

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _module():
    return importlib.import_module("sagewai.db.migrations.versions.026_workflow_run_project_scope")


def _create_legacy_table(connection: sa.Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE workflow_runs ("
        "id TEXT PRIMARY KEY, "
        "project_id TEXT NOT NULL DEFAULT 'default', "
        "workflow_name TEXT NOT NULL, "
        "run_id TEXT NOT NULL, "
        "data JSON NOT NULL DEFAULT '{}', "
        "idempotency_key TEXT)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE workflow_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "project_id TEXT NOT NULL DEFAULT 'default', "
        "run_id TEXT NOT NULL, "
        "event_type TEXT NOT NULL, "
        "data JSON NOT NULL DEFAULT '{}', "
        "created_at TEXT)"
    )


def _run_with_operations(connection: sa.Connection, operation: str) -> None:
    mod = _module()
    original_op = mod.op
    mod.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(mod, operation)()
    finally:
        mod.op = original_op


def test_migration_026_revision_chain() -> None:
    mod = _module()

    assert mod.revision == "026_workflow_run_project_scope"
    assert mod.down_revision == "025_assumption_project_scope"
    assert callable(mod.upgrade) and callable(mod.downgrade)


def test_postgres_upgrade_uses_jsonb_presence_and_null_semantics(
    monkeypatch,
) -> None:
    mod = _module()
    executed: list[str] = []

    class Batch:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def alter_column(self, *_args, **_kwargs):
            return None

    class NoRows:
        def first(self):
            return None

    class RecordingOp:
        def get_bind(self):
            return type(
                "Bind",
                (),
                {
                    "dialect": type("Dialect", (), {"name": "postgresql"})(),
                    "execute": lambda _self, _statement: NoRows(),
                },
            )()

        def execute(self, statement) -> None:
            executed.append(str(statement))

        def batch_alter_table(self, _table_name: str):
            return Batch()

    monkeypatch.setattr(mod, "op", RecordingOp())
    mod.upgrade()

    assert "SET project_id = data ->> 'project_id'" in executed[0]
    assert "WHERE data ? 'project_id'" in executed[0]


def test_sqlite_upgrade_rewrites_ids_and_allows_global_scope() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _create_legacy_table(connection)
        connection.exec_driver_sql(
            "INSERT INTO workflow_runs "
            "(id, project_id, workflow_name, run_id, idempotency_key) VALUES "
            "('legacy-a', 'alpha', 'wf', 'same', 'dedupe'), "
            "('legacy-b', 'beta', 'wf', 'same', NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO workflow_events (project_id, run_id, event_type) "
            "VALUES ('alpha', 'event-only', 'STARTED')"
        )

        _run_with_operations(connection, "upgrade")

        rows = connection.exec_driver_sql(
            "SELECT id, project_id, idempotency_key " "FROM workflow_runs ORDER BY project_id"
        ).all()
        assert rows == [
            ("7:p:alpha2:wfsame", "alpha", "7:p:alphadedupe"),
            ("6:p:beta2:wfsame", "beta", None),
        ]
        for table_name in ("workflow_runs", "workflow_events"):
            project_column = next(
                column
                for column in sa.inspect(connection).get_columns(table_name)
                if column["name"] == "project_id"
            )
            assert project_column["nullable"] is True
            assert project_column["default"] is None

        connection.exec_driver_sql(
            "INSERT INTO workflow_runs "
            "(id, project_id, workflow_name, run_id) "
            "VALUES ('2:g:2:wfsame', NULL, 'wf', 'same')"
        )
        connection.exec_driver_sql(
            "INSERT INTO workflow_events (project_id, run_id, event_type) "
            "VALUES (NULL, 'same', 'GLOBAL')"
        )


def test_sqlite_upgrade_reconciles_scope_from_serialized_run() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _create_legacy_table(connection)
        connection.exec_driver_sql(
            "INSERT INTO workflow_runs "
            "(id, project_id, workflow_name, run_id, data) VALUES "
            "('project', 'default', 'wf', 'project', "
            "json_object('project_id', 'alpha')), "
            "('global', 'default', 'wf', 'global', "
            "json_object('project_id', NULL)), "
            "('absent', 'kept', 'wf', 'absent', "
            "json_object('other', 1))"
        )

        connection.exec_driver_sql(
            "INSERT INTO workflow_events (project_id, run_id, event_type) VALUES "
            "('default', 'project', 'PROJECT'), "
            "('default', 'global', 'GLOBAL'), "
            "('default', 'absent', 'ABSENT'), "
            "('preserved', 'unmatched', 'UNMATCHED')"
        )

        _run_with_operations(connection, "upgrade")

        rows = connection.exec_driver_sql(
            "SELECT run_id, id, project_id, "
            "json_extract(data, '$.project_id'), "
            "json_type(data, '$.project_id') "
            "FROM workflow_runs ORDER BY run_id"
        ).all()
        assert rows == [
            ("absent", "6:p:kept2:wfabsent", "kept", None, None),
            ("global", "2:g:2:wfglobal", None, None, "null"),
            ("project", "7:p:alpha2:wfproject", "alpha", "alpha", "text"),
        ]

        event_rows = connection.exec_driver_sql(
            "SELECT run_id, project_id FROM workflow_events ORDER BY run_id"
        ).all()
        assert event_rows == [
            ("absent", "kept"),
            ("global", None),
            ("project", "alpha"),
            ("unmatched", "preserved"),
        ]


def test_sqlite_upgrade_refuses_ambiguous_event_scope() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _create_legacy_table(connection)
        connection.exec_driver_sql(
            "INSERT INTO workflow_runs "
            "(id, project_id, workflow_name, run_id, data) VALUES "
            "('a', 'default', 'one', 'shared', "
            "json_object('project_id', 'alpha')), "
            "('b', 'default', 'two', 'shared', "
            "json_object('project_id', 'beta'))"
        )
        connection.exec_driver_sql(
            "INSERT INTO workflow_events (project_id, run_id, event_type) "
            "VALUES ('default', 'shared', 'STARTED')"
        )

        with pytest.raises(RuntimeError, match="ambiguous run project scope"):
            _run_with_operations(connection, "upgrade")


def test_sqlite_downgrade_refuses_global_and_colliding_data() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _create_legacy_table(connection)
        connection.exec_driver_sql(
            "INSERT INTO workflow_runs "
            "(id, project_id, workflow_name, run_id, idempotency_key) VALUES "
            "('legacy-a', 'alpha', 'wf', 'same', 'dedupe'), "
            "('legacy-b', 'beta', 'wf', 'same', 'other')"
        )
        _run_with_operations(connection, "upgrade")

        with pytest.raises(RuntimeError, match="cross-project IDs collide"):
            _run_with_operations(connection, "downgrade")

        connection.exec_driver_sql(
            "UPDATE workflow_runs SET run_id = 'different', "
            "id = '6:p:beta2:wfdifferent', "
            "idempotency_key = '6:p:betadedupe' "
            "WHERE project_id = 'beta'"
        )
        with pytest.raises(RuntimeError, match="idempotency keys collide"):
            _run_with_operations(connection, "downgrade")

        connection.exec_driver_sql("DELETE FROM workflow_runs WHERE project_id = 'beta'")
        connection.exec_driver_sql(
            "INSERT INTO workflow_events (project_id, run_id, event_type) "
            "VALUES (NULL, 'global', 'GLOBAL')"
        )
        with pytest.raises(RuntimeError, match="global runs or events exist"):
            _run_with_operations(connection, "downgrade")

        connection.exec_driver_sql("DELETE FROM workflow_events WHERE project_id IS NULL")
        _run_with_operations(connection, "downgrade")

        assert connection.exec_driver_sql(
            "SELECT id, project_id, idempotency_key FROM workflow_runs"
        ).one() == ("wf:same", "alpha", "dedupe")
        for table_name in ("workflow_runs", "workflow_events"):
            project_column = next(
                column
                for column in sa.inspect(connection).get_columns(table_name)
                if column["name"] == "project_id"
            )
            assert project_column["nullable"] is False
            assert "default" in str(project_column["default"])
