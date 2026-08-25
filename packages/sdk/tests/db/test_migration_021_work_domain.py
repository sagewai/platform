# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Migration 021 revision and portable Work-domain DDL tests."""

from __future__ import annotations

import importlib

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from sagewai.db.models import WorkEventModel, WorkItemModel


def test_migration_021_revision_chain() -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.021_work_domain")

    assert mod.revision == "021_work_domain"
    assert mod.down_revision == "020_fleet_task_lease"


def test_work_tables_compile_with_postgres_jsonb_mapping() -> None:
    dialect = postgresql.dialect()

    work_items = str(CreateTable(WorkItemModel.__table__).compile(dialect=dialect))
    work_events = str(CreateTable(WorkEventModel.__table__).compile(dialect=dialect))

    assert "profile_context JSONB NOT NULL" in work_items
    assert "payload_json JSONB NOT NULL" in work_events
    assert "UNIQUE (work_id, sequence)" in work_events


def test_migration_021_up_down_against_sqlite() -> None:
    mod = importlib.import_module("sagewai.db.migrations.versions.021_work_domain")
    engine = sa.create_engine("sqlite://")

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as conn:
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            mod.upgrade()
            inspector = sa.inspect(conn)
            assert {"work_items", "work_events"} <= set(inspector.get_table_names())
            assert {
                "work_id",
                "project_id",
                "source_ref",
                "profile",
                "status",
                "contract_version",
                "active_run_id",
                "pending_gate",
                "profile_context",
                "created_at",
                "updated_at",
            } == {column["name"] for column in inspector.get_columns("work_items")}
            assert {
                "id",
                "project_id",
                "work_id",
                "sequence",
                "event_type",
                "actor_type",
                "actor_ref",
                "payload_json",
                "created_at",
            } == {column["name"] for column in inspector.get_columns("work_events")}
            unique_columns = {
                tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints("work_events")
            }
            assert ("work_id", "sequence") in unique_columns

            mod.downgrade()
            assert {"work_items", "work_events"}.isdisjoint(sa.inspect(conn).get_table_names())
