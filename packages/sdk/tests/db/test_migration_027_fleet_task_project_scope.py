# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 027: explicitly scoped durable Fleet task identity."""

from __future__ import annotations

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _module():
    return importlib.import_module(
        "sagewai.db.migrations.versions.027_fleet_task_project_scope"
    )


def _run(connection: sa.Connection, operation: str) -> None:
    module = _module()
    original_op = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, operation)()
    finally:
        module.op = original_op


def _create_legacy_table(connection: sa.Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE fleet_tasks ("
        "run_id TEXT PRIMARY KEY, org_id TEXT NOT NULL, project_id TEXT, "
        "pool TEXT NOT NULL DEFAULT 'default', model TEXT, labels JSON NOT NULL, "
        "payload JSON NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
        "worker_id TEXT, claimed_at TIMESTAMP, output TEXT, error TEXT, "
        "reported_at TIMESTAMP, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "lease_expires_at TIMESTAMP, attempts INTEGER NOT NULL DEFAULT 0)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_fleet_tasks_claim ON fleet_tasks "
        "(status, org_id, project_id, pool, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_fleet_tasks_scope ON fleet_tasks (org_id, project_id, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_fleet_tasks_lease ON fleet_tasks (status, lease_expires_at)"
    )


def test_migration_027_revision_chain() -> None:
    module = _module()
    assert module.revision == "027_fleet_task_project_scope"
    assert module.down_revision == "026_workflow_run_project_scope"


def test_sqlite_upgrade_preserves_rows_and_allows_project_local_run_ids() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _create_legacy_table(connection)
        connection.exec_driver_sql(
            "INSERT INTO fleet_tasks "
            "(run_id, org_id, project_id, labels, payload) "
            "VALUES ('same', 'org', 'alpha', '{}', '{\"kept\": true}')"
        )

        _run(connection, "upgrade")

        inspector = sa.inspect(connection)
        assert inspector.get_pk_constraint("fleet_tasks")["constrained_columns"] == [
            "org_id", "project_scope_key", "run_id"
        ]
        assert connection.exec_driver_sql(
            "SELECT project_scope_key, json_extract(payload, '$.kept') FROM fleet_tasks"
        ).one() == ("p:alpha", 1)
        connection.exec_driver_sql(
            "INSERT INTO fleet_tasks "
            "(run_id, org_id, project_id, project_scope_key, labels, payload) VALUES "
            "('same', 'org', 'beta', 'p:beta', '{}', '{}'), "
            "('same', 'org', NULL, 'g:', '{}', '{}')"
        )
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM fleet_tasks WHERE run_id = 'same'"
        ).scalar_one() == 3
        with pytest.raises(RuntimeError, match="cross-scope run IDs collide"):
            _run(connection, "downgrade")
